/**
 * HU-010-D: the resident's demo payment, in its two controllers.
 *
 * The obligations (demo-payment.ts): amounts (exact text, compared in cents), the list, the form, the
 * review and its projected balance, the start and the navigation to the checkout of the transaction
 * (also for one already in progress, and from the legacy ?transaction= URL).
 *
 * The checkout (demo-checkout.ts): the transaction read from the server (also after a reload), its
 * summary, the catalog of test instruments (read from the API), the choice and the processing with its
 * id only, the processing view, the results and reasons the server answers, a new transaction to try
 * another instrument, the cancellation, the expiry, the bounded polling, the answers out of order and
 * every error.
 *
 * The API, the clock, the timers and the navigation are fakes injected into the controllers (no server);
 * the fake catalog is the backend's own (services/demo_instruments.py), never an invented instrument.
 * Run with `npm test`. The money rules themselves (balances, allocation, idempotency) and the result of
 * each instrument are the backend's and are tested there against MySQL.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError, type ApiErrorBody } from '../src/lib/api/errors.ts';
import type { DemoInstrument, DemoTransaction, PayableObligation } from '../src/lib/api/demo-payments.ts';
import { CONNECTION_ERROR, CSRF_MESSAGE } from '../src/lib/obligations/charge-format.ts';
import { METHODS, METHOD_LABELS } from '../src/lib/payments/payment-form.ts';
import {
  CANCELLED_PATH,
  DEMO_NOTICE,
  EMPTY_MESSAGE,
  FORBIDDEN_MESSAGE,
  INVALID_MESSAGE,
  LOAD_ERROR,
  OBLIGATION_GONE,
  RESUMED_IN_PROGRESS,
  SESSION_EXPIRED_MESSAGE,
  SIMULATION_BADGE,
  amountInputText,
  checkoutPath,
  createDemoPaymentFlow,
  isFullPayment,
  isUnavailable,
  legacyCheckoutRedirect,
  obligationLines,
  obligationTitle,
  parseTransactionParam,
  paymentPath,
  projectedOutstanding,
  toCents,
  validateAmount,
  type DemoState
} from '../src/lib/resident/demo-payment.ts';
import {
  CHECKOUT_STEPS,
  CHECK_ERROR,
  EXPECTED_RESULT_LABELS,
  EXPIRY_GRACE_MS,
  FINISHED_MESSAGE,
  INSTRUMENTS_ERROR,
  LOAD_TRANSACTION_ERROR,
  MAX_POLLS,
  MIN_PROCESSING_MS,
  POLLING_STOPPED,
  POLL_INTERVAL_MS,
  RESULT_TEXTS,
  TRANSACTION_STATUS_LABELS,
  UNCONFIRMED_CHECKED,
  UNCONFIRMED_MESSAGE,
  canCancel,
  canChooseInstrument,
  canProcess,
  canRetryWithAnother,
  checkoutStep,
  createDemoCheckoutFlow,
  instrumentDetails,
  instrumentName,
  parseResumedParam,
  receiptLinkOf,
  resultKind,
  resultReasonText,
  retryHint,
  stepIndex,
  transactionHeading,
  transactionLines,
  type CheckoutState
} from '../src/lib/resident/demo-checkout.ts';

const spaces = (text: string) => text.replace(/\s/g, ' ');
const flush = () => new Promise<void>((resolve) => setImmediate(resolve));

function apiError(status: number, code: string, message = 'x', details: ApiErrorBody['error']['details'] = []): ApiError {
  return new ApiError(status, { error: { code, message, details } });
}

const NOW = Date.parse('2026-10-09T20:00:00Z');

function obligation(overrides: Partial<PayableObligation> = {}): PayableObligation {
  return {
    charge_id: 7, concept: 'MONTHLY_FEE', description: null, billing_period: '2026-10', due_date: '2026-10-05',
    amount: '100000.00', applied_amount: '0.00', outstanding_amount: '100000.00', status: 'OVERDUE',
    in_progress_transaction_id: null, ...overrides
  };
}

function transaction(overrides: Partial<DemoTransaction> = {}): DemoTransaction {
  return {
    id: 31, reference: 'DEMO-0123456789ABCDEF', charge_id: 7, amount: '40000.50', status: 'INITIATED',
    created_at: '2026-10-09T20:00:00Z', expires_at: '2026-10-09T20:15:00Z', finished_at: null, payment_id: null,
    receipt_number: null, test_instrument: null, result_reason: null, ...overrides
  };
}

/** The backend's catalog (services/demo_instruments.py), as GET /instruments answers it. */
const CATALOG: DemoInstrument[] = [
  { id: 'TEST_CARD_APPROVE', label: 'Tarjeta de prueba ficticia terminada en 0001', kind: 'CARD', ending: '0001',
    expected_result: 'APPROVED',
    description: 'Instrumento ficticio de demostración: simula un pago aprobado. No es una tarjeta real.' },
  { id: 'TEST_CARD_INSUFFICIENT_FUNDS', label: 'Tarjeta de prueba ficticia terminada en 0002', kind: 'CARD', ending: '0002',
    expected_result: 'REJECTED',
    description: 'Instrumento ficticio de demostración: simula un rechazo por fondos insuficientes. No es una tarjeta real.' },
  { id: 'TEST_ACCOUNT_TECHNICAL_ERROR', label: 'Cuenta de prueba ficticia', kind: 'ACCOUNT', ending: null,
    expected_result: 'FAILED',
    description: 'Instrumento ficticio de demostración: simula un error técnico de procesamiento. No es una cuenta real.' }
];
const [APPROVE, FUNDS, TECHNICAL] = CATALOG.map((instrument) => instrument.id);

const ended = (status: DemoTransaction['status'], extra: Partial<DemoTransaction> = {}) =>
  transaction({ status, finished_at: '2026-10-09T20:03:00Z', ...extra });
const APPROVED = ended('APPROVED', { payment_id: 12, receipt_number: 'RC-00000012', test_instrument: APPROVE });
const REJECTED = ended('REJECTED', { test_instrument: FUNDS, result_reason: 'INSUFFICIENT_FUNDS' });
const FAILED = ended('FAILED', { test_instrument: TECHNICAL, result_reason: 'TECHNICAL_ERROR' });
const NOT_APPLIED = ended('FAILED', { test_instrument: APPROVE, result_reason: 'NOT_APPLIED' });

type Answer<T> = T | Error | (() => Promise<T>);

async function answer<T>(name: string, queue: Answer<T>[] | undefined): Promise<T> {
  const next = queue?.shift();
  if (next === undefined) throw new Error(`unexpected call: ${name}`);
  if (next instanceof Error) throw next;
  if (typeof next === 'function') return (next as () => Promise<T>)();
  return next;
}

const list = (...items: PayableObligation[]) => ({ items });

/** A request whose answer the test releases when it decides (no timing involved). */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject, answer: () => promise };
}

function fakeTimers() {
  const timers = new Map<number, { callback: () => void; ms: number }>();
  let next = 1;
  return {
    setTimer: (callback: () => void, ms: number) => {
      timers.set(next, { callback, ms });
      return next++;
    },
    clearTimer: (handle: unknown) => void timers.delete(handle as number),
    delays: () => [...timers.values()].map(({ ms }) => ms),
    async fire(): Promise<void> {
      const [id, timer] = [...timers.entries()][0] ?? [];
      assert.ok(timer, 'a timer is pending');
      timers.delete(id!);
      timer.callback();
      await flush();
    }
  };
}

// ============================================================================================================
// The obligations (demo-payment.ts)
// ============================================================================================================

function setUpList(queues: { list?: Answer<{ items: PayableObligation[] }>[]; start?: Answer<DemoTransaction>[] }) {
  const calls: [string, ...unknown[]][] = [];
  const states: DemoState[] = [];
  const navigated: string[] = [];
  const flow = createDemoPaymentFlow({
    api: {
      list: () => (calls.push(['list']), answer('list', queues.list)),
      start: (chargeId: number, amount: string) => (calls.push(['start', chargeId, amount]), answer('start', queues.start))
    },
    render: (state) => states.push(state),
    navigate: (path) => navigated.push(path)
  });
  return { flow, calls, names: () => calls.map(([name]) => name), states, navigated };
}

/** The review of 40.000,50 for obligation 7. */
async function inReview(queues: Parameters<typeof setUpList>[0] = {}) {
  const setup = setUpList({ ...queues, list: [list(obligation()), ...(queues.list ?? [])] });
  await setup.flow.init();
  setup.flow.select(7);
  setup.flow.review('40000,50');
  assert.equal(setup.flow.state.view.kind, 'review');
  return setup;
}

