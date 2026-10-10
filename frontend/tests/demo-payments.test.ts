/**
 * HU-010-D: the resident's demo payment flow — amounts (exact text, compared in cents), labels,
 * the list, the form, the review, the start, the three simulated outcomes, the cancellation, the
 * expiry, the bounded polling, resuming from the URL and every error. The API, the clock and the
 * timers are fakes injected into the controller (no server). Run with `npm test`. The money rules
 * themselves (balances, allocation, idempotency) are the backend's and are tested there against MySQL.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError, type ApiErrorBody } from '../src/lib/api/errors.ts';
import type { DemoOutcome, DemoTransaction, PayableObligation } from '../src/lib/api/demo-payments.ts';
import { CONNECTION_ERROR, CSRF_MESSAGE } from '../src/lib/obligations/charge-format.ts';
import { METHODS, METHOD_LABELS } from '../src/lib/payments/payment-form.ts';
import {
  CHECK_ERROR,
  DEMO_NOTICE,
  EMPTY_MESSAGE,
  EXPIRY_GRACE_MS,
  FINISHED_MESSAGE,
  FORBIDDEN_MESSAGE,
  INVALID_MESSAGE,
  LOAD_ERROR,
  MAX_POLLS,
  OBLIGATION_GONE,
  OUTCOMES,
  OUTCOME_LABELS,
  POLLING_STOPPED,
  POLL_INTERVAL_MS,
  RESULT_TEXTS,
  RESUMED_IN_PROGRESS,
  SESSION_EXPIRED_MESSAGE,
  TRANSACTION_MISSING,
  TRANSACTION_STATUS_LABELS,
  UNCONFIRMED_MESSAGE,
  amountInputText,
  createDemoPaymentFlow,
  isFullPayment,
  isUnavailable,
  obligationLines,
  obligationTitle,
  parseTransactionParam,
  paymentPath,
  receiptLinkOf,
  toCents,
  transactionHeading,
  transactionLines,
  validateAmount,
  type DemoState
} from '../src/lib/resident/demo-payment.ts';

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
    receipt_number: null, ...overrides
  };
}

const ended = (status: DemoTransaction['status'], extra: Partial<DemoTransaction> = {}) =>
  transaction({ status, finished_at: '2026-10-09T20:03:00Z', ...extra });
const APPROVED = ended('APPROVED', { payment_id: 12, receipt_number: 'RC-00000012' });

type Answer<T> = T | Error | (() => Promise<T>);

/** A fake API: each method answers from its queue (a value, an error, or a pending promise) and logs its calls. */
function fakeApi(queues: {
  list?: Answer<{ items: PayableObligation[] }>[];
  start?: Answer<DemoTransaction>[];
  get?: Answer<DemoTransaction>[];
  complete?: Answer<DemoTransaction>[];
  cancel?: Answer<DemoTransaction>[];
}) {
  const calls: [string, ...unknown[]][] = [];
  const answer = async <T>(name: string, queue: Answer<T>[] | undefined): Promise<T> => {
    const next = queue?.shift();
    if (next === undefined) throw new Error(`unexpected call: ${name}`);
    if (next instanceof Error) throw next;
    if (typeof next === 'function') return (next as () => Promise<T>)();
    return next;
  };
  return {
    calls,
    names: () => calls.map(([name]) => name),
    api: {
      list: () => (calls.push(['list']), answer('list', queues.list)),
      start: (chargeId: number, amount: string) => (calls.push(['start', chargeId, amount]), answer('start', queues.start)),
      get: (id: number) => (calls.push(['get', id]), answer('get', queues.get)),
      complete: (id: number, outcome: DemoOutcome) => (calls.push(['complete', id, outcome]), answer('complete', queues.complete)),
      cancel: (id: number) => (calls.push(['cancel', id]), answer('cancel', queues.cancel))
    }
  };
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

function setUp(queues: Parameters<typeof fakeApi>[0], clock = { now: NOW }) {
  const fake = fakeApi(queues);
  const timers = fakeTimers();
  const states: DemoState[] = [];
  const remembered: (number | null)[] = [];
  const flow = createDemoPaymentFlow({
    api: fake.api,
    render: (state) => states.push(state),
    remember: (id) => remembered.push(id),
    now: () => clock.now,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer
  });
  return { flow, fake, timers, states, remembered, clock };
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

/** A flow already in the gateway with an INITIATED transaction of 40.000,50. */
async function inGateway(queues: Parameters<typeof fakeApi>[0] = {}, clock = { now: NOW }) {
  const setup = setUp({ ...queues, list: [list(obligation()), ...(queues.list ?? [])], start: [transaction(), ...(queues.start ?? [])] }, clock);
  await setup.flow.init(null);
  setup.flow.select(7);
  setup.flow.review('40000,50');
  await setup.flow.start();
  assert.equal(setup.flow.state.view.kind, 'transaction');
  return setup;
}

// --- Amounts ------------------------------------------------------------------------------------------------

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
});

// --- Labels and display ----------------------------------------------------------------------------------