describe('demo payment: amounts', () => {
  it('toCents reads the API decimal text exactly (BigInt), never a binary float', () => {
    assert.equal(toCents('100000.00'), 10000000n);
    assert.equal(toCents('0.10'), 10n);
    assert.equal(toCents('40000.5'), 4000050n);
    assert.equal(toCents('9999999999.99'), 999999999999n); // the backend's maximum, exact
    assert.equal(toCents('1e3'), null);
    assert.equal(toCents('-1.00'), null);
    assert.equal(toCents('1.001'), null);
  });

  it('the outstanding balance is offered as the resident types it', () => {
    assert.equal(amountInputText('100000.00'), '100000');
    assert.equal(amountInputText('40000.25'), '40000,25');
    assert.equal(amountInputText('40000.50'), '40000,50');
    assert.equal(amountInputText('40000'), '40000');
  });

  it('full and partial payments up to the outstanding balance, as exact text', () => {
    assert.deepEqual(validateAmount('100000', '100000.00'), { value: '100000' });
    assert.deepEqual(validateAmount('100000,00', '100000.00'), { value: '100000.00' });
    assert.deepEqual(validateAmount('40000,5', '100000.00'), { value: '40000.50' });
    assert.deepEqual(validateAmount(' $ 0,01 ', '100000.00'), { value: '0.01' });
    assert.deepEqual(validateAmount('9999999999.99', '9999999999.99'), { value: '9999999999.99' });
    assert.ok(isFullPayment('100000', '100000.00') && isFullPayment('100000.00', '100000.00'));
    assert.ok(!isFullPayment('99999.99', '100000.00'));
  });

  it('rejects invalid amounts before any request, with the messages of the form', () => {
    const over = { error: 'El monto no puede superar el saldo pendiente de la obligación ($ 100.000,00).' };
    const normalized = (r: ReturnType<typeof validateAmount>) => ('error' in r ? { error: spaces(r.error) } : r);
    assert.deepEqual(normalized(validateAmount('100000,01', '100000.00')), over); // one cent above: exact comparison
    assert.deepEqual(normalized(validateAmount('9999999999.99', '9999999999.98')), {
      error: 'El monto no puede superar el saldo pendiente de la obligación ($ 9.999.999.999,98).' });
    assert.deepEqual(validateAmount('', '100000.00'), { error: 'Ingresa el monto.' });
    assert.deepEqual(validateAmount('0', '100000.00'), { error: 'El monto debe ser mayor que cero.' });
    assert.deepEqual(validateAmount('0,00', '100000.00'), { error: 'El monto debe ser mayor que cero.' });
    for (const text of ['-100', '100.000', '100,001', '1e5', 'abc', '10.000,50']) {
      assert.match(spaces((validateAmount(text, '100000.00') as { error: string }).error), /solo con números/, text);
    }
    assert.match(spaces((validateAmount('10000000000', '9999999999.99') as { error: string }).error), /no puede superar \$ 9\.999\.999\.999,99/);
  });

  it('the projected balance of the review: exact, in cents; nothing when it cannot be computed', () => {
    assert.equal(projectedOutstanding('40000.50', '100000.00'), '59999.50');
    assert.equal(projectedOutstanding('100000', '100000.00'), '0.00'); // a full payment
    assert.equal(projectedOutstanding('0.01', '9999999999.99'), '9999999999.98');
    assert.equal(projectedOutstanding('100000.01', '100000.00'), null); // above the balance: never negative
    assert.equal(projectedOutstanding('1e3', '100000.00'), null);
  });
});

describe('demo payment: what the resident reads', () => {
  it('the permanent notice: an academic demo, no real money, no external provider, no card data', () => {
    assert.match(DEMO_NOTICE, /simulación interna/);
    assert.match(DEMO_NOTICE, /no se realiza ningún cobro real/);
    assert.match(DEMO_NOTICE, /no se mueve dinero/);
    assert.match(DEMO_NOTICE, /pasarela ni con un proveedor financiero externo/);
    assert.match(DEMO_NOTICE, /Nunca se solicitan datos de tarjetas/);
    assert.equal(SIMULATION_BADGE, 'Entorno de simulación: no se realizará ningún cobro real');
  });

  it('an obligation: concept, period, due date, value, applied and outstanding', () => {
    assert.equal(obligationTitle(obligation()), 'Mensualidad — octubre de 2026');
    assert.equal(obligationTitle(obligation({ concept: 'PENALTY', description: 'Ruido', billing_period: null })), 'Penalización: Ruido');
    assert.deepEqual(obligationLines(obligation({ applied_amount: '10000.25', outstanding_amount: '89999.75' })).map((l) => [l.label, spaces(l.value)]), [
      ['Período', 'Octubre de 2026'], ['Vencimiento', '5 de octubre de 2026'], ['Valor', '$ 100.000,00'],
      ['Abonado', '$ 10.000,25'], ['Saldo pendiente', '$ 89.999,75']
    ]);
    assert.equal(obligationLines(obligation({ billing_period: null }))[0].value, 'No aplica');
  });

  it('the checkout of a transaction has its own URL; the legacy ?transaction= URL leads there', () => {
    assert.equal(checkoutPath(31), '/resident/demo-payments/checkout?transaction=31');
    assert.equal(checkoutPath(31, { resumed: true }), '/resident/demo-payments/checkout?transaction=31&resumed=1');
    assert.equal(legacyCheckoutRedirect('?transaction=31'), '/resident/demo-payments/checkout?transaction=31');
    for (const search of ['', '?transaction=', '?transaction=0', '?transaction=abc', '?transaction=1e3', '?cancelled=1']) {
      assert.equal(legacyCheckoutRedirect(search), null, search); // no valid id: the obligations, no redirection
    }
    assert.equal(CANCELLED_PATH, '/resident/demo-payments?cancelled=1');
    assert.equal(paymentPath(12), '/resident/payments/12');
  });

  it('the transaction id of the URL: a positive integer only', () => {
    assert.equal(parseTransactionParam('?transaction=31'), 31);
    assert.equal(parseTransactionParam('?transaction=31&resumed=1'), 31);
    for (const search of ['', '?transaction=', '?transaction=0', '?transaction=-1', '?transaction=abc', '?transaction=1e3',
      '?transaction=3.5', '?transaction=99999999999999999', '?transaction=01']) {
      assert.equal(parseTransactionParam(search), null, search);
    }
  });

  it('the history shows the demo gateway with its own label; the administrator still picks manual methods only', () => {
    assert.equal(METHOD_LABELS.DEMO_GATEWAY, 'Pasarela de demostración');
    assert.deepEqual(METHODS, ['CASH', 'CARD', 'TRANSFER']); // HU-004 form unchanged
  });
});

describe('demo payment: obligations', () => {
  it('loading, then the list the server sent', async () => {
    const { flow, states, names } = setUpList({ list: [list(obligation(), obligation({ charge_id: 8 }))] });
    await flow.init();
    assert.equal(states[0].view.kind, 'loading');
    assert.equal(states[0].busy, 'loading');
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.obligations.map((o) => o.charge_id), [7, 8]);
    assert.deepEqual(names(), ['list']);
  });

  it('no obligations: an empty list (the page says so)', async () => {
    const { flow } = setUpList({ list: [list()] });
    await flow.init();
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.obligations, []);
    assert.equal(EMPTY_MESSAGE, 'No tienes obligaciones pendientes por pagar.');
  });

  it('after a cancellation confirmed in the checkout: the list with its notice', async () => {
    const notice = { tone: 'info' as const, text: 'Cancelaste la operación.' };
    const { flow } = setUpList({ list: [list(obligation())] });
    await flow.init(notice);
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.notice, notice);
  });

  it('errors while loading: session, permission, the simulator disabled, the network, the server', async () => {
    const cases: [Error, Partial<DemoState>][] = [
      [apiError(401, 'NOT_AUTHENTICATED'), { view: { kind: 'load-error', message: SESSION_EXPIRED_MESSAGE }, sessionExpired: true }],
      [apiError(403, 'FORBIDDEN'), { view: { kind: 'load-error', message: FORBIDDEN_MESSAGE } }],
      [apiError(404, 'NOT_FOUND'), { view: { kind: 'unavailable' } }], // disabled: never "no obligations"
      [new TypeError('Failed to fetch'), { view: { kind: 'load-error', message: CONNECTION_ERROR } }],
      [apiError(500, 'INTERNAL_ERROR', 'Traceback (most recent call last)'), { view: { kind: 'load-error', message: LOAD_ERROR } }]
    ];
    for (const [error, expected] of cases) {
      const { flow } = setUpList({ list: [error] });
      await flow.init();
      for (const [key, value] of Object.entries(expected)) assert.deepEqual(flow.state[key as keyof DemoState], value, error.message);
      assert.equal(flow.state.busy, null);
    }
  });

  it('retry loads the list again', async () => {
    const { flow } = setUpList({ list: [new TypeError('offline'), list(obligation())] });
    await flow.init();
    assert.equal(flow.state.view.kind, 'load-error');
    await flow.retry();
    assert.equal(flow.state.view.kind, 'list');
  });

  it('an older list does not overwrite a newer one', async () => {
    const stale = deferred<{ items: PayableObligation[] }>();
    const { flow } = setUpList({ list: [stale.answer, list()] });
    const first = flow.init();
    await flow.init(); // a newer list: nothing left to pay
    stale.resolve(list(obligation())); // the older list arrives last
    await first;
    assert.deepEqual(flow.state.obligations, []);
  });

  it('a 404 of the route is "not available", a 404 of a resource is not', () => {
    assert.ok(isUnavailable(apiError(404, 'NOT_FOUND')));
    assert.ok(!isUnavailable(apiError(404, 'CHARGE_NOT_FOUND')));
    assert.ok(!isUnavailable(apiError(404, 'DEMO_TRANSACTION_NOT_FOUND')));
  });

  it('"Continuar pago en curso" opens the checkout of that transaction (no request, never a second one)', async () => {
    const { flow, names, navigated } = setUpList({ list: [list(obligation({ in_progress_transaction_id: 31 }))] });
    await flow.init();
    flow.select(7);
    flow.select(7); // a double click
    assert.deepEqual(navigated, ['/resident/demo-payments/checkout?transaction=31&resumed=1']);
    assert.deepEqual(names(), ['list']);
    assert.equal(flow.state.busy, 'opening'); // everything stays disabled while the page is left
  });
});

describe('demo payment: form and review', () => {
  it('selecting an obligation opens the form with its outstanding balance', async () => {
    const { flow } = setUpList({ list: [list(obligation({ applied_amount: '59999.75', outstanding_amount: '40000.25', status: 'PARTIAL' }))] });
    await flow.init();
    flow.select(7);
    assert.deepEqual(flow.state.view, { kind: 'form', obligation: flow.state.obligations[0], amountText: '40000,25', error: null });
  });

  it('only an obligation of the list with a balance can be selected', async () => {
    const { flow, names } = setUpList({ list: [list(obligation({ outstanding_amount: '0.00', applied_amount: '100000.00' }))] });
    await flow.init();
    flow.select(7); // no balance left
    flow.select(999); // not in the list (another resident's, or invented)
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(names(), ['list']);
  });

  it('an invalid amount stays in the form with its error and nothing is sent', async () => {
    const { flow, names } = setUpList({ list: [list(obligation())] });
    await flow.init();
    flow.select(7);
    flow.review('100000,01');
    assert.equal(flow.state.view.kind, 'form');
    assert.match(spaces((flow.state.view as { error: string }).error), /no puede superar el saldo pendiente/);
    assert.equal((flow.state.view as { amountText: string }).amountText, '100000,01'); // what was typed is kept
    flow.review('0');
    assert.equal((flow.state.view as { error: string }).error, 'El monto debe ser mayor que cero.');
    assert.deepEqual(names(), ['list']);
  });

  it('a valid amount shows the review; editing returns to the form; back returns to the list (no request)', async () => {
    const { flow, names } = setUpList({ list: [list(obligation()), list(obligation())] });
    await flow.init();
    flow.select(7);
    flow.review('40000,5');
    assert.deepEqual(flow.state.view, { kind: 'review', obligation: obligation(), amount: '40000.50' });
    flow.edit();
    assert.deepEqual(flow.state.view, { kind: 'form', obligation: obligation(), amountText: '40000,50', error: null });
    await flow.back();
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(names(), ['list', 'list']); // never a start
  });
});

describe('demo payment: start, then the checkout', () => {
  it('sends exactly the obligation and the amount text, then opens the checkout of the transaction created', async () => {
    const { flow, calls, navigated } = await inReview({ start: [transaction({ id: 31 })] });
    await flow.start();
    assert.deepEqual(calls.at(-1), ['start', 7, '40000.50']); // a string, never a number
    assert.deepEqual(navigated, ['/resident/demo-payments/checkout?transaction=31']); // the id the server answered
    assert.equal(flow.state.busy, 'opening');
  });

  it('a full payment sends the balance as typed by default', async () => {
    const { flow, calls } = setUpList({ list: [list(obligation())], start: [transaction({ amount: '100000.00' })] });
    await flow.init();
    flow.select(7);
    flow.review((flow.state.view as { amountText: string }).amountText);
    await flow.start();
    assert.deepEqual(calls.at(-1), ['start', 7, '100000']);
  });

  it('a double click starts once and opens one checkout: everything is disabled while it is sent', async () => {
    const pending = deferred<DemoTransaction>();
    const { flow, names, states, navigated } = await inReview({ start: [pending.answer] });
    const first = flow.start();
    const second = flow.start();
    assert.equal(flow.state.busy, 'starting');
    flow.select(7);
    flow.edit();
    await flow.back();
    pending.resolve(transaction());
    await Promise.all([first, second]);
    await flow.start(); // after the answer too: the page is being left
    assert.deepEqual(names(), ['list', 'start']);
    assert.deepEqual(navigated, ['/resident/demo-payments/checkout?transaction=31']);
    assert.ok(states.some((s) => s.busy === 'starting'));
  });

  it('the backend validates again: its amount error is shown next to the field', async () => {
    const error = apiError(422, 'VALIDATION_ERROR', 'Revisa', [{ field: 'amount', message: 'El monto no puede superar el saldo pendiente de la obligación ($ 30.000,00).' }]);
    const { flow, navigated } = await inReview({ start: [error] });
    await flow.start();
    assert.deepEqual(flow.state.view, { kind: 'form', obligation: obligation(), amountText: '40000,50',
      error: 'El monto no puede superar el saldo pendiente de la obligación ($ 30.000,00).' });
    assert.deepEqual(navigated, []);
  });

  async function startFails(error: Error, more: { list?: Answer<{ items: PayableObligation[] }>[] } = {}) {
    const setup = await inReview({ start: [error], list: more.list });
    await setup.flow.start();
    return setup;
  }

  it('409 CHARGE_NOT_PAYABLE: the list again, with the backend message', async () => {
    const message = 'La obligación no tiene saldo pendiente por pagar.';
    const { flow, names } = await startFails(apiError(409, 'CHARGE_NOT_PAYABLE', message), { list: [list()] });
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.obligations, []);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: message });
    assert.deepEqual(names(), ['list', 'start', 'list']);
  });

  it('409 DEMO_PAYMENT_IN_PROGRESS: the resident\'s own live transaction opens in its checkout, never a second one', async () => {
    const own = obligation({ in_progress_transaction_id: 30 });
    const { names, navigated } = await startFails(apiError(409, 'DEMO_PAYMENT_IN_PROGRESS', 'Ya hay un pago en curso'),
      { list: [list(own)] });
    assert.deepEqual(names(), ['list', 'start', 'list']);
    assert.deepEqual(navigated, ['/resident/demo-payments/checkout?transaction=30&resumed=1']);
  });

  it('409 DEMO_PAYMENT_IN_PROGRESS of someone else: the list with the message', async () => {
    const { flow, navigated } = await startFails(apiError(409, 'DEMO_PAYMENT_IN_PROGRESS', 'Ya hay un pago en curso'), { list: [list(obligation())] });
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.notice, { tone: 'error', text: 'Ya hay un pago en curso' });
    assert.deepEqual(navigated, []);
  });

  it('404 CHARGE_NOT_FOUND: the obligation is no longer there (another resident\'s looks the same)', async () => {
    const { flow } = await startFails(apiError(404, 'CHARGE_NOT_FOUND', 'La obligación no existe.'), { list: [list()] });
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.notice, { tone: 'error', text: OBLIGATION_GONE });
  });

  it('404 NOT_FOUND: the simulator was disabled meanwhile (not a financial error)', async () => {
    const { flow } = await startFails(apiError(404, 'NOT_FOUND', 'No encontrado'));
    assert.equal(flow.state.view.kind, 'unavailable');
    assert.equal(flow.state.notice, null);
  });

  it('401: session expired, the review stays and nothing else can be sent', async () => {
    const { flow, names } = await startFails(apiError(401, 'NOT_AUTHENTICATED'));
    assert.equal(flow.state.view.kind, 'review');
    assert.equal(flow.state.sessionExpired, true);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: SESSION_EXPIRED_MESSAGE });
    await flow.start();
    assert.deepEqual(names(), ['list', 'start']);
  });

  it('403: CSRF or permission, without technical text', async () => {
    assert.deepEqual((await startFails(apiError(403, 'CSRF_FAILED'))).flow.state.notice, { tone: 'error', text: CSRF_MESSAGE });
    assert.deepEqual((await startFails(apiError(403, 'FORBIDDEN'))).flow.state.notice, { tone: 'error', text: FORBIDDEN_MESSAGE });
    assert.deepEqual((await startFails(apiError(422, 'VALIDATION_ERROR', 'x', [{ field: 'charge_id', message: 'x' }]))).flow.state.notice,
      { tone: 'error', text: INVALID_MESSAGE });
    assert.deepEqual((await startFails(new TypeError('Failed to fetch'))).flow.state.notice, { tone: 'error', text: CONNECTION_ERROR });
  });
});

// ============================================================================================================
// The checkout (demo-checkout.ts)
// ============================================================================================================

interface CheckoutQueues {
  get?: Answer<DemoTransaction>[];
  complete?: Answer<DemoTransaction>[];
  cancel?: Answer<DemoTransaction>[];
  start?: Answer<DemoTransaction>[];
  /** Without a queue the obligations always answer [obligation()]. */
  list?: Answer<{ items: PayableObligation[] }>[];
  /** Without a queue the catalog always answers CATALOG. */
  instruments?: Answer<{ items: DemoInstrument[] }>[];
}

/**
 * A fake API: each method answers from its queue (a value, an error, or a pending promise) and logs its
 * calls. The obligations and the catalog are read on their own tracks (logged apart: they never change
 * the transaction's sequence).
 */
function fakeCheckoutApi(queues: CheckoutQueues) {
  const calls: [string, ...unknown[]][] = [];
  const listCalls: number[] = [];
  const catalogCalls: number[] = [];
  return {
    calls,
    listCalls,
    catalogCalls,
    names: () => calls.map(([name]) => name),
    api: {
      get: (id: number) => (calls.push(['get', id]), answer('get', queues.get)),
      complete: (id: number, instrumentId: string) => (calls.push(['complete', id, instrumentId]), answer('complete', queues.complete)),
      cancel: (id: number) => (calls.push(['cancel', id]), answer('cancel', queues.cancel)),
      start: (chargeId: number, amount: string) => (calls.push(['start', chargeId, amount]), answer('start', queues.start)),
      list: () => (listCalls.push(listCalls.length + 1),
        queues.list ? answer('list', queues.list) : Promise.resolve(list(obligation()))),
      instruments: () => (catalogCalls.push(catalogCalls.length + 1),
        queues.instruments ? answer('instruments', queues.instruments) : Promise.resolve({ items: CATALOG }))
    }
  };
}

function setUpCheckout(queues: CheckoutQueues, clock = { now: NOW }, minProcessingMs = 0) {
  const fake = fakeCheckoutApi(queues);
  const timers = fakeTimers();
  const states: CheckoutState[] = [];
  const navigated: string[] = [];
  const flow = createDemoCheckoutFlow({
    api: fake.api,
    render: (state) => states.push(state),
    navigate: (path) => navigated.push(path),
    now: () => clock.now,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
    minProcessingMs
  });
  return { flow, fake, timers, states, navigated, clock };
}

type CheckoutFlow = ReturnType<typeof createDemoCheckoutFlow>;
const shown = (flow: { state: CheckoutState }) => (flow.state.view as { transaction: DemoTransaction }).transaction;

/** The checkout of /resident/demo-payments/checkout?transaction=31, with its INITIATED transaction of 40.000,50. */
async function inCheckout(queues: CheckoutQueues = {}, clock = { now: NOW }, minProcessingMs = 0) {
  const setup = setUpCheckout({ ...queues, get: [transaction(), ...(queues.get ?? [])] }, clock, minProcessingMs);
  await setup.flow.init(31);
  await flush(); // the catalog of instruments, read when the transaction is shown
  assert.equal(setup.flow.state.view.kind, 'transaction');
  return setup;
}