describe('demo payment: what the resident reads', () => {
  it('the permanent notice: an academic demo, no real money, no external provider, no card data', () => {
    assert.match(DEMO_NOTICE, /simulación interna/);
    assert.match(DEMO_NOTICE, /no se realiza ningún cobro real/);
    assert.match(DEMO_NOTICE, /no se mueve dinero/);
    assert.match(DEMO_NOTICE, /pasarela ni con un proveedor financiero externo/);
    assert.match(DEMO_NOTICE, /Nunca se solicitan datos de tarjetas/);
  });

  it('three explicit, different simulated outcomes', () => {
    assert.deepEqual(OUTCOMES, ['APPROVED', 'REJECTED', 'FAILED']);
    assert.deepEqual(Object.values(OUTCOME_LABELS), ['Simular aprobación', 'Simular rechazo', 'Simular error de procesamiento']);
  });

  it('every terminal status is explained in words (never color alone) and only APPROVED says it was registered', () => {
    assert.deepEqual(TRANSACTION_STATUS_LABELS, {
      INITIATED: 'En curso', APPROVED: 'Aprobado', REJECTED: 'Rechazado', FAILED: 'Fallido', CANCELLED: 'Cancelado',
      EXPIRED: 'Vencido'
    });
    assert.match(RESULT_TEXTS.APPROVED.text, /registró en tu historial/);
    assert.match(RESULT_TEXTS.APPROVED.text, /No se movió dinero real/);
    for (const status of ['REJECTED', 'FAILED', 'CANCELLED', 'EXPIRED'] as const) {
      assert.match(RESULT_TEXTS[status].text, /No se (aplicó|registró) ningún pago/, status);
    }
    assert.match(RESULT_TEXTS.REJECTED.text, /sigue pendiente/);
    assert.match(RESULT_TEXTS.FAILED.text, /no debe considerarse pagada/);
    assert.equal(transactionHeading(transaction()).title, 'Transacción de demostración en curso');
    assert.equal(transactionHeading(APPROVED).title, 'Pago aprobado y registrado');
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

  it('a transaction: reference, amount, obligation and expiry; once approved, its number in the history (no document)', () => {
    const live = transactionLines(transaction(), obligation());
    assert.deepEqual(live.map((l) => l.label), ['Referencia de demostración', 'Monto', 'Obligación', 'Iniciada', 'Vence']);
    assert.equal(spaces(live[1].value), '$ 40.000,50');
    assert.match(spaces(live[4].value), /^9 de octubre de 2026.* 3:15 p\. m\.$/); // Bogotá time
    const approved = transactionLines(APPROVED, null);
    assert.deepEqual(approved.map((l) => l.label), ['Referencia de demostración', 'Monto', 'Iniciada', 'Finalizada',
      'Número del pago en tu historial']);
    assert.ok(approved.every((l) => !/pdf|descarg/i.test(`${l.label} ${l.value}`)));
    assert.ok(transactionLines(ended('REJECTED'), null).every((l) => !l.label.startsWith('Número')));
    assert.equal(paymentPath(12), '/resident/payments/12');
  });

  it('the receipt link: only for an APPROVED answer with the persisted payment id', () => {
    assert.equal(receiptLinkOf(APPROVED), '/api/v1/resident/payments/12/receipt'); // the payment's id, not the transaction's (31)
    for (const status of ['INITIATED', 'REJECTED', 'FAILED', 'CANCELLED', 'EXPIRED'] as const) {
      assert.equal(receiptLinkOf(status === 'INITIATED' ? transaction() : ended(status)), null, status);
    }
    assert.equal(receiptLinkOf({ ...APPROVED, payment_id: null }), null); // never invented
    // 409 DEMO_PAYMENT_NOT_APPLIED ends FAILED without a payment: no receipt either.
    assert.equal(receiptLinkOf(ended('FAILED', { payment_id: null })), null);
  });

  it('the transaction id of the URL: a positive integer only', () => {
    assert.equal(parseTransactionParam('?transaction=31'), 31);
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

// --- The list -----------------------------------------------------------------------------------------------

describe('demo payment: obligations', () => {
  it('loading, then the list the server sent', async () => {
    const { flow, states, fake } = setUp({ list: [list(obligation(), obligation({ charge_id: 8 }))] });
    await flow.init(null);
    assert.equal(states[0].view.kind, 'loading');
    assert.equal(states[0].busy, 'loading');
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.obligations.map((o) => o.charge_id), [7, 8]);
    assert.deepEqual(fake.names(), ['list']);
  });

  it('no obligations: an empty list (the page says so)', async () => {
    const { flow } = setUp({ list: [list()] });
    await flow.init(null);
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.obligations, []);
    assert.equal(EMPTY_MESSAGE, 'No tienes obligaciones pendientes por pagar.');
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
      const { flow } = setUp({ list: [error] });
      await flow.init(31); // the transaction of the URL is not read when the list could not be
      for (const [key, value] of Object.entries(expected)) assert.deepEqual(flow.state[key as keyof DemoState], value, error.message);
      assert.equal(flow.state.busy, null);
    }
  });

  it('retry loads the list again', async () => {
    const { flow } = setUp({ list: [new TypeError('offline'), list(obligation())] });
    await flow.init(null);
    assert.equal(flow.state.view.kind, 'load-error');
    await flow.retry();
    assert.equal(flow.state.view.kind, 'list');
  });

  it('a 404 of the route is "not available", a 404 of a resource is not', () => {
    assert.ok(isUnavailable(apiError(404, 'NOT_FOUND')));
    assert.ok(!isUnavailable(apiError(404, 'CHARGE_NOT_FOUND')));
    assert.ok(!isUnavailable(apiError(404, 'DEMO_TRANSACTION_NOT_FOUND')));
  });
});

// --- Form and review ------------------------------------------------------------------------------------------

describe('demo payment: form and review', () => {
  it('selecting an obligation opens the form with its outstanding balance', async () => {
    const { flow } = setUp({ list: [list(obligation({ applied_amount: '59999.75', outstanding_amount: '40000.25', status: 'PARTIAL' }))] });
    await flow.init(null);
    flow.select(7);
    assert.deepEqual(flow.state.view, { kind: 'form', obligation: flow.state.obligations[0], amountText: '40000,25', error: null });
  });

  it('only an obligation of the list with a balance can be selected', async () => {
    const { flow, fake } = setUp({ list: [list(obligation({ outstanding_amount: '0.00', applied_amount: '100000.00' }))] });
    await flow.init(null);
    flow.select(7); // no balance left
    flow.select(999); // not in the list (another resident's, or invented)
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(fake.names(), ['list']);
  });

  it('an invalid amount stays in the form with its error and nothing is sent', async () => {
    const { flow, fake } = setUp({ list: [list(obligation())] });
    await flow.init(null);
    flow.select(7);
    flow.review('100000,01');
    assert.equal(flow.state.view.kind, 'form');
    assert.match(spaces((flow.state.view as { error: string }).error), /no puede superar el saldo pendiente/);
    assert.equal((flow.state.view as { amountText: string }).amountText, '100000,01'); // what was typed is kept
    flow.review('0');
    assert.equal((flow.state.view as { error: string }).error, 'El monto debe ser mayor que cero.');
    assert.deepEqual(fake.names(), ['list']);
  });

  it('a valid amount shows the review; editing returns to the form; back returns to the list (no request)', async () => {
    const { flow, fake } = setUp({ list: [list(obligation()), list(obligation())] });
    await flow.init(null);
    flow.select(7);
    flow.review('40000,5');
    assert.deepEqual(flow.state.view, { kind: 'review', obligation: obligation(), amount: '40000.50' });
    flow.edit();
    assert.deepEqual(flow.state.view, { kind: 'form', obligation: obligation(), amountText: '40000,50', error: null });
    await flow.back();
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(fake.names(), ['list', 'list']); // never a start
  });
});

// --- Start --------------------------------------------------------------------------------------------------------

describe('demo payment: start', () => {
  it('sends exactly the obligation and the amount text, then shows the reference and the expiry', async () => {
    const { flow, fake, remembered, timers } = await inGateway();
    assert.deepEqual(fake.calls.find(([name]) => name === 'start'), ['start', 7, '40000.50']); // a string, never a number
    const view = flow.state.view as { transaction: DemoTransaction; obligation: PayableObligation };
    assert.equal(view.transaction.reference, 'DEMO-0123456789ABCDEF');
    assert.equal(view.transaction.expires_at, '2026-10-09T20:15:00Z');
    assert.deepEqual(view.obligation, obligation()); // the obligation chosen, for the panel
    assert.deepEqual(remembered.at(-1), 31); // the id in the URL, to resume after a reload
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]);
    assert.equal(flow.state.polling, true);
  });

  it('a full payment sends the balance as typed by default', async () => {
    const { flow, fake } = setUp({ list: [list(obligation())], start: [transaction({ amount: '100000.00' })] });
    await flow.init(null);
    flow.select(7);
    flow.review((flow.state.view as { amountText: string }).amountText);
    await flow.start();
    assert.deepEqual(fake.calls.at(-1), ['start', 7, '100000']);
  });

  it('a double click starts once: everything is disabled while it is sent', async () => {
    let release!: (value: DemoTransaction) => void;
    const { flow, fake, states } = setUp({
      list: [list(obligation())], start: [() => new Promise<DemoTransaction>((resolve) => { release = resolve; })]
    });
    await flow.init(null);
    flow.select(7);
    flow.review('1000');
    const first = flow.start();
    const second = flow.start();
    assert.equal(flow.state.busy, 'starting');
    flow.complete('APPROVED'); // ignored: nothing started yet
    flow.select(7);
    release(transaction());
    await Promise.all([first, second]);
    assert.deepEqual(fake.names(), ['list', 'start']);
    assert.ok(states.some((s) => s.busy === 'starting'));
    assert.equal(flow.state.busy, null);
  });

  it('the backend validates again: its amount error is shown next to the field', async () => {
    const error = apiError(422, 'VALIDATION_ERROR', 'Revisa', [{ field: 'amount', message: 'El monto no puede superar el saldo pendiente de la obligación ($ 30.000,00).' }]);
    const { flow } = setUp({ list: [list(obligation())], start: [error] });
    await flow.init(null);
    flow.select(7);
    flow.review('40000');
    await flow.start();
    assert.deepEqual(flow.state.view, { kind: 'form', obligation: obligation(), amountText: '40000',
      error: 'El monto no puede superar el saldo pendiente de la obligación ($ 30.000,00).' });
  });

  async function startFails(error: Error, more: Parameters<typeof fakeApi>[0] = {}) {
    const setup = setUp({ list: [list(obligation()), ...(more.list ?? [])], start: [error], get: more.get });
    await setup.flow.init(null);
    setup.flow.select(7);
    setup.flow.review('1000');
    await setup.flow.start();
    return setup;
  }

  it('409 CHARGE_NOT_PAYABLE: the list again, with the backend message', async () => {
    const message = 'La obligación no tiene saldo pendiente por pagar.';
    const { flow, fake } = await startFails(apiError(409, 'CHARGE_NOT_PAYABLE', message), { list: [list()] });
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.obligations, []);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: message });
    assert.deepEqual(fake.names(), ['list', 'start', 'list']);
  });

  it('409 DEMO_PAYMENT_IN_PROGRESS: the resident\'s own live transaction is resumed, never a second one', async () => {
    const own = obligation({ in_progress_transaction_id: 30 });
    const { flow, fake } = await startFails(apiError(409, 'DEMO_PAYMENT_IN_PROGRESS', 'Ya hay un pago en curso'),
      { list: [list(own)], get: [transaction({ id: 30, amount: '1000.00' })] });
    assert.deepEqual(fake.names(), ['list', 'start', 'list', 'get']);
    assert.equal((flow.state.view as { transaction: DemoTransaction }).transaction.id, 30);
    assert.deepEqual(flow.state.notice, { tone: 'info', text: RESUMED_IN_PROGRESS });
  });

  it('409 DEMO_PAYMENT_IN_PROGRESS of someone else: the list with the message', async () => {
    const { flow } = await startFails(apiError(409, 'DEMO_PAYMENT_IN_PROGRESS', 'Ya hay un pago en curso'), { list: [list(obligation())] });
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.notice, { tone: 'error', text: 'Ya hay un pago en curso' });
  });

  it('404 CHARGE_NOT_FOUND: the obligation is no longer there (another resident\'s looks the same)', async () => {
    const { flow } = await startFails(apiError(404, 'CHARGE_NOT_FOUND', 'La obligación no existe.'), { list: [list()] });
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.notice, { tone: 'error', text: OBLIGATION_GONE });
  });

  it('404 NOT_FOUND: the simulator was disabled meanwhile (not a financial error)', async () => {
    const { flow, remembered } = await startFails(apiError(404, 'NOT_FOUND', 'No encontrado'));
    assert.equal(flow.state.view.kind, 'unavailable');
    assert.equal(flow.state.notice, null);
    assert.equal(remembered.at(-1), null);
  });

  it('401: session expired, the review stays and nothing else can be sent', async () => {
    const { flow, fake } = await startFails(apiError(401, 'NOT_AUTHENTICATED'));
    assert.equal(flow.state.view.kind, 'review');
    assert.equal(flow.state.sessionExpired, true);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: SESSION_EXPIRED_MESSAGE });
    await flow.start();
    assert.deepEqual(fake.names(), ['list', 'start']);
  });

  it('403: CSRF or permission, without technical text', async () => {
    assert.deepEqual((await startFails(apiError(403, 'CSRF_FAILED'))).flow.state.notice, { tone: 'error', text: CSRF_MESSAGE });
    assert.deepEqual((await startFails(apiError(403, 'FORBIDDEN'))).flow.state.notice, { tone: 'error', text: FORBIDDEN_MESSAGE });
    assert.deepEqual((await startFails(apiError(422, 'VALIDATION_ERROR', 'x', [{ field: 'charge_id', message: 'x' }]))).flow.state.notice,
      { tone: 'error', text: INVALID_MESSAGE });
    assert.deepEqual((await startFails(new TypeError('Failed to fetch'))).flow.state.notice, { tone: 'error', text: CONNECTION_ERROR });
  });
});

// --- Outcomes and cancellation ---------------------------------------------------------------------------------

describe('demo payment: the simulated outcomes', () => {
  it('APPROVED: the server\'s answer is shown, polling stops and the obligations are read again', async () => {
    const { flow, fake, timers } = await inGateway({ complete: [APPROVED], list: [list()] });
    await flow.complete('APPROVED');
    await flush();
    const view = flow.state.view as { transaction: DemoTransaction };
    assert.equal(view.transaction.status, 'APPROVED');
    assert.equal(view.transaction.receipt_number, 'RC-00000012');
    assert.deepEqual(fake.names(), ['list', 'start', 'complete', 'list']);
    assert.deepEqual(fake.calls[2], ['complete', 31, 'APPROVED']);
    assert.deepEqual(flow.state.obligations, []); // paid in full: no longer listed
    assert.deepEqual(timers.delays(), []);
    assert.equal(flow.state.polling, false);
  });

  for (const outcome of ['REJECTED', 'FAILED'] as const) {
    it(`${outcome}: shown as the server answered it, nothing is paid`, async () => {
      const { flow, fake } = await inGateway({ complete: [ended(outcome)], list: [list(obligation())] });
      await flow.complete(outcome);
      await flush();
      assert.equal((flow.state.view as { transaction: DemoTransaction }).transaction.status, outcome);
      assert.deepEqual(fake.calls[2], ['complete', 31, outcome]);
      assert.equal(flow.state.obligations[0].outstanding_amount, '100000.00');
    });
  }

  it('the result is the server\'s, never the button pressed', async () => {
    // The resident asked for APPROVED, but the transaction had already been rejected elsewhere.
    const message = 'La transacción ya terminó y no admite cambios.';
    const { flow } = await inGateway({ complete: [apiError(409, 'DEMO_TRANSACTION_FINISHED', message)], get: [ended('REJECTED')],
      list: [list(obligation())] });
    await flow.complete('APPROVED');
    await flush();
    assert.equal((flow.state.view as { transaction: DemoTransaction }).transaction.status, 'REJECTED');
    assert.deepEqual(flow.state.notice, { tone: 'info', text: FINISHED_MESSAGE });
  });

  it('409 DEMO_PAYMENT_NOT_APPLIED: the message, then the transaction read again (FAILED, no receipt)', async () => {
    const message = 'No fue posible completar la transacción. El pago no fue aplicado y tu cuenta no cambió.';
    const { flow, fake } = await inGateway({ complete: [apiError(409, 'DEMO_PAYMENT_NOT_APPLIED', message)], get: [ended('FAILED')],
      list: [list(obligation())] });
    await flow.complete('APPROVED');
    await flush();
    assert.deepEqual(fake.names(), ['list', 'start', 'complete', 'get', 'list']);
    const view = flow.state.view as { transaction: DemoTransaction };
    assert.equal(view.transaction.status, 'FAILED');
    assert.equal(view.transaction.payment_id, null);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: message });
    assert.ok(transactionLines(view.transaction, null).every((line) => !line.label.startsWith('Número')));
  });

  it('cancel: CANCELLED as the server answers; nothing is applied', async () => {
    const { flow, fake } = await inGateway({ cancel: [ended('CANCELLED')], list: [list(obligation())] });
    await flow.cancel();
    await flush();
    assert.deepEqual(fake.calls[2], ['cancel', 31]);
    assert.equal((flow.state.view as { transaction: DemoTransaction }).transaction.status, 'CANCELLED');
  });

  it('one action at a time: a second outcome or a cancel while one is sent does nothing', async () => {
    let release!: (value: DemoTransaction) => void;
    const { flow, fake } = await inGateway({ complete: [() => new Promise<DemoTransaction>((resolve) => { release = resolve; })],
      list: [list()] });
    const first = flow.complete('APPROVED');
    await flow.complete('APPROVED');
    await flow.complete('REJECTED');
    await flow.cancel();
    await flow.refresh();
    assert.equal(flow.state.busy, 'APPROVED');
    release(APPROVED);
    await first;
    assert.deepEqual(fake.names().filter((n) => n !== 'list'), ['start', 'complete']);
  });

  it('no answer (network): the outcome is unknown, only "Consultar estado" is allowed, then the server decides', async () => {
    const { flow, fake } = await inGateway({ complete: [new TypeError('Failed to fetch')], get: [APPROVED], list: [list()] });
    await flow.complete('APPROVED');
    assert.equal(flow.state.mustCheck, true);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: UNCONFIRMED_MESSAGE });
    await flow.complete('APPROVED'); // not sent again blindly
    await flow.cancel();
    assert.deepEqual(fake.names(), ['list', 'start', 'complete']);
    await flow.refresh();
    await flush();
    assert.equal(flow.state.mustCheck, false);
    assert.equal((flow.state.view as { transaction: DemoTransaction }).transaction.status, 'APPROVED'); // it had been applied
  });

  it('403 or 422 while completing: the message, the transaction stays in the gateway', async () => {
    const { flow, timers } = await inGateway({ complete: [apiError(403, 'CSRF_FAILED')] });
    await flow.complete('APPROVED');
    assert.deepEqual(flow.state.notice, { tone: 'error', text: CSRF_MESSAGE });
    assert.equal((flow.state.view as { transaction: DemoTransaction }).transaction.status, 'INITIATED');
    assert.equal(flow.state.busy, null);
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]); // still followed
  });

  it('404 while completing: the transaction is not the resident\'s (or is gone): back to the list', async () => {
    const { flow, remembered } = await inGateway({ complete: [apiError(404, 'DEMO_TRANSACTION_NOT_FOUND')], list: [list(obligation())] });
    await flow.complete('APPROVED');
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.notice, { tone: 'error', text: TRANSACTION_MISSING });
    assert.equal(remembered.at(-1), null);
  });

  it('401 while completing: session expired, every action stops', async () => {
    const { flow, timers } = await inGateway({ complete: [apiError(401, 'NOT_AUTHENTICATED')] });
    await flow.complete('APPROVED');
    assert.equal(flow.state.sessionExpired, true);
    assert.deepEqual(timers.delays(), []);
    assert.equal(flow.state.polling, false);
  });

  it('back from an INITIATED transaction does nothing (cancel it or let it expire); from a result, the list', async () => {
    const { flow, fake, remembered } = await inGateway({ complete: [ended('REJECTED')], list: [list(obligation()), list(obligation())] });
    await flow.back();
    assert.equal(flow.state.view.kind, 'transaction');
    await flow.complete('REJECTED');
    await flush();
    await flow.back();
    assert.equal(flow.state.view.kind, 'list');
    assert.equal(remembered.at(-1), null);
    assert.equal(fake.names().filter((n) => n === 'list').length, 3);
  });
});