/** The resident chooses an instrument and presses "Procesar pago de demostración". */
function pay(flow: CheckoutFlow, instrumentId: string): Promise<void> {
  flow.selectInstrument(instrumentId);
  return flow.process();
}

describe('demo checkout: what the resident reads', () => {
  it('each instrument: its kind, fictitious ending, description and what it simulates, as the server sent them', () => {
    assert.deepEqual(instrumentDetails(CATALOG[0]), [
      'Tarjeta de prueba · terminación ficticia 0001', CATALOG[0].description, 'Resultado esperado: simula una aprobación.'
    ]);
    assert.deepEqual(instrumentDetails(CATALOG[1]), [
      'Tarjeta de prueba · terminación ficticia 0002', CATALOG[1].description, 'Resultado esperado: simula un rechazo.'
    ]);
    assert.deepEqual(instrumentDetails(CATALOG[2]), [ // an account: no ending
      'Cuenta de prueba', CATALOG[2].description, 'Resultado esperado: simula un error de procesamiento.'
    ]);
    assert.deepEqual(Object.keys(EXPECTED_RESULT_LABELS), ['APPROVED', 'REJECTED', 'FAILED']);
    // The name of the instrument of a transaction: its label, or its id if the catalog no longer lists it.
    assert.equal(instrumentName(FUNDS, CATALOG), 'Tarjeta de prueba ficticia terminada en 0002');
    assert.equal(instrumentName('TEST_CARD_WITHDRAWN', CATALOG), 'TEST_CARD_WITHDRAWN');
    assert.equal(instrumentName(APPROVE, []), APPROVE);
  });

  it('every result in words (never color alone): a simulated rejection or error is never the backend\'s failure', () => {
    assert.deepEqual(TRANSACTION_STATUS_LABELS, {
      INITIATED: 'En curso', APPROVED: 'Aprobado', REJECTED: 'Rechazado', FAILED: 'Fallido', CANCELLED: 'Cancelado',
      EXPIRED: 'Vencido'
    });
    assert.equal(resultKind(transaction()), null);
    assert.equal(resultKind(APPROVED), 'approved');
    assert.equal(resultKind(REJECTED), 'rejected');
    assert.equal(resultKind(FAILED), 'technical');
    assert.equal(resultKind(NOT_APPLIED), 'not-applied');
    assert.equal(resultKind(ended('FAILED')), 'failed'); // an older one, without a reason
    assert.equal(resultKind(ended('CANCELLED')), 'cancelled');
    assert.equal(resultKind(ended('EXPIRED')), 'expired');
    assert.equal(transactionHeading(FAILED).title, 'Error de procesamiento simulado');
    assert.equal(transactionHeading(NOT_APPLIED).title, 'El pago no pudo aplicarse');
    assert.doesNotMatch(transactionHeading(NOT_APPLIED).title + transactionHeading(NOT_APPLIED).text, /simul/);
    assert.match(RESULT_TEXTS.approved.text, /registró en tu historial/);
    assert.match(RESULT_TEXTS.approved.text, /No se movió dinero real/);
    for (const kind of ['rejected', 'technical', 'not-applied', 'failed', 'cancelled', 'expired'] as const) {
      assert.match(RESULT_TEXTS[kind].text, /No se (aplicó|registró) ningún pago/, kind);
    }
    assert.match(RESULT_TEXTS.rejected.text, /sigue pendiente/);
    assert.equal(transactionHeading(transaction()).title, 'Elige un instrumento de prueba');
  });

  it('the reason in words: a simulated one says so; NOT_APPLIED is the backend\'s decision, not a simulation', () => {
    const funds = resultReasonText(REJECTED)!;
    const technical = resultReasonText(FAILED)!;
    const notApplied = resultReasonText(NOT_APPLIED)!;
    assert.match(funds, /^Motivo simulado: fondos insuficientes/);
    assert.match(funds, /no se consultó ningún banco ni se intentó un cobro real/);
    assert.match(technical, /^Motivo simulado: error técnico/);
    assert.match(technical, /No hubo ninguna falla real/);
    assert.doesNotMatch(notApplied, /simulado/);
    assert.match(notApplied, /No se registró ningún pago y tu cuenta no cambió/);
    for (const plain of [APPROVED, ended('CANCELLED'), ended('EXPIRED'), transaction(), ended('REJECTED')]) {
      assert.equal(resultReasonText(plain), null, plain.status); // no reason stored: none shown
    }
  });

  it('the summary: obligation, amount, reference, current balance, expiry; once ended, the instrument and the number', () => {
    const live = transactionLines(transaction(), obligation());
    assert.deepEqual(live.map((l) => l.label), ['Obligación', 'Monto', 'Referencia de demostración', 'Saldo pendiente actual',
      'Iniciada', 'Vence']);
    assert.equal(spaces(live[1].value), '$ 40.000,50');
    assert.match(spaces(live[5].value), /^9 de octubre de 2026.* 3:15 p\. m\.$/); // Bogotá time
    // Without the obligation (no longer listed): only what the transaction keeps, nothing invented.
    const approved = transactionLines(APPROVED, null, CATALOG);
    assert.deepEqual(approved.map((l) => l.label), ['Monto', 'Referencia de demostración', 'Iniciada', 'Finalizada',
      'Instrumento de prueba', 'Número del pago en tu historial']);
    assert.equal(approved[4].value, 'Tarjeta de prueba ficticia terminada en 0001');
    assert.ok(approved.every((l) => !/pdf|descarg/i.test(`${l.label} ${l.value}`)));
    assert.ok(transactionLines(REJECTED, null).every((l) => !l.label.startsWith('Número')));
  });

  it('the receipt link: only for an APPROVED answer with the persisted payment id', () => {
    assert.equal(receiptLinkOf(APPROVED), '/api/v1/resident/payments/12/receipt'); // the payment's id, not the transaction's (31)
    for (const status of ['INITIATED', 'REJECTED', 'FAILED', 'CANCELLED', 'EXPIRED'] as const) {
      assert.equal(receiptLinkOf(status === 'INITIATED' ? transaction() : ended(status)), null, status);
    }
    assert.equal(receiptLinkOf({ ...APPROVED, payment_id: null }), null); // never invented
    assert.equal(receiptLinkOf(NOT_APPLIED), null); // FAILED without a payment: no receipt either
  });

  it('the steps: the amount (done on the obligations page), the instrument, the processing, the result', () => {
    assert.deepEqual(CHECKOUT_STEPS, ['Obligación y monto', 'Instrumento de prueba', 'Procesamiento', 'Resultado']);
    assert.deepEqual((['select', 'processing', 'uncertain', 'result'] as const).map(stepIndex), [1, 2, 2, 3]);
    assert.equal(parseResumedParam('?transaction=31&resumed=1'), true);
    assert.equal(parseResumedParam('?transaction=31'), false);
    assert.match(spaces(retryHint(REJECTED)), /^Se iniciará una nueva transacción de demostración por \$ 40\.000,50 para la misma obligación\./);
  });
});

describe('demo checkout: opening and reloading', () => {
  it('reads the transaction of the URL and its obligation; the choice step when INITIATED (never started again)', async () => {
    const { flow, fake, states, timers } = await inCheckout();
    assert.equal(states[0].view.kind, 'loading');
    assert.equal(states[0].busy, 'loading');
    assert.deepEqual(fake.names(), ['get']); // read, never started
    assert.deepEqual(fake.calls[0], ['get', 31]);
    assert.equal(fake.listCalls.length, 1);
    assert.deepEqual(flow.state.obligation, obligation());
    assert.equal(flow.state.listed, true);
    assert.equal(checkoutStep(flow.state), 'select');
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]);
    assert.equal(flow.state.polling, true);
  });

  it('a reload of an INITIATED checkout recovers it from the server, with nothing chosen', async () => {
    const first = await inCheckout();
    first.flow.selectInstrument(APPROVE);
    first.flow.dispose(); // the page is left (reload)
    const { flow, fake } = await inCheckout();
    assert.equal(shown(flow).status, 'INITIATED');
    assert.equal(flow.state.selectedInstrument, null); // nothing kept in the browser
    assert.ok(!fake.names().includes('start'));
  });

  it('opened for a transaction already in progress: it says so, but only while it can be continued', async () => {
    const live = setUpCheckout({ get: [transaction()] });
    await live.flow.init(31, { resumed: true });
    assert.deepEqual(live.flow.state.notice, { tone: 'info', text: RESUMED_IN_PROGRESS });
    const over = setUpCheckout({ get: [ended('EXPIRED')] });
    await over.flow.init(31, { resumed: true });
    assert.equal(over.flow.state.notice, null);
  });

  it('a reload of a finished one shows its stored result, instrument and reason, never processed again', async () => {
    const { flow, fake } = setUpCheckout({ get: [REJECTED] });
    await flow.init(31);
    await flush();
    assert.deepEqual(shown(flow), REJECTED);
    assert.equal(checkoutStep(flow.state), 'result');
    assert.match(resultReasonText(shown(flow))!, /fondos insuficientes/);
    assert.equal(fake.catalogCalls.length, 1); // to name the instrument
    const named = transactionLines(shown(flow), null, flow.state.catalog.items).find((l) => l.label === 'Instrumento de prueba');
    assert.equal(named?.value, 'Tarjeta de prueba ficticia terminada en 0002');
    await pay(flow, APPROVE);
    await flow.cancel();
    assert.deepEqual(fake.names(), ['get']);
  });

  it('an older transaction without an instrument: shown as it was, no instrument invented, never processed', async () => {
    const older = ended('REJECTED'); // resolved before the instruments existed: test_instrument and result_reason null
    const { flow, fake } = setUpCheckout({ get: [older] });
    await flow.init(31);
    await flush();
    assert.equal(shown(flow).status, 'REJECTED');
    assert.equal(shown(flow).test_instrument, null);
    assert.equal(resultReasonText(shown(flow)), null);
    assert.ok(transactionLines(shown(flow), null, CATALOG).every((l) => l.label !== 'Instrumento de prueba'));
    assert.deepEqual(fake.catalogCalls, []); // nothing to choose or to name
    assert.equal(flow.state.selectedInstrument, null);
    await pay(flow, FUNDS);
    assert.deepEqual(fake.names(), ['get']);
  });

  it('after a full approval the obligation is no longer listed: only the transaction\'s own data, said honestly', async () => {
    const { flow } = setUpCheckout({ get: [APPROVED], list: [list()] });
    await flow.init(31);
    assert.equal(flow.state.listed, false);
    assert.equal(flow.state.obligation, null);
    assert.ok(transactionLines(shown(flow), flow.state.obligation).every((l) => l.label !== 'Obligación'));
    const failed = setUpCheckout({ get: [APPROVED], list: [new TypeError('offline')] });
    await failed.flow.init(31);
    assert.equal(failed.flow.state.listed, null); // not read: no claim either way
    assert.equal(shown(failed.flow).status, 'APPROVED'); // the transaction is still shown
  });

  it('no transaction in the URL: a safe exit, nothing requested', async () => {
    const { flow, fake } = setUpCheckout({});
    await flow.init(null);
    assert.equal(flow.state.view.kind, 'missing');
    assert.deepEqual(fake.names(), []);
    assert.deepEqual(fake.listCalls, []);
  });

  it('another resident\'s or a missing transaction: "no existe", without any data', async () => {
    for (const error of [apiError(404, 'DEMO_TRANSACTION_NOT_FOUND', 'La transacción no existe.'), apiError(422, 'VALIDATION_ERROR')]) {
      const { flow, fake } = setUpCheckout({ get: [error] });
      await flow.init(999);
      assert.equal(flow.state.view.kind, 'not-found');
      assert.equal(flow.state.obligation, null);
      assert.deepEqual(fake.listCalls, []); // nothing else read
      assert.deepEqual(fake.catalogCalls, []);
    }
  });

  it('errors while loading: session, the simulator disabled, the network, the server; "Reintentar" reads it again', async () => {
    const cases: [Error, CheckoutState['view'], boolean][] = [
      [apiError(401, 'NOT_AUTHENTICATED'), { kind: 'load-error', message: SESSION_EXPIRED_MESSAGE }, true],
      [apiError(404, 'NOT_FOUND'), { kind: 'unavailable' }, false],
      [new TypeError('Failed to fetch'), { kind: 'load-error', message: CONNECTION_ERROR }, false],
      [apiError(500, 'INTERNAL_ERROR', 'Traceback (most recent call last)'), { kind: 'load-error', message: LOAD_TRANSACTION_ERROR }, false]
    ];
    for (const [error, view, expired] of cases) {
      const { flow } = setUpCheckout({ get: [error] });
      await flow.init(31);
      assert.deepEqual(flow.state.view, view, error.message);
      assert.equal(flow.state.sessionExpired, expired);
      assert.equal(flow.state.busy, null);
    }
    const { flow, fake } = setUpCheckout({ get: [new TypeError('offline'), transaction()] });
    await flow.init(31);
    await flow.retry();
    assert.equal(shown(flow).status, 'INITIATED');
    assert.deepEqual(fake.names(), ['get', 'get']);
  });
});

describe('demo checkout: the test instruments, from the server', () => {
  it('read once from the API, in its order and with its data; nothing preselected', async () => {
    const { flow, fake, timers } = await inCheckout({ get: [transaction(), transaction()] });
    assert.equal(fake.catalogCalls.length, 1);
    assert.deepEqual(flow.state.catalog, { status: 'ready', items: CATALOG, error: null });
    assert.deepEqual(flow.state.catalog.items.map((i) => i.id), [APPROVE, FUNDS, TECHNICAL]);
    assert.equal(flow.state.selectedInstrument, null);
    assert.ok(canChooseInstrument(flow.state));
    assert.ok(!canProcess(flow.state)); // "Procesar" stays disabled until an instrument is chosen
    await timers.fire(); // the readings of the transaction never read the catalog again
    await timers.fire();
    assert.equal(fake.catalogCalls.length, 1);
  });

  it('while it loads: its status, no choice and nothing processed; cancel and "Consultar estado" still work', async () => {
    const catalog = deferred<{ items: DemoInstrument[] }>();
    const { flow, fake } = await inCheckout({ instruments: [catalog.answer], get: [transaction()] });
    assert.equal(flow.state.catalog.status, 'loading');
    assert.equal(flow.state.busy, null); // the catalog never blocks the transaction's actions
    assert.ok(canCancel(flow.state));
    flow.selectInstrument(APPROVE);
    assert.equal(flow.state.selectedInstrument, null);
    await flow.process();
    assert.deepEqual(fake.names(), ['get']);
    await flow.refresh();
    assert.deepEqual(fake.names(), ['get', 'get']);
    catalog.resolve({ items: CATALOG });
    await flush();
    assert.equal(flow.state.catalog.status, 'ready');
    flow.selectInstrument(APPROVE);
    assert.equal(flow.state.selectedInstrument, APPROVE);
  });

  it('an empty catalog: nothing can be chosen or processed', async () => {
    const { flow, fake } = await inCheckout({ instruments: [{ items: [] }] });
    assert.deepEqual(flow.state.catalog, { status: 'ready', items: [], error: null });
    await pay(flow, APPROVE);
    assert.equal(flow.state.selectedInstrument, null);
    assert.deepEqual(fake.names(), ['get']);
  });

  it('a catalog that cannot be read: its message, nothing processed, and "Reintentar" reads it again', async () => {
    const { flow, fake } = await inCheckout({ instruments: [new TypeError('Failed to fetch'), { items: CATALOG }] });
    assert.deepEqual(flow.state.catalog, { status: 'error', items: [], error: CONNECTION_ERROR });
    assert.ok(!canChooseInstrument(flow.state) && !canProcess(flow.state));
    await pay(flow, APPROVE);
    assert.deepEqual(fake.names(), ['get']);
    await flow.retryInstruments();
    assert.equal(fake.catalogCalls.length, 2);
    assert.equal(flow.state.catalog.status, 'ready');
    await flow.retryInstruments(); // already loaded: not read again
    assert.equal(fake.catalogCalls.length, 2);
  });

  it('catalog errors: a server error without technical text, a 401 ends the session, a 404 is the simulator disabled', async () => {
    const failed = await inCheckout({ instruments: [apiError(500, 'INTERNAL_ERROR', 'Traceback (most recent call last)')] });
    assert.deepEqual(failed.flow.state.catalog, { status: 'error', items: [], error: INSTRUMENTS_ERROR });
    const expired = await inCheckout({ instruments: [apiError(401, 'NOT_AUTHENTICATED')] });
    assert.equal(expired.flow.state.sessionExpired, true);
    assert.deepEqual(expired.timers.delays(), []);
    const disabled = setUpCheckout({ get: [transaction()], instruments: [apiError(404, 'NOT_FOUND')] });
    await disabled.flow.init(31);
    await flush();
    assert.equal(disabled.flow.state.view.kind, 'unavailable');
  });

  it('only an instrument the server listed can be chosen; without a choice nothing is sent', async () => {
    const { flow, fake } = await inCheckout();
    await flow.process(); // nothing chosen
    for (const invented of ['TEST_CARD_UNKNOWN', 'APPROVED', 'test_card_approve', ` ${APPROVE}`, '4242424242424242', '']) {
      flow.selectInstrument(invented);
      assert.equal(flow.state.selectedInstrument, null, invented);
    }
    await flow.process();
    assert.deepEqual(fake.names(), ['get']);
    flow.selectInstrument(FUNDS);
    flow.selectInstrument(APPROVE); // changed before processing: the last choice counts
    assert.equal(flow.state.selectedInstrument, APPROVE);
    assert.ok(canProcess(flow.state));
  });

  it('the instrument is no longer available (422 on instrument_id): its message, the choice dropped, the catalog read again', async () => {
    const message = 'Ese instrumento de prueba ya no está disponible. Selecciona otro.';
    const { flow, fake, timers } = await inCheckout({
      complete: [apiError(422, 'VALIDATION_ERROR', 'La solicitud no es válida.', [{ field: 'instrument_id', message }])],
      instruments: [{ items: CATALOG }, { items: [CATALOG[0], CATALOG[2]] }] // the backend withdrew it meanwhile
    });
    await pay(flow, FUNDS);
    await flush();
    assert.deepEqual(flow.state.notice, { tone: 'error', text: message });
    assert.equal(flow.state.selectedInstrument, null);
    assert.equal(fake.catalogCalls.length, 2);
    assert.deepEqual(flow.state.catalog.items.map((i) => i.id), [APPROVE, TECHNICAL]);
    assert.equal(checkoutStep(flow.state), 'select');
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]); // still followed
    flow.selectInstrument(FUNDS); // no longer listed: cannot be chosen
    assert.equal(flow.state.selectedInstrument, null);
  });
});