// --- Expiry and polling -----------------------------------------------------------------------------------------

describe('demo payment: expiry and status reading', () => {
  it('reads the status every 20 s while INITIATED and stops at a terminal status', async () => {
    const { flow, fake, timers } = await inGateway({ get: [transaction(), ended('CANCELLED')], list: [list(obligation())] });
    await timers.fire();
    assert.deepEqual(fake.calls.at(-1), ['get', 31]);
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]);
    await timers.fire(); // cancelled in another tab
    await flush();
    assert.equal((flow.state.view as { transaction: DemoTransaction }).transaction.status, 'CANCELLED');
    assert.deepEqual(timers.delays(), []); // no more reading
  });

  it('expired on screen: the server is asked at expires_at (plus a margin) and its EXPIRED is shown', async () => {
    const clock = { now: Date.parse('2026-10-09T20:14:50Z') };
    const { flow, timers } = await inGateway({ get: [ended('EXPIRED', { finished_at: '2026-10-09T20:15:00Z' })], list: [list(obligation())] }, clock);
    assert.deepEqual(timers.delays(), [10_000 + EXPIRY_GRACE_MS]); // sooner than the interval
    clock.now = Date.parse('2026-10-09T20:15:02Z');
    await timers.fire();
    await flush();
    assert.equal((flow.state.view as { transaction: DemoTransaction }).transaction.status, 'EXPIRED');
  });

  it('the browser clock ahead of the server: one reading at the local expiry, then the normal interval', async () => {
    // The browser believes the transaction expired 5 minutes ago; the server still has it INITIATED.
    const clock = { now: Date.parse('2026-10-09T20:20:00Z') };
    const { flow, fake, timers } = await inGateway({
      get: [transaction(), transaction(), transaction(), ended('EXPIRED')], list: [list(obligation())]
    }, clock);
    const scheduled = [...timers.delays()];
    for (let reading = 0; reading < 4; reading += 1) {
      await timers.fire();
      await flush();
      scheduled.push(...timers.delays());
    }
    // Asked once right away (the local clock says it expired), then every 20 s while the server says INITIATED.
    assert.deepEqual(scheduled, [0, POLL_INTERVAL_MS, POLL_INTERVAL_MS, POLL_INTERVAL_MS]);
    assert.equal(scheduled.filter((ms) => ms === 0).length, 1); // never a burst of immediate readings
    assert.equal(fake.names().filter((name) => name === 'get').length, 4); // one reading per timer, no more
    assert.equal((flow.state.view as { transaction: DemoTransaction }).transaction.status, 'EXPIRED'); // the server decides
    assert.deepEqual(timers.delays(), []);
  });

  it('the reading aimed at the expiry answers INITIATED (server clock behind): the next one waits the interval', async () => {
    const clock = { now: Date.parse('2026-10-09T20:14:50Z') };
    const { fake, timers } = await inGateway({ get: [transaction(), transaction()] }, clock);
    assert.deepEqual(timers.delays(), [10_000 + EXPIRY_GRACE_MS]); // aimed at expires_at + margin
    clock.now = Date.parse('2026-10-09T20:15:02Z');
    await timers.fire();
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]); // not 0, although the local clock is past the expiry
    clock.now += POLL_INTERVAL_MS;
    await timers.fire();
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]);
    assert.equal(fake.names().filter((name) => name === 'get').length, 2);
  });

  it('expired on screen: an outcome is not sent before asking the server', async () => {
    const clock = { now: NOW };
    const { flow, fake } = await inGateway({ get: [transaction()], complete: [ended('REJECTED')], list: [list(obligation())] }, clock);
    clock.now = Date.parse('2026-10-09T20:15:00Z'); // the local clock reached expires_at
    await flow.complete('REJECTED');
    assert.deepEqual(fake.calls.at(-1), ['get', 31]); // asked first, nothing sent
    // The server (its clock decides) still has it INITIATED: now the outcome is sent.
    await flow.complete('REJECTED');
    await flush();
    assert.deepEqual(fake.calls.find(([n]) => n === 'complete'), ['complete', 31, 'REJECTED']);
  });

  it('409 DEMO_TRANSACTION_EXPIRED: read again, EXPIRED shown, never paid', async () => {
    const message = 'La transacción venció. El pago no fue aplicado; inicia uno nuevo si aún quieres pagar.';
    const { flow } = await inGateway({ complete: [apiError(409, 'DEMO_TRANSACTION_EXPIRED', message)], get: [ended('EXPIRED')],
      list: [list(obligation())] });
    await flow.complete('APPROVED');
    await flush();
    assert.equal((flow.state.view as { transaction: DemoTransaction }).transaction.status, 'EXPIRED');
    assert.deepEqual(flow.state.notice, { tone: 'info', text: message });
  });

  it('never indefinitely: at most MAX_POLLS automatic reads, then "Consultar estado"', async () => {
    const clock = { now: NOW };
    const gets = Array.from({ length: MAX_POLLS }, () => transaction({ expires_at: '2026-10-10T20:00:00Z' }));
    const { flow, fake, timers } = await inGateway({ get: gets, start: [] }, clock);
    // A transaction far from expiring (the server's clock is ahead): polling still ends.
    for (let i = 0; i < MAX_POLLS; i += 1) await timers.fire();
    assert.equal(fake.names().filter((n) => n === 'get').length, MAX_POLLS);
    assert.deepEqual(timers.delays(), []);
    assert.equal(flow.state.polling, false);
    assert.deepEqual(flow.state.notice, { tone: 'info', text: POLLING_STOPPED });
  });

  it('a failed read stops the polling and asks the resident to check', async () => {
    const { flow, timers } = await inGateway({ get: [new TypeError('offline')] });
    await timers.fire();
    assert.deepEqual(timers.delays(), []);
    assert.equal(flow.state.mustCheck, true);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: CHECK_ERROR });
  });

  it('a 401 while reading stops everything', async () => {
    const { flow, timers } = await inGateway({ get: [apiError(401, 'NOT_AUTHENTICATED')] });
    await timers.fire();
    assert.equal(flow.state.sessionExpired, true);
    assert.deepEqual(timers.delays(), []);
  });

  it('leaving the page stops the timers and the renders', async () => {
    const { flow, timers, states } = await inGateway();
    const rendered = states.length;
    flow.dispose();
    assert.deepEqual(timers.delays(), []);
    await flow.refresh();
    assert.equal(states.length, rendered);
  });
});