describe('demo checkout: processing with the chosen instrument', () => {
  it('sends exactly the chosen instrument\'s id, never a status or a result', async () => {
    const { flow, fake } = await inCheckout({ complete: [APPROVED] });
    await pay(flow, TECHNICAL);
    assert.deepEqual(fake.calls[1], ['complete', 31, TECHNICAL]);
    assert.equal(fake.calls[1].length, 3); // the transaction and the id: nothing else reaches the API
  });

  it('the processing view at once; never inferred: INITIATED, without payment or receipt, until the server answers', async () => {
    const action = deferred<DemoTransaction>();
    const { flow, states } = await inCheckout({ complete: [action.answer] });
    const pending = pay(flow, APPROVE); // the instrument that simulates an approval
    assert.equal(checkoutStep(flow.state), 'processing'); // immediately
    assert.equal(flow.state.busy, 'processing');
    assert.equal(shown(flow).status, 'INITIATED');
    assert.equal(shown(flow).payment_id, null);
    assert.equal(receiptLinkOf(shown(flow)), null);
    assert.ok(!canProcess(flow.state) && !canCancel(flow.state) && !canChooseInstrument(flow.state));
    action.resolve(APPROVED);
    await pending;
    assert.equal(shown(flow).status, 'APPROVED'); // now, from the server
    assert.equal(checkoutStep(flow.state), 'result');
    // No state ever showed an approval before the server's answer.
    const answeredAt = states.findIndex((s) => s.view.kind === 'transaction' && s.view.transaction.status === 'APPROVED');
    assert.ok(states.slice(0, answeredAt).every((s) => s.view.kind !== 'transaction' || s.view.transaction.status === 'INITIATED'));
  });

  it(`a fast answer: the processing view lasts ${MIN_PROCESSING_MS} ms, without delaying the request or changing its result`, async () => {
    const { flow, fake, timers } = await inCheckout({ complete: [REJECTED] }, { now: NOW }, MIN_PROCESSING_MS);
    const pending = pay(flow, FUNDS);
    assert.deepEqual(fake.calls.at(-1), ['complete', 31, FUNDS]); // sent at once
    await flush();
    assert.deepEqual(timers.delays(), [MIN_PROCESSING_MS]); // only the wait of the view (the polling stopped)
    assert.equal(checkoutStep(flow.state), 'processing');
    await timers.fire();
    await pending;
    assert.deepEqual(shown(flow), REJECTED); // exactly the server's answer
  });

  it('a slow answer: no second wait', async () => {
    const clock = { now: NOW };
    const action = deferred<DemoTransaction>();
    const { flow, timers } = await inCheckout({ complete: [action.answer] }, clock, MIN_PROCESSING_MS);
    const pending = pay(flow, APPROVE);
    clock.now += 600; // the server took longer than the minimum
    action.resolve(APPROVED);
    await pending;
    assert.equal(shown(flow).status, 'APPROVED');
    assert.deepEqual(timers.delays(), []);
  });

  it('leaving the page during the wait: no timer stays active, nothing is rendered', async () => {
    const { flow, timers, states } = await inCheckout({ complete: [APPROVED] }, { now: NOW }, MIN_PROCESSING_MS);
    void pay(flow, APPROVE);
    await flush();
    assert.deepEqual(timers.delays(), [MIN_PROCESSING_MS]);
    const rendered = states.length;
    flow.dispose();
    assert.deepEqual(timers.delays(), []);
    assert.equal(states.length, rendered);
  });

  it('APPROVED: the result, the receipt of the persisted payment, the instrument; the obligation read again', async () => {
    const { flow, fake, timers } = await inCheckout({ complete: [APPROVED], list: [list(obligation()), list()] });
    await pay(flow, APPROVE);
    await flush();
    assert.equal(shown(flow).status, 'APPROVED');
    assert.equal(resultReasonText(shown(flow)), null);
    assert.equal(receiptLinkOf(shown(flow)), '/api/v1/resident/payments/12/receipt');
    assert.equal(paymentPath(shown(flow).payment_id!), '/resident/payments/12');
    assert.equal(fake.listCalls.length, 2);
    assert.equal(flow.state.listed, false); // paid in full: no longer listed
    assert.deepEqual(timers.delays(), []);
    assert.equal(flow.state.polling, false);
    assert.ok(!canRetryWithAnother(flow.state));
  });

  for (const [instrumentId, answered, reason] of [[FUNDS, REJECTED, /^Motivo simulado: fondos insuficientes/],
    [TECHNICAL, FAILED, /^Motivo simulado: error técnico/]] as const) {
    it(`${answered.status} (${instrumentId}): shown as the server answered it, with its simulated reason; nothing is paid`, async () => {
      const { flow, fake } = await inCheckout({ complete: [answered] });
      await pay(flow, instrumentId);
      await flush();
      assert.deepEqual(shown(flow), answered);
      assert.deepEqual(fake.calls[1], ['complete', 31, instrumentId]);
      assert.match(resultReasonText(shown(flow))!, reason);
      assert.equal(receiptLinkOf(shown(flow)), null);
      assert.equal(flow.state.obligation?.outstanding_amount, '100000.00');
      assert.equal(flow.state.selectedInstrument, null); // a result keeps no choice: nothing to process again
      assert.ok(canRetryWithAnother(flow.state));
    });
  }

  it('the result is the server\'s, never the instrument chosen', async () => {
    // The resident chose the approval instrument, but the transaction had already been rejected elsewhere.
    const message = 'La transacción ya terminó y no admite cambios.';
    const { flow } = await inCheckout({ complete: [apiError(409, 'DEMO_TRANSACTION_FINISHED', message)], get: [REJECTED] });
    await pay(flow, APPROVE);
    await flush();
    assert.equal(shown(flow).status, 'REJECTED');
    assert.equal(shown(flow).test_instrument, FUNDS); // the one the server stored, not the one chosen here
    assert.deepEqual(flow.state.notice, { tone: 'info', text: FINISHED_MESSAGE });
  });

  it('409 DEMO_PAYMENT_NOT_APPLIED: the message, then the transaction read again (FAILED, NOT_APPLIED, no receipt, no retry)', async () => {
    const message = 'No fue posible completar la transacción. El pago no fue aplicado y tu cuenta no cambió.';
    const { flow, fake } = await inCheckout({ complete: [apiError(409, 'DEMO_PAYMENT_NOT_APPLIED', message)], get: [NOT_APPLIED] });
    await pay(flow, APPROVE);
    await flush();
    assert.deepEqual(fake.names(), ['get', 'complete', 'get']);
    assert.equal(shown(flow).status, 'FAILED'); // although the instrument simulates an approval
    assert.equal(resultKind(shown(flow)), 'not-applied');
    assert.equal(receiptLinkOf(shown(flow)), null);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: message });
    assert.ok(!canRetryWithAnother(flow.state)); // the obligation has to be reviewed on its page
  });

  it('409 DEMO_TRANSACTION_EXPIRED: read again, EXPIRED shown, never paid', async () => {
    const message = 'La transacción venció. El pago no fue aplicado; inicia uno nuevo si aún quieres pagar.';
    const { flow } = await inCheckout({ complete: [apiError(409, 'DEMO_TRANSACTION_EXPIRED', message)], get: [ended('EXPIRED')] });
    await pay(flow, APPROVE);
    await flush();
    assert.equal(shown(flow).status, 'EXPIRED');
    assert.deepEqual(flow.state.notice, { tone: 'info', text: message });
  });

  it('no answer (network): the transaction is read at once, never sent again; the server decides', async () => {
    const { flow, fake } = await inCheckout({ complete: [new TypeError('Failed to fetch')], get: [APPROVED] });
    await pay(flow, APPROVE);
    await flush();
    assert.deepEqual(fake.names(), ['get', 'complete', 'get']); // one completion only
    assert.equal(shown(flow).status, 'APPROVED'); // it had been applied
    assert.deepEqual(flow.state.notice, { tone: 'info', text: UNCONFIRMED_CHECKED });
    assert.equal(flow.state.mustCheck, false);
  });

  it('no answer and the reading fails too: uncertain, only "Consultar estado", then the server decides', async () => {
    const { flow, fake } = await inCheckout({ complete: [new TypeError('Failed to fetch')], get: [new TypeError('offline'), APPROVED] });
    await pay(flow, APPROVE);
    assert.equal(flow.state.mustCheck, true);
    assert.equal(checkoutStep(flow.state), 'uncertain');
    assert.deepEqual(flow.state.notice, { tone: 'error', text: UNCONFIRMED_MESSAGE });
    assert.ok(!canProcess(flow.state) && !canCancel(flow.state));
    await pay(flow, APPROVE); // not sent again blindly
    await flow.cancel();
    assert.deepEqual(fake.names(), ['get', 'complete', 'get']);
    await flow.refresh();
    await flush();
    assert.equal(flow.state.mustCheck, false);
    assert.equal(shown(flow).status, 'APPROVED');
  });

  it('403 or 422 while processing: the message, the transaction stays in the choice step', async () => {
    const { flow, timers } = await inCheckout({ complete: [apiError(403, 'CSRF_FAILED')] });
    await pay(flow, APPROVE);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: CSRF_MESSAGE });
    assert.equal(checkoutStep(flow.state), 'select');
    assert.equal(flow.state.selectedInstrument, APPROVE);
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]); // still followed
  });

  it('404 while processing: the transaction is not the resident\'s (or is gone): "no existe"', async () => {
    const { flow } = await inCheckout({ complete: [apiError(404, 'DEMO_TRANSACTION_NOT_FOUND')] });
    await pay(flow, APPROVE);
    assert.equal(flow.state.view.kind, 'not-found');
  });

  it('401 while processing: session expired, every action stops', async () => {
    const { flow, timers } = await inCheckout({ complete: [apiError(401, 'NOT_AUTHENTICATED')] });
    await pay(flow, APPROVE);
    assert.equal(flow.state.sessionExpired, true);
    assert.deepEqual(timers.delays(), []);
    assert.equal(flow.state.polling, false);
  });

  it('one action at a time: a second processing, another choice or a cancel while one is sent does nothing', async () => {
    const action = deferred<DemoTransaction>();
    const { flow, fake } = await inCheckout({ complete: [action.answer] });
    const first = pay(flow, APPROVE);
    await pay(flow, APPROVE);
    await pay(flow, FUNDS);
    await flow.cancel();
    await flow.refresh();
    assert.equal(flow.state.busy, 'processing');
    assert.equal(flow.state.selectedInstrument, APPROVE); // the choice cannot change while it is sent
    action.resolve(APPROVED);
    await first;
    assert.deepEqual(fake.names(), ['get', 'complete']);
  });
});

describe('demo checkout: cancellation', () => {
  it('confirmed by the server: back to the obligations, which say so', async () => {
    const { flow, fake, navigated, timers } = await inCheckout({ cancel: [ended('CANCELLED')] });
    await flow.cancel();
    assert.deepEqual(fake.calls[1], ['cancel', 31]);
    assert.deepEqual(navigated, [CANCELLED_PATH]);
    assert.equal(flow.state.busy, 'opening');
    assert.deepEqual(timers.delays(), []);
  });

  it('already finished: its real result is shown, never a cancellation assumed', async () => {
    const { flow, navigated } = await inCheckout({ cancel: [apiError(409, 'DEMO_TRANSACTION_FINISHED', 'x')], get: [APPROVED] });
    await flow.cancel();
    await flush();
    assert.equal(shown(flow).status, 'APPROVED');
    assert.deepEqual(flow.state.notice, { tone: 'info', text: FINISHED_MESSAGE });
    assert.deepEqual(navigated, []);
  });

  it('already expired: EXPIRED is shown', async () => {
    const message = 'La transacción venció.';
    const { flow, navigated } = await inCheckout({ cancel: [apiError(409, 'DEMO_TRANSACTION_EXPIRED', message)], get: [ended('EXPIRED')] });
    await flow.cancel();
    assert.equal(shown(flow).status, 'EXPIRED');
    assert.deepEqual(flow.state.notice, { tone: 'info', text: message });
    assert.deepEqual(navigated, []);
  });

  it('twice: one request', async () => {
    const action = deferred<DemoTransaction>();
    const { flow, fake } = await inCheckout({ cancel: [action.answer] });
    const first = flow.cancel();
    await flow.cancel();
    assert.equal(flow.state.busy, 'cancelling');
    action.resolve(ended('CANCELLED'));
    await first;
    assert.deepEqual(fake.names(), ['get', 'cancel']);
  });
});

describe('demo checkout: trying another instrument (a new transaction)', () => {
  it('after a rejection: a NEW transaction for the same obligation and amount, then its checkout', async () => {
    const { flow, fake, navigated } = await inCheckout({ complete: [REJECTED], start: [transaction({ id: 32 })] });
    await pay(flow, FUNDS);
    assert.ok(canRetryWithAnother(flow.state));
    await flow.retryWithAnother();
    assert.deepEqual(fake.names(), ['get', 'complete', 'start']); // never the finished one again
    assert.deepEqual(fake.calls.at(-1), ['start', 7, '40000.50']);
    assert.deepEqual(navigated, ['/resident/demo-payments/checkout?transaction=32']);
    assert.equal(flow.state.busy, 'opening');
    await flow.retryWithAnother(); // a double click: one transaction
    assert.equal(fake.names().filter((n) => n === 'start').length, 1);
  });

  it('only after a simulated rejection or error: never INITIATED, approved, cancelled, expired or NOT_APPLIED', async () => {
    const live = await inCheckout();
    assert.ok(!canRetryWithAnother(live.flow.state));
    await live.flow.retryWithAnother();
    assert.ok(!live.fake.names().includes('start'));
    for (const finished of [APPROVED, ended('CANCELLED'), ended('EXPIRED'), NOT_APPLIED]) {
      const { flow } = setUpCheckout({ get: [finished] });
      await flow.init(31);
      assert.ok(!canRetryWithAnother(flow.state), finished.status);
    }
    for (const finished of [REJECTED, FAILED, ended('FAILED')]) {
      const { flow } = setUpCheckout({ get: [finished] });
      await flow.init(31);
      assert.ok(canRetryWithAnother(flow.state), finished.status);
    }
  });

  it('the server refuses the new one: its message (amount above the new balance, not payable, gone), and nothing opened', async () => {
    const cases: [Error, string][] = [
      [apiError(422, 'VALIDATION_ERROR', 'x', [{ field: 'amount', message: 'El monto no puede superar el saldo pendiente.' }]),
        'El monto no puede superar el saldo pendiente.'],
      [apiError(409, 'CHARGE_NOT_PAYABLE', 'La obligación no tiene saldo pendiente por pagar.'), 'La obligación no tiene saldo pendiente por pagar.'],
      [apiError(404, 'CHARGE_NOT_FOUND', 'x'), OBLIGATION_GONE],
      [new TypeError('Failed to fetch'), CONNECTION_ERROR]
    ];
    for (const [error, text] of cases) {
      const { flow, navigated } = setUpCheckout({ get: [REJECTED], start: [error] });
      await flow.init(31);
      await flow.retryWithAnother();
      assert.deepEqual(flow.state.notice, { tone: 'error', text }, error.message);
      assert.deepEqual(navigated, []);
      assert.equal(flow.state.busy, null);
    }
  });

  it('one already in progress (another tab): its checkout, never a second one', async () => {
    const { flow, navigated } = setUpCheckout({
      get: [REJECTED], start: [apiError(409, 'DEMO_PAYMENT_IN_PROGRESS', 'Ya hay un pago en curso')],
      list: [list(obligation()), list(obligation({ in_progress_transaction_id: 33 }))]
    });
    await flow.init(31);
    await flow.retryWithAnother();
    assert.deepEqual(navigated, ['/resident/demo-payments/checkout?transaction=33&resumed=1']);
  });
});

describe('demo checkout: expiry and status reading', () => {
  it('reads the status every 20 s while INITIATED and stops at a terminal status', async () => {
    const { flow, fake, timers } = await inCheckout({ get: [transaction(), ended('CANCELLED')] });
    await timers.fire();
    assert.deepEqual(fake.calls.at(-1), ['get', 31]);
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]);
    await timers.fire(); // cancelled in another tab
    await flush();
    assert.equal(shown(flow).status, 'CANCELLED');
    assert.deepEqual(timers.delays(), []); // no more reading
  });

  it('expired on screen: the server is asked at expires_at (plus a margin) and its EXPIRED is shown', async () => {
    const clock = { now: Date.parse('2026-10-09T20:14:50Z') };
    const { flow, timers } = await inCheckout({ get: [ended('EXPIRED', { finished_at: '2026-10-09T20:15:00Z' })] }, clock);
    assert.deepEqual(timers.delays(), [10_000 + EXPIRY_GRACE_MS]); // sooner than the interval
    clock.now = Date.parse('2026-10-09T20:15:02Z');
    await timers.fire();
    await flush();
    assert.equal(shown(flow).status, 'EXPIRED');
    assert.equal(transactionHeading(shown(flow)).title, 'Transacción vencida');
  });

  it('the browser clock ahead of the server: one reading at the local expiry, then the normal interval', async () => {
    // The browser believes the transaction expired 5 minutes ago; the server still has it INITIATED.
    const clock = { now: Date.parse('2026-10-09T20:20:00Z') };
    const { flow, fake, timers } = await inCheckout({ get: [transaction(), transaction(), transaction(), ended('EXPIRED')] }, clock);
    const scheduled = [...timers.delays()];
    for (let reading = 0; reading < 4; reading += 1) {
      await timers.fire();
      await flush();
      scheduled.push(...timers.delays());
    }
    // Asked once right away (the local clock says it expired), then every 20 s while the server says INITIATED.
    assert.deepEqual(scheduled, [0, POLL_INTERVAL_MS, POLL_INTERVAL_MS, POLL_INTERVAL_MS]);
    assert.equal(fake.names().filter((name) => name === 'get').length, 5); // the first reading, then one per timer
    assert.equal(shown(flow).status, 'EXPIRED'); // the server decides
    assert.deepEqual(timers.delays(), []);
  });

  it('the reading aimed at the expiry answers INITIATED (server clock behind): the next one waits the interval', async () => {
    const clock = { now: Date.parse('2026-10-09T20:14:50Z') };
    const { fake, timers } = await inCheckout({ get: [transaction(), transaction()] }, clock);
    assert.deepEqual(timers.delays(), [10_000 + EXPIRY_GRACE_MS]); // aimed at expires_at + margin
    clock.now = Date.parse('2026-10-09T20:15:02Z');
    await timers.fire();
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]); // not 0, although the local clock is past the expiry
    clock.now += POLL_INTERVAL_MS;
    await timers.fire();
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]);
    assert.equal(fake.names().filter((name) => name === 'get').length, 3);
  });

  it('expired on screen: the instrument is not sent before asking the server', async () => {
    const clock = { now: NOW };
    const { flow, fake } = await inCheckout({ get: [transaction()], complete: [REJECTED] }, clock);
    clock.now = Date.parse('2026-10-09T20:15:00Z'); // the local clock reached expires_at
    await pay(flow, FUNDS);
    assert.deepEqual(fake.calls.at(-1), ['get', 31]); // asked first, nothing sent
    // The server (its clock decides) still has it INITIATED: the choice is kept and now it is sent.
    await pay(flow, FUNDS);
    await flush();
    assert.deepEqual(fake.calls.find(([n]) => n === 'complete'), ['complete', 31, FUNDS]);
  });

  it('never indefinitely: at most MAX_POLLS automatic reads, then "Consultar estado"', async () => {
    const far = () => transaction({ expires_at: '2026-10-10T20:00:00Z' });
    const setup = setUpCheckout({ get: [far(), ...Array.from({ length: MAX_POLLS }, far)] });
    await setup.flow.init(31);
    // A transaction far from expiring (the server's clock is ahead): polling still ends.
    for (let i = 0; i < MAX_POLLS; i += 1) await setup.timers.fire();
    assert.equal(setup.fake.names().filter((n) => n === 'get').length, MAX_POLLS + 1);
    assert.deepEqual(setup.timers.delays(), []);
    assert.equal(setup.flow.state.polling, false);
    assert.deepEqual(setup.flow.state.notice, { tone: 'info', text: POLLING_STOPPED });
  });

  it('a failed read stops the polling and asks the resident to check', async () => {
    const { flow, timers } = await inCheckout({ get: [new TypeError('offline')] });
    await timers.fire();
    assert.deepEqual(timers.delays(), []);
    assert.equal(flow.state.mustCheck, true);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: CHECK_ERROR });
  });

  it('a 401 while reading stops everything', async () => {
    const { flow, timers } = await inCheckout({ get: [apiError(401, 'NOT_AUTHENTICATED')] });
    await timers.fire();
    assert.equal(flow.state.sessionExpired, true);
    assert.deepEqual(timers.delays(), []);
  });

  it('leaving the page stops the timers and the renders', async () => {
    const { flow, timers, states } = await inCheckout();
    const rendered = states.length;
    flow.dispose();
    assert.deepEqual(timers.delays(), []);
    await flow.refresh();
    assert.equal(states.length, rendered);
  });
});