// --- Resuming from the URL ------------------------------------------------------------------------------------

describe('demo payment: resuming after a reload', () => {
  it('the transaction of the URL is read again: the gateway if still INITIATED', async () => {
    const { flow, fake, timers, remembered } = setUp({ list: [list(obligation({ in_progress_transaction_id: 31 }))], get: [transaction()] });
    await flow.init(31);
    assert.deepEqual(fake.names(), ['list', 'get']);
    assert.deepEqual(remembered, [31]); // the id never leaves the URL while it is being resumed
    const view = flow.state.view as { transaction: DemoTransaction; obligation: PayableObligation };
    assert.equal(view.transaction.status, 'INITIATED');
    assert.equal(view.obligation.charge_id, 7);
    assert.deepEqual(timers.delays(), [POLL_INTERVAL_MS]);
  });

  it('or its result, as the server keeps it', async () => {
    const { flow, fake } = setUp({ list: [list(), list()], get: [APPROVED] });
    await flow.init(31);
    await flush();
    assert.equal((flow.state.view as { transaction: DemoTransaction }).transaction.status, 'APPROVED');
    assert.deepEqual(fake.names(), ['list', 'get', 'list']);
  });

  it('a reload whose reading fails keeps the id in the URL, to check it again', async () => {
    const { flow, remembered } = setUp({ list: [list(obligation())], get: [new TypeError('offline')] });
    await flow.init(31);
    assert.deepEqual(remembered, []);
    assert.equal(flow.state.mustCheck, true);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: CONNECTION_ERROR });
  });

  it('a failed load while resuming keeps the id in the URL, and "Reintentar" resumes that transaction', async () => {
    const { flow, fake, remembered } = setUp({
      list: [new TypeError('Failed to fetch'), list(obligation({ in_progress_transaction_id: 31 }))], get: [transaction()]
    });
    await flow.init(31);
    assert.equal(flow.state.view.kind, 'load-error');
    assert.equal(remembered.length, 0); // ?transaction=31 is still there (never removed)
    await flow.retry();
    assert.deepEqual(fake.names(), ['list', 'list', 'get']); // read again, never started
    assert.deepEqual(remembered, [31]); // never null
    const after: DemoState = flow.state;
    assert.equal((after.view as { transaction: DemoTransaction }).transaction.id, 31);
  });

  it('once the server said the transaction does not exist, "Reintentar" no longer looks for it', async () => {
    const { flow, fake, remembered } = setUp({
      list: [list(), new TypeError('offline'), list()], get: [apiError(404, 'DEMO_TRANSACTION_NOT_FOUND')]
    });
    await flow.init(999);
    assert.equal(remembered.at(-1), null);
    // (the list after "no existe" fails; retrying only loads the list)
    await flow.retry();
    assert.deepEqual(fake.names(), ['list', 'get', 'list', 'list']);
    assert.equal(flow.state.view.kind, 'list');
  });

  it('another resident\'s or a missing transaction: "no existe" and the list, without any data', async () => {
    const { flow, remembered } = setUp({ list: [list(obligation()), list(obligation())], get: [apiError(404, 'DEMO_TRANSACTION_NOT_FOUND', 'La transacción no existe.')] });
    await flow.init(999);
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.notice, { tone: 'error', text: TRANSACTION_MISSING });
    assert.equal(remembered.at(-1), null);
  });

  it('"Continuar pago en curso" in the list reads that transaction', async () => {
    const { flow, fake } = setUp({ list: [list(obligation({ in_progress_transaction_id: 31 }))], get: [transaction()] });
    await flow.init(null);
    flow.select(7);
    await flush();
    assert.deepEqual(fake.calls.at(-1), ['get', 31]);
    assert.equal(flow.state.view.kind, 'transaction');
    assert.deepEqual(flow.state.notice, { tone: 'info', text: RESUMED_IN_PROGRESS });
  });
});