describe('demo checkout: an older answer never overwrites a newer one', () => {
  const actions: [string, DemoTransaction, (flow: CheckoutFlow) => Promise<void>][] = [
    ['an approval', APPROVED, (flow) => pay(flow, APPROVE)],
    ['a rejection', REJECTED, (flow) => pay(flow, FUNDS)],
    ['a simulated failure', FAILED, (flow) => pay(flow, TECHNICAL)]
  ];

  for (const [name, answered, act] of actions) {
    it(`a reading sent before ${name} answers INITIATED afterwards: the result stays`, async () => {
      const reading = deferred<DemoTransaction>();
      const { flow, timers, fake } = await inCheckout({ get: [reading.answer], complete: [answered] });
      await timers.fire(); // the automatic reading is sent and stays pending
      assert.deepEqual(fake.calls.at(-1), ['get', 31]);
      await act(flow); // the resident's action is answered first
      await flush();
      assert.equal(shown(flow).status, answered.status);

      reading.resolve(transaction()); // the old answer, INITIATED, arrives last
      await flush();
      assert.equal(shown(flow).status, answered.status); // never back to the choice
      assert.deepEqual(timers.delays(), []); // and no reading is scheduled again
      assert.equal(flow.state.polling, false);
    });
  }

  it('an old reading that fails afterwards does not turn the result into an unknown one', async () => {
    const reading = deferred<DemoTransaction>();
    const { flow, timers } = await inCheckout({ get: [reading.answer], complete: [APPROVED] });
    await timers.fire();
    await pay(flow, APPROVE);
    reading.reject(new TypeError('Failed to fetch'));
    await flush();
    assert.equal(shown(flow).status, 'APPROVED');
    assert.equal(flow.state.mustCheck, false);
    assert.equal(flow.state.notice, null);
  });

  it('"Consultar estado" answered first wins over an older automatic reading', async () => {
    const reading = deferred<DemoTransaction>();
    const { flow, timers } = await inCheckout({ get: [reading.answer, ended('CANCELLED')] });
    await timers.fire();
    await flow.refresh(); // cancelled in another tab
    reading.resolve(transaction());
    await flush();
    assert.equal(shown(flow).status, 'CANCELLED');
    assert.deepEqual(timers.delays(), []);
  });

  it('an older list of obligations does not overwrite the one read after the result', async () => {
    const stale = deferred<{ items: PayableObligation[] }>();
    const { flow } = setUpCheckout({ get: [transaction()], complete: [APPROVED], list: [stale.answer, list()] });
    void flow.init(31); // its list stays pending
    await flush();
    await pay(flow, APPROVE); // the result reads the list again: paid in full, no longer listed
    await flush();
    assert.equal(flow.state.listed, false);
    stale.resolve(list(obligation())); // the older list, with the balance before the payment, arrives last
    await flush();
    assert.equal(flow.state.listed, false);
    assert.equal(flow.state.obligation, null);
  });
});

describe('demo checkout: while a processing or a cancellation is pending', () => {
  const actions: [string, DemoTransaction, (flow: CheckoutFlow) => Promise<void>][] = [
    ['approval', APPROVED, (flow) => pay(flow, APPROVE)],
    ['rejection', REJECTED, (flow) => pay(flow, FUNDS)]
  ];

  for (const [name, answered, act] of actions) {
    it(`A. ${name} pending: "Consultar estado" sends no reading, the confirmed answer is shown`, async () => {
      const action = deferred<DemoTransaction>();
      const { flow, fake, timers } = await inCheckout({ complete: [action.answer] });
      const pending = act(flow);
      assert.deepEqual(timers.delays(), []); // the automatic reading stopped with the action
      await flow.refresh();
      await pay(flow, APPROVE); // and no second action either
      await flow.cancel();
      assert.deepEqual(fake.names(), ['get', 'complete']);
      assert.notEqual(flow.state.busy, null);

      action.resolve(answered);
      await pending;
      await flush();
      assert.equal(shown(flow).status, answered.status);
      assert.equal(flow.state.busy, null);
      assert.deepEqual(timers.delays(), []);
    });
  }

  it('B. an older reading answers INITIATED while the approval is pending: the approval still decides', async () => {
    const reading = deferred<DemoTransaction>();
    const action = deferred<DemoTransaction>();
    const { flow, fake, timers } = await inCheckout({ get: [reading.answer], complete: [action.answer] });
    await timers.fire(); // the reading is sent
    const pending = pay(flow, APPROVE); // then the approval
    reading.resolve(transaction()); // the older INITIATED arrives first
    await flush();
    assert.equal(flow.state.busy, 'processing'); // still waiting for the approval: nothing re-enabled
    assert.equal(checkoutStep(flow.state), 'processing');
    assert.equal(flow.state.mustCheck, false);
    assert.equal(flow.state.notice, null);
    assert.deepEqual(timers.delays(), []); // no reading scheduled by the old answer

    action.resolve(APPROVED);
    await pending;
    await flush();
    assert.equal(shown(flow).status, 'APPROVED');
    assert.equal(shown(flow).receipt_number, 'RC-00000012');
    assert.deepEqual(fake.names(), ['get', 'get', 'complete']);
  });

  it('C. an older reading fails (network) while the approval is pending: no stale error, the approval decides', async () => {
    const reading = deferred<DemoTransaction>();
    const action = deferred<DemoTransaction>();
    const { flow, timers } = await inCheckout({ get: [reading.answer], complete: [action.answer] });
    await timers.fire();
    const pending = pay(flow, APPROVE);
    reading.reject(new TypeError('Failed to fetch')); // the older reading fails first
    await flush();
    assert.equal(flow.state.busy, 'processing');
    assert.equal(flow.state.mustCheck, false); // never "unknown" because of an old reading
    assert.equal(flow.state.notice, null); // no stale error shown
    assert.deepEqual(timers.delays(), []);

    action.resolve(APPROVED);
    await pending;
    await flush();
    assert.equal(shown(flow).status, 'APPROVED');
    assert.equal(flow.state.mustCheck, false);
  });

  it('D. no answer, its reading fails, and an older reading answers INITIATED: still uncertain', async () => {
    const reading = deferred<DemoTransaction>();
    const { flow, fake, timers } = await inCheckout({
      get: [reading.answer, new TypeError('offline'), APPROVED], complete: [new TypeError('Failed to fetch')]
    });
    await timers.fire();
    await pay(flow, APPROVE); // no answer, and the state could not be read either
    reading.resolve(transaction()); // the older INITIATED arrives afterwards
    await flush();
    assert.equal(flow.state.mustCheck, true); // the old INITIATED does not settle the doubt
    assert.equal(checkoutStep(flow.state), 'uncertain');
    assert.equal(shown(flow).payment_id, null); // never an approval invented by the page
    assert.deepEqual(timers.delays(), []);

    await pay(flow, APPROVE); // no second payment sent blindly
    await flow.cancel();
    assert.deepEqual(fake.names(), ['get', 'get', 'complete', 'get']);

    await flow.refresh(); // the real state, from the server
    await flush();
    assert.equal(shown(flow).status, 'APPROVED');
    assert.equal(flow.state.mustCheck, false);
  });

  it('E. 409 DEMO_PAYMENT_NOT_APPLIED: an older INITIATED during the re-read is not shown as the result', async () => {
    const reading = deferred<DemoTransaction>();
    const reread = deferred<DemoTransaction>();
    const message = 'No fue posible completar la transacción. El pago no fue aplicado y tu cuenta no cambió.';
    const { flow, fake, timers } = await inCheckout({
      get: [reading.answer, reread.answer], complete: [apiError(409, 'DEMO_PAYMENT_NOT_APPLIED', message)]
    });
    await timers.fire();
    const pending = pay(flow, APPROVE);
    await flush(); // the 409 arrived: the real state is being read (the processing view stays)
    assert.equal(checkoutStep(flow.state), 'processing');
    reading.resolve(transaction()); // the older INITIATED arrives during the re-read
    await flush();
    assert.equal(checkoutStep(flow.state), 'processing'); // still waiting for the real state
    assert.deepEqual(timers.delays(), []);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: message });

    reread.resolve(NOT_APPLIED);
    await pending;
    await flush();
    assert.equal(shown(flow).status, 'FAILED');
    assert.equal(receiptLinkOf(shown(flow)), null);
    assert.deepEqual(fake.names(), ['get', 'get', 'complete', 'get']);
  });

  it('F. "Reintentar" or another instrument while the approval is pending: nothing loaded, the approval decides', async () => {
    const action = deferred<DemoTransaction>();
    const { flow, fake } = await inCheckout({ complete: [action.answer] });
    const pending = pay(flow, APPROVE);
    await flow.retry(); // called on the controller directly (the page shows it only after a load error)
    await flow.retryWithAnother();
    assert.deepEqual(fake.names(), ['get', 'complete']); // no reading, no new transaction
    assert.equal(flow.state.busy, 'processing');
    action.resolve(APPROVED);
    await pending;
    assert.equal(shown(flow).status, 'APPROVED');
  });
});