// --- Answers out of order ----------------------------------------------------------------------------------

describe('demo payment: an older answer never overwrites a newer one', () => {
  const view = (flow: { state: DemoState }) => flow.state.view as { transaction: DemoTransaction };

  const actions: [string, DemoTransaction, (flow: ReturnType<typeof createDemoPaymentFlow>) => Promise<void>][] = [
    ['an approval', APPROVED, (flow) => flow.complete('APPROVED')],
    ['a rejection', ended('REJECTED'), (flow) => flow.complete('REJECTED')],
    ['a simulated failure', ended('FAILED'), (flow) => flow.complete('FAILED')],
    ['a cancellation', ended('CANCELLED'), (flow) => flow.cancel()]
  ];

  for (const [name, answer, act] of actions) {
    it(`a reading sent before ${name} answers INITIATED afterwards: the result stays`, async () => {
      const reading = deferred<DemoTransaction>();
      const { flow, timers, fake } = await inGateway({
        get: [reading.answer], complete: [answer], cancel: [answer], list: [list(obligation())]
      });
      await timers.fire(); // the automatic reading is sent and stays pending
      assert.deepEqual(fake.calls.at(-1), ['get', 31]);
      await act(flow); // the resident's action is answered first
      await flush();
      assert.equal(view(flow).transaction.status, answer.status);

      reading.resolve(transaction()); // the old answer, INITIATED, arrives last
      await flush();
      assert.equal(view(flow).transaction.status, answer.status); // never back to the gateway
      assert.deepEqual(timers.delays(), []); // and no reading is scheduled again
      assert.equal(flow.state.polling, false);
    });
  }

  it('an old reading that fails afterwards does not turn the result into an unknown one', async () => {
    const reading = deferred<DemoTransaction>();
    const { flow, timers } = await inGateway({ get: [reading.answer], complete: [APPROVED], list: [list()] });
    await timers.fire();
    await flow.complete('APPROVED');
    reading.reject(new TypeError('Failed to fetch'));
    await flush();
    assert.equal(view(flow).transaction.status, 'APPROVED');
    assert.equal(flow.state.mustCheck, false);
    assert.equal(flow.state.notice, null);
  });

  it('409 DEMO_PAYMENT_NOT_APPLIED: the state read afterwards (FAILED) is kept, not the older reading', async () => {
    const reading = deferred<DemoTransaction>();
    const message = 'No fue posible completar la transacción. El pago no fue aplicado y tu cuenta no cambió.';
    const { flow, timers, fake } = await inGateway({
      get: [reading.answer, ended('FAILED')], complete: [apiError(409, 'DEMO_PAYMENT_NOT_APPLIED', message)],
      list: [list(obligation())]
    });
    await timers.fire();
    await flow.complete('APPROVED');
    await flush();
    reading.resolve(transaction());
    await flush();
    assert.deepEqual(fake.names().filter((n) => n !== 'list'), ['start', 'get', 'complete', 'get']);
    assert.equal(view(flow).transaction.status, 'FAILED');
    assert.equal(view(flow).transaction.payment_id, null); // no receipt
    assert.deepEqual(flow.state.notice, { tone: 'error', text: message });
  });

  it('"Consultar estado" answered first wins over an older automatic reading', async () => {
    const reading = deferred<DemoTransaction>();
    const { flow, timers } = await inGateway({ get: [reading.answer, ended('CANCELLED')], list: [list(obligation())] });
    await timers.fire();
    await flow.refresh(); // cancelled in another tab
    reading.resolve(transaction());
    await flush();
    assert.equal(view(flow).transaction.status, 'CANCELLED');
    assert.deepEqual(timers.delays(), []);
  });

  it('after a resumption, an approval is not undone by the reading sent before it', async () => {
    const reading = deferred<DemoTransaction>();
    const { flow, timers } = setUp({
      list: [list(obligation({ in_progress_transaction_id: 31 })), list()], get: [transaction(), reading.answer],
      complete: [APPROVED]
    });
    await flow.init(31); // reload with ?transaction=31: INITIATED again
    assert.equal(view(flow).transaction.status, 'INITIATED');
    await timers.fire();
    await flow.complete('APPROVED');
    reading.resolve(transaction());
    await flush();
    assert.equal(view(flow).transaction.status, 'APPROVED');
  });

  it('an older list does not overwrite a newer one', async () => {
    const stale = deferred<{ items: PayableObligation[] }>();
    const { flow } = await inGateway({ complete: [APPROVED], list: [stale.answer, list()] });
    await flow.complete('APPROVED'); // asks for the list again (it stays pending)
    await flow.back(); // a newer list: nothing left to pay
    assert.deepEqual(flow.state.obligations, []);
    stale.resolve(list(obligation())); // the older list, with the balance before the payment
    await flush();
    assert.equal(flow.state.view.kind, 'list');
    assert.deepEqual(flow.state.obligations, []);
  });
});

// --- Answers while an action is pending (A–F of the technical review) ----------------------------------------

describe('demo payment: while an outcome or a cancellation is pending', () => {
  const view = (flow: { state: DemoState }) => flow.state.view as { transaction: DemoTransaction };
  const withoutList = (names: string[]) => names.filter((name) => name !== 'list');

  const actions: [string, DemoTransaction, (flow: ReturnType<typeof createDemoPaymentFlow>) => Promise<void>][] = [
    ['approval', APPROVED, (flow) => flow.complete('APPROVED')],
    ['rejection', ended('REJECTED'), (flow) => flow.complete('REJECTED')],
    ['cancellation', ended('CANCELLED'), (flow) => flow.cancel()]
  ];

  for (const [name, answer, act] of actions) {
    it(`A. ${name} pending: "Consultar estado" sends no reading, the confirmed answer is shown`, async () => {
      const action = deferred<DemoTransaction>();
      const { flow, fake, timers } = await inGateway({ complete: [action.answer], cancel: [action.answer], list: [list()] });
      const pending = act(flow);
      assert.deepEqual(timers.delays(), []); // the automatic reading stopped with the action
      await flow.refresh();
      await flow.complete('APPROVED'); // and no second action either
      await flow.cancel();
      assert.deepEqual(withoutList(fake.names()), ['start', name === 'cancellation' ? 'cancel' : 'complete']);
      assert.notEqual(flow.state.busy, null);

      action.resolve(answer);
      await pending;
      await flush();
      assert.equal(view(flow).transaction.status, answer.status);
      assert.equal(flow.state.busy, null);
      assert.deepEqual(timers.delays(), []);
    });
  }

  it('B. an older reading answers INITIATED while the approval is pending: the approval still decides', async () => {
    const reading = deferred<DemoTransaction>();
    const action = deferred<DemoTransaction>();
    const { flow, fake, timers } = await inGateway({ get: [reading.answer], complete: [action.answer], list: [list()] });
    await timers.fire(); // the reading is sent
    const pending = flow.complete('APPROVED'); // then the approval
    reading.resolve(transaction()); // the older INITIATED arrives first
    await flush();
    assert.equal(flow.state.busy, 'APPROVED'); // still waiting for the approval: nothing re-enabled
    assert.equal(view(flow).transaction.status, 'INITIATED'); // what was shown, unchanged until the server answers
    assert.equal(flow.state.mustCheck, false);
    assert.equal(flow.state.notice, null);
    assert.deepEqual(timers.delays(), []); // no reading scheduled by the old answer

    action.resolve(APPROVED);
    await pending;
    await flush();
    assert.equal(view(flow).transaction.status, 'APPROVED');
    assert.equal(view(flow).transaction.receipt_number, 'RC-00000012');
    assert.deepEqual(withoutList(fake.names()), ['start', 'get', 'complete']);
  });

  it('C. an older reading fails (network) while the approval is pending: no stale error, the approval decides', async () => {
    const reading = deferred<DemoTransaction>();
    const action = deferred<DemoTransaction>();
    const { flow, fake, timers } = await inGateway({ get: [reading.answer], complete: [action.answer], list: [list()] });
    await timers.fire();
    const pending = flow.complete('APPROVED');
    reading.reject(new TypeError('Failed to fetch')); // the older reading fails first
    await flush();
    assert.equal(flow.state.busy, 'APPROVED');
    assert.equal(flow.state.mustCheck, false); // never "unknown" because of an old reading
    assert.equal(flow.state.notice, null); // no stale error shown
    assert.deepEqual(timers.delays(), []);

    action.resolve(APPROVED);
    await pending;
    await flush();
    assert.equal(view(flow).transaction.status, 'APPROVED');
    assert.equal(flow.state.mustCheck, false);
    assert.equal(flow.state.notice, null);
    assert.deepEqual(withoutList(fake.names()), ['start', 'get', 'complete']);
  });

  it('D. the approval gets no answer and an older reading answers INITIATED: still uncertain, only "Consultar estado"', async () => {
    const reading = deferred<DemoTransaction>();
    const { flow, fake, timers } = await inGateway({
      get: [reading.answer, APPROVED], complete: [new TypeError('Failed to fetch')], list: [list()]
    });
    await timers.fire();
    await flow.complete('APPROVED'); // no answer: applied or not, nobody knows yet
    reading.resolve(transaction()); // the older INITIATED arrives afterwards
    await flush();
    assert.equal(flow.state.mustCheck, true); // the old INITIATED does not settle the doubt
    assert.deepEqual(flow.state.notice, { tone: 'error', text: UNCONFIRMED_MESSAGE });
    assert.equal(view(flow).transaction.status, 'INITIATED'); // never an approval invented by the page
    assert.equal(view(flow).transaction.payment_id, null);
    assert.deepEqual(timers.delays(), []);

    await flow.complete('APPROVED'); // no second payment sent blindly
    await flow.cancel();
    assert.deepEqual(withoutList(fake.names()), ['start', 'get', 'complete']);

    await flow.refresh(); // the real state, from the server
    await flush();
    assert.equal(view(flow).transaction.status, 'APPROVED');
    assert.equal(flow.state.mustCheck, false);
    assert.deepEqual(withoutList(fake.names()), ['start', 'get', 'complete', 'get']);
  });

  it('E. 409 DEMO_PAYMENT_NOT_APPLIED: an older INITIATED during the re-read is not shown as the result', async () => {
    const reading = deferred<DemoTransaction>();
    const reread = deferred<DemoTransaction>();
    const message = 'No fue posible completar la transacción. El pago no fue aplicado y tu cuenta no cambió.';
    const { flow, fake, timers } = await inGateway({
      get: [reading.answer, reread.answer], complete: [apiError(409, 'DEMO_PAYMENT_NOT_APPLIED', message)],
      list: [list(obligation())]
    });
    await timers.fire();
    const pending = flow.complete('APPROVED');
    await flush(); // the 409 arrived: the real state is being read
    assert.equal(flow.state.busy, 'checking');
    reading.resolve(transaction()); // the older INITIATED arrives during the re-read
    await flush();
    assert.equal(flow.state.busy, 'checking'); // still waiting for the real state
    assert.deepEqual(timers.delays(), []);
    assert.deepEqual(flow.state.notice, { tone: 'error', text: message });

    reread.resolve(ended('FAILED'));
    await pending;
    await flush();
    assert.equal(view(flow).transaction.status, 'FAILED');
    assert.equal(view(flow).transaction.payment_id, null);
    assert.ok(transactionLines(view(flow).transaction, null).every((line) => !line.label.startsWith('Número'))); // no receipt
    assert.deepEqual(withoutList(fake.names()), ['start', 'get', 'complete', 'get']);
  });

  it('F. "Reintentar" while the approval is pending loads nothing and does not drop the approval', async () => {
    const action = deferred<DemoTransaction>();
    const { flow, fake, remembered } = await inGateway({ complete: [action.answer], list: [list()] });
    const pending = flow.complete('APPROVED');
    await flow.retry(); // called on the controller directly (the page hides and disables the button)
    assert.deepEqual(fake.names(), ['list', 'start', 'complete']); // no list, no reading, no new transaction
    assert.equal(flow.state.busy, 'APPROVED');
    assert.equal(flow.state.view.kind, 'transaction');

    action.resolve(APPROVED);
    await pending;
    await flush();
    assert.equal(view(flow).transaction.status, 'APPROVED');
    assert.deepEqual(fake.names(), ['list', 'start', 'complete', 'list']); // only the list refreshed after the result
    assert.equal(remembered.at(-1), 31);
  });

  it('F. after a resumption too: "Reintentar" during the pending approval changes nothing', async () => {
    const action = deferred<DemoTransaction>();
    const { flow, fake } = setUp({
      list: [new TypeError('offline'), list(obligation({ in_progress_transaction_id: 31 })), list()],
      get: [transaction()], complete: [action.answer]
    });
    await flow.init(31);
    await flow.retry(); // the normal retry: resumes ?transaction=31
    assert.equal(view(flow).transaction.status, 'INITIATED');
    const pending = flow.complete('APPROVED');
    await flow.retry();
    action.resolve(APPROVED);
    await pending;
    await flush();
    assert.equal(view(flow).transaction.status, 'APPROVED');
    assert.deepEqual(fake.names(), ['list', 'list', 'get', 'complete', 'list']);
  });
});
