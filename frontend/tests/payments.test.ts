/**
 * HU-004: payment form, resident picker, confirmation and payments summary.
 * Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError } from '../src/lib/api/errors.ts';
import type { Payment, PaymentPage, PaymentPayload, RegisteredPayment } from '../src/lib/api/payments.ts';
import type { Resident, ResidentPage } from '../src/lib/api/residents.ts';
import { decidePageAccess } from '../src/lib/auth/page-access.ts';
import {
  CONNECTION_ERROR,
  EMAIL_FAILED_WARNING,
  FORBIDDEN_MESSAGE,
  SUCCESS_MESSAGE,
  bogotaToday,
  confirmationOf,
  createPaymentFlow,
  createResidentPicker,
  formatCop,
  formatDate,
  isIsoDate,
  parseAmount,
  residentSearchQuery,
  showsDescription,
  validatePaymentForm,
  type PaymentFormValues,
  type PickerState,
  type SubmitState
} from '../src/lib/payments/payment-form.ts';
import {
  DEFAULT_QUERY,
  EMPTY_MESSAGE,
  LOADING_MESSAGE,
  NO_RESULTS_MESSAGE,
  createPaymentsList,
  dateRangeError,
  parseQuery,
  rangeText,
  toQueryString,
  type PaymentQuery,
  type PaymentsListState
} from '../src/lib/payments/payments-list.ts';

const TODAY = '2026-10-02';
const apiError = (status: number, message = 'x', code = 'X', details: { field?: string; message: string }[] = []) =>
  new ApiError(status, { error: { code, message, details } });
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Manual debounce timer. */
function manualTimer() {
  let pending: (() => void) | null = null;
  return {
    schedule: (callback: () => void) => {
      pending = callback;
      return () => {
        pending = null;
      };
    },
    has: () => pending !== null,
    fire: async () => {
      const callback = pending;
      pending = null;
      callback?.();
      await tick();
    }
  };
}

const LAURA: Resident = {
  id: 7,
  full_name: 'Laura Gómez',
  document_number: '1012345678',
  phone: null,
  email: 'laura@example.com',
  unit: { id: 3, label: 'B1-TO2-APTO-104' },
  status: 'ACTIVE'
};

const VALID: PaymentFormValues = {
  residentId: 7,
  amount: '150000',
  method: 'TRANSFER',
  concept: 'MONTHLY_FEE',
  description: '',
  paymentDate: TODAY,
  transactionReference: '',
  notes: ''
};

function registered(overrides: Partial<RegisteredPayment> = {}): RegisteredPayment {
  return {
    id: 42,
    receipt_number: 'RC-00000042',
    resident: { id: 7, full_name: 'Laura Gómez' },
    unit: { id: 3, label: 'B1-TO2-APTO-104' },
    amount: '250000.00',
    method: 'TRANSFER',
    concept: 'MONTHLY_FEE',
    concept_description: null,
    payment_date: TODAY,
    registered_at: '2026-10-02T20:05:00Z',
    transaction_reference: null,
    notes: null,
    applied_amount: '220000.00',
    credit_amount: '30000.00',
    status: 'PARTIALLY_APPLIED',
    receipt_email_status: 'SENT',
    allocations: [],
    account: { pending_balance: '0.00', credit_balance: '30000.00' },
    ...overrides
  };
}

// --- Form -------------------------------------------------------------------------------------------

describe('amount', () => {
  it('numbers with up to 2 decimals (comma or dot), never rounded or guessed', () => {
    assert.deepEqual(parseAmount('150000'), { value: '150000' });
    assert.deepEqual(parseAmount(' 150000,5 '), { value: '150000.50' });
    assert.deepEqual(parseAmount('$150000.25'), { value: '150000.25' });
    assert.deepEqual(parseAmount('0007'), { value: '7' });
    for (const text of ['150.000', '150.000,00', '12,345', '1e3', '-5', 'abc', '15 0O0']) {
      assert.ok('error' in parseAmount(text), text);
    }
    assert.deepEqual(parseAmount(''), { error: 'Ingresa el monto.' });
    assert.deepEqual(parseAmount('0,00'), { error: 'El monto debe ser mayor que cero.' });
    assert.ok('error' in parseAmount('10000000000'));
    assert.deepEqual(parseAmount('9999999999.99'), { value: '9999999999.99' });
  });

  it('COP format for display', () => {
    assert.equal(formatCop('150000.00').replace(/\s/g, ' '), '$ 150.000,00');
  });
});

describe('dates', () => {
  it('today in Bogotá, not in UTC', () => {
    assert.equal(bogotaToday(new Date('2026-10-03T03:00:00Z')), '2026-10-02');
    assert.equal(bogotaToday(new Date('2026-10-03T05:00:00Z')), '2026-10-03');
  });

  it('real calendar dates only', () => {
    assert.equal(isIsoDate('2026-02-28'), true);
    for (const text of ['2026-02-30', '2026-13-01', '02/10/2026', '2026-1-5', '']) assert.equal(isIsoDate(text), false, text);
    assert.equal(formatDate('2026-10-01'), '1 de octubre de 2026');
  });
});

describe('form validation (format only)', () => {
  it('a valid form gives the payload: no apartment, no status, no registration time', () => {
    const { errors, payload } = validatePaymentForm(VALID, TODAY);
    assert.deepEqual(errors, {});
    assert.deepEqual(payload, {
      resident_id: 7, amount: '150000', method: 'TRANSFER', concept: 'MONTHLY_FEE', concept_description: null, payment_date: TODAY,
      transaction_reference: null, notes: null
    } satisfies PaymentPayload);
    assert.deepEqual(Object.keys(payload!).sort(), [
      'amount', 'concept', 'concept_description', 'method', 'notes', 'payment_date', 'resident_id', 'transaction_reference'
    ]);
  });

  it('transaction reference and notes (HU-008): optional, normalized and limited', () => {
    const { errors, payload } = validatePaymentForm(
      { ...VALID, transactionReference: '  TRF   2026-10-0042 ', notes: '  Pagó en la portería.\r\nTrajo el soporte.  ' },
      TODAY
    );
    assert.deepEqual(errors, {});
    assert.equal(payload?.transaction_reference, 'TRF 2026-10-0042');
    assert.equal(payload?.notes, 'Pagó en la portería.\nTrajo el soporte.');
    // Empty or only spaces: null (never an invented reference).
    const empty = validatePaymentForm({ ...VALID, transactionReference: '   ', notes: ' \n ' }, TODAY).payload;
    assert.equal(empty?.transaction_reference, null);
    assert.equal(empty?.notes, null);
    assert.deepEqual(validatePaymentForm({ ...VALID, transactionReference: 'x'.repeat(101), notes: 'y'.repeat(501) }, TODAY).errors, {
      transaction_reference: 'La referencia de transacción no puede superar 100 caracteres.',
      notes: 'Las notas no pueden superar 500 caracteres.'
    });
    assert.equal(validatePaymentForm({ ...VALID, transactionReference: 'x'.repeat(100), notes: 'y'.repeat(500) }, TODAY).payload?.notes?.length, 500);
  });

  it('every missing field is pointed at (criterion 1.4)', () => {
    const { errors, payload } = validatePaymentForm(
      { residentId: null, amount: '', method: '', concept: '', description: '', paymentDate: '', transactionReference: '', notes: '' },
      TODAY
    );
    assert.equal(payload, null);
    assert.deepEqual(errors, {
      resident_id: 'Selecciona el residente.',
      amount: 'Ingresa el monto.',
      method: 'Selecciona el método de pago.',
      concept: 'Selecciona el concepto del pago.',
      payment_date: 'Selecciona la fecha del pago.'
    });
  });

  it('only the allowed methods and concepts', () => {
    assert.ok(validatePaymentForm({ ...VALID, method: 'PSE' }, TODAY).errors.method);
    assert.ok(validatePaymentForm({ ...VALID, concept: 'RENT' }, TODAY).errors.concept);
  });

  it('future or invalid dates', () => {
    assert.equal(validatePaymentForm({ ...VALID, paymentDate: '2026-10-03' }, TODAY).errors.payment_date,
      'La fecha del pago no puede ser posterior a hoy.');
    assert.equal(validatePaymentForm({ ...VALID, paymentDate: '2026-02-30' }, TODAY).errors.payment_date,
      'La fecha del pago no es válida.');
  });

  it('"Otro" shows and requires a description; other concepts never send it', () => {
    assert.equal(showsDescription('OTHER'), true);
    assert.equal(showsDescription('MONTHLY_FEE'), false);
    assert.equal(validatePaymentForm({ ...VALID, concept: 'OTHER' }, TODAY).errors.concept_description,
      'Describe el concepto del pago.');
    const other = validatePaymentForm({ ...VALID, concept: 'OTHER', description: '  Arreglo   puerta ' }, TODAY);
    assert.equal(other.payload?.concept_description, 'Arreglo puerta');
    // A description typed before switching to another concept is not sent.
    assert.equal(validatePaymentForm({ ...VALID, description: 'quedó escrita' }, TODAY).payload?.concept_description, null);
    assert.ok(validatePaymentForm({ ...VALID, concept: 'OTHER', description: 'x'.repeat(201) }, TODAY).errors.concept_description);
  });
});

// --- Resident picker ------------------------------------------------------------------------------------

describe('resident picker', () => {
  function setup(answer: ResidentPage | Error) {
    const timer = manualTimer();
    const requests: string[] = [];
    const states: PickerState[] = [];
    const navigations: string[] = [];
    const picker = createResidentPicker({
      load: async (query) => {
        requests.push(query);
        if (answer instanceof Error) throw answer;
        return answer;
      },
      render: (state) => states.push(state),
      navigate: (path) => navigations.push(path),
      schedule: timer.schedule
    });
    return { picker, timer, requests, states, navigations };
  }
  const found: ResidentPage = { items: [LAURA], page: 1, page_size: 8, total: 1, total_pages: 1 };

  it('searches registered residents (HU-006 list) after a pause', async () => {
    const { picker, timer, requests, states } = setup(found);
    picker.search('La');
    picker.search('Laura');
    assert.equal(requests.length, 0);
    await timer.fire();
    assert.deepEqual(requests, [residentSearchQuery('Laura')]);
    assert.equal(requests[0], 'page_size=8&search=Laura');
    assert.deepEqual(states.map((s) => s.kind), ['searching', 'results']);
  });

  it('selecting shows the apartment resolved by the backend', async () => {
    const { picker, timer, states } = setup(found);
    picker.search('Laura');
    await timer.fire();
    picker.select(LAURA);
    assert.deepEqual(states.at(-1), { kind: 'selected', resident: LAURA });
    assert.equal(picker.selected?.unit.label, 'B1-TO2-APTO-104');
    picker.search('otra');  // typing again clears the selection
    assert.equal(picker.selected, null);
    picker.clear();
    assert.deepEqual(states.at(-1), { kind: 'idle' });
  });

  it('no match, errors and an expired session', async () => {
    const none = setup({ items: [], page: 1, page_size: 8, total: 0, total_pages: 0 });
    none.picker.search('zzz');
    await none.timer.fire();
    assert.equal(none.states.at(-1)?.kind, 'none');

    const failing = setup(apiError(500, 'Error interno'));
    failing.picker.search('x');
    await failing.timer.fire();
    assert.deepEqual(failing.states.at(-1), { kind: 'error', message: 'Error interno' });

    const expired = setup(apiError(401));
    expired.picker.search('x');
    await expired.timer.fire();
    assert.deepEqual(expired.navigations, ['/login']);
  });

  it('an empty text cancels the pending search', () => {
    const { picker, timer, states } = setup(found);
    picker.search('La');
    picker.search('   ');
    assert.equal(timer.has(), false);
    assert.deepEqual(states.at(-1), { kind: 'idle' });
  });
});

// --- Submit and confirmation ------------------------------------------------------------------------------

describe('registering a payment', () => {
  function setup(answer: RegisteredPayment | Error) {
    const payloads: PaymentPayload[] = [];
    const states: SubmitState[] = [];
    const navigations: string[] = [];
    const registeredPayments: RegisteredPayment[] = [];
    const flow = createPaymentFlow({
      register: async (payload) => {
        payloads.push(payload);
        await tick();
        if (answer instanceof Error) throw answer;
        return answer;
      },
      render: (state) => states.push(state),
      navigate: (path) => navigations.push(path),
      onRegistered: (payment) => registeredPayments.push(payment)
    });
    return { flow, payloads, states, navigations, registeredPayments };
  }

  it('submits, confirms and asks the summary to refresh', async () => {
    const { flow, payloads, states, registeredPayments } = setup(registered());
    await flow.submit(VALID, TODAY);
    assert.equal(payloads.length, 1);
    assert.deepEqual(states.map((s) => s.kind), ['submitting', 'done']);
    assert.equal(registeredPayments.length, 1);
  });

  it('confirmation: receipt number, amount, applied, credit, status and the unit balance', () => {
    const confirmation = confirmationOf(registered());
    assert.equal(confirmation.title, SUCCESS_MESSAGE);
    const values = Object.fromEntries(confirmation.lines.map((l) => [l.label, l.value.replace(/\s/g, ' ')]));
    assert.equal(values['Recibo'], 'RC-00000042');
    assert.equal(values['Monto'], '$ 250.000,00');
    assert.equal(values['Valor aplicado'], '$ 220.000,00');
    assert.equal(values['Saldo a favor'], '$ 30.000,00');
    assert.equal(values['Estado del pago'], 'Aplicado parcialmente');
    assert.equal(values['Saldo a favor de la unidad'], '$ 30.000,00');
    assert.equal(confirmation.warning, null);
    const applied = confirmationOf(registered({ credit_amount: '0.00', status: 'APPLIED' }));
    assert.equal(applied.lines.some((l) => l.label === 'Saldo a favor'), false);
    // Without a reference or notes, no empty lines.
    assert.equal(confirmation.lines.some((l) => l.label === 'Referencia de transacción' || l.label === 'Notas'), false);
    const withData = confirmationOf(registered({ transaction_reference: 'TRF-0042', notes: 'Soporte en físico.' }));
    const shown = Object.fromEntries(withData.lines.map((l) => [l.label, l.value]));
    assert.equal(shown['Referencia de transacción'], 'TRF-0042');
    assert.equal(shown['Notas'], 'Soporte en físico.');
    assert.equal(shown['Recibo'], 'RC-00000042', 'the receipt number stays separate');
  });

  it('a failed email is a separate warning, never a failed payment', async () => {
    const { flow, states } = setup(registered({ receipt_email_status: 'FAILED' }));
    await flow.submit(VALID, TODAY);
    const done = states.at(-1)!;
    assert.equal(done.kind, 'done');
    assert.equal(done.kind === 'done' && done.confirmation.title, SUCCESS_MESSAGE);
    assert.equal(done.kind === 'done' && done.confirmation.warning, EMAIL_FAILED_WARNING);
  });

  it('invalid form: nothing is sent', async () => {
    const { flow, payloads, states } = setup(registered());
    await flow.submit({ ...VALID, amount: 'abc' }, TODAY);
    assert.equal(payloads.length, 0);
    assert.equal(states.at(-1)?.kind, 'invalid');
  });

  it('no double submission while sending', async () => {
    const { flow, payloads } = setup(registered());
    await Promise.all([flow.submit(VALID, TODAY), flow.submit(VALID, TODAY)]);
    assert.equal(payloads.length, 1);
  });

  it('422 from the backend points at the field', async () => {
    const error = apiError(422, 'Los datos enviados no son válidos.', 'VALIDATION_ERROR', [
      { field: 'resident_id', message: 'Selecciona un residente registrado con una residencia vigente.' }
    ]);
    const { flow, states } = setup(error);
    await flow.submit(VALID, TODAY);
    assert.deepEqual(states.at(-1), {
      kind: 'error',
      message: 'Los datos enviados no son válidos.',
      errors: { resident_id: 'Selecciona un residente registrado con una residencia vigente.' }
    });
  });

  it('401 → login, 403 → not allowed, 500 and network errors → message', async () => {
    const expired = setup(apiError(401));
    await expired.flow.submit(VALID, TODAY);
    assert.deepEqual(expired.navigations, ['/login']);

    const forbidden = setup(apiError(403, 'x', 'FORBIDDEN'));
    await forbidden.flow.submit(VALID, TODAY);
    assert.deepEqual(forbidden.states.at(-1), { kind: 'error', message: FORBIDDEN_MESSAGE, errors: {} });

    const csrf = setup(apiError(403, 'No se pudo verificar la solicitud.', 'CSRF_FAILED'));
    await csrf.flow.submit(VALID, TODAY);
    // A CSRF failure is not "not allowed": the API message (reload the page) is shown.
    assert.deepEqual(csrf.states.at(-1), { kind: 'error', message: 'No se pudo verificar la solicitud.', errors: {} });

    const server = setup(apiError(500, 'Ocurrió un error interno.'));
    await server.flow.submit(VALID, TODAY);
    assert.deepEqual(server.states.at(-1), { kind: 'error', message: 'Ocurrió un error interno.', errors: {} });

    const offline = setup(new TypeError('fetch failed'));
    await offline.flow.submit(VALID, TODAY);
    assert.deepEqual(offline.states.at(-1), { kind: 'error', message: CONNECTION_ERROR, errors: {} });
  });
});

// --- Summary ------------------------------------------------------------------------------------------------

function payment(id: number): Payment {
  const { allocations: _a, account: _b, ...base } = registered({ id, receipt_number: `RC-${String(id).padStart(8, '0')}` });
  return base;
}

function pageOf(items: Payment[], page = 1, pageSize = 10, total = items.length): PaymentPage {
  return { items, page, page_size: pageSize, total, total_pages: Math.ceil(total / pageSize) };
}

function listSetup(answer: PaymentPage | Error | ((query: string) => PaymentPage | Error), initial: PaymentQuery = DEFAULT_QUERY) {
  const timer = manualTimer();
  const requests: string[] = [];
  const states: PaymentsListState[] = [];
  const navigations: string[] = [];
  const list = createPaymentsList(initial, {
    load: async (query) => {
      requests.push(query);
      const result = typeof answer === 'function' ? answer(query) : answer;
      if (result instanceof Error) throw result;
      return result;
    },
    render: (state) => states.push(state),
    remember: () => {},
    navigate: (path) => navigations.push(path),
    schedule: timer.schedule
  });
  return { list, timer, requests, states, navigations };
}

describe('payments summary', () => {
  it('query string: whitelisted options, newest first by default', () => {
    assert.equal(toQueryString(DEFAULT_QUERY), '');
    const query: PaymentQuery = { page: 2, pageSize: 25, search: 'Gómez', dateFrom: '2026-01-01', dateTo: '2026-01-31', sortBy: 'amount', sortOrder: 'asc' };
    assert.deepEqual(parseQuery(new URLSearchParams(toQueryString(query))), query);
    assert.deepEqual(parseQuery(new URLSearchParams('sort_by=password_hash&sort_order=up&date_from=ayer&page=-1&page_size=7')), DEFAULT_QUERY);
    assert.deepEqual(parseQuery(new URLSearchParams('date_from=2026-02-01&date_to=2026-01-01')), DEFAULT_QUERY);
  });

  it('loading, then the table', async () => {
    const { list, states } = listSetup(pageOf([payment(1), payment(2)]));
    await list.start();
    assert.deepEqual(states.map((s) => s.kind), ['loading', 'results']);
    assert.equal(LOADING_MESSAGE, 'Consultando pagos...');
    assert.equal(rangeText(pageOf([payment(1)])), 'Mostrando 1–1 de 1 pago');
  });

  it('empty and no results', async () => {
    const empty = listSetup(pageOf([]));
    await empty.list.start();
    assert.equal(empty.states.at(-1)?.kind, 'empty');
    assert.equal(EMPTY_MESSAGE, 'No hay pagos registrados.');
    const filtered = listSetup(pageOf([]), { ...DEFAULT_QUERY, dateFrom: '2026-01-01' });
    await filtered.list.start();
    assert.equal(filtered.states.at(-1)?.kind, 'no-results');
    assert.equal(NO_RESULTS_MESSAGE, 'No se encontraron pagos con los filtros seleccionados.');
  });

  it('filters: resident (debounced) and dates, from page 1', async () => {
    const { list, timer, requests } = listSetup(pageOf([payment(1)]), { ...DEFAULT_QUERY, page: 3 });
    list.searchResident('Lau');
    list.searchResident('Laura');
    await timer.fire();
    await list.setDates('2026-01-01', '2026-01-31');
    assert.deepEqual(requests, ['search=Laura', 'search=Laura&date_from=2026-01-01&date_to=2026-01-31']);
    assert.equal(dateRangeError('2026-02-01', '2026-01-01'), 'La fecha final no puede ser anterior a la inicial.');
    assert.equal(dateRangeError('2026-01-01', ''), null);
  });

  it('sort: date and amount start with the newest/largest, the others alphabetically', async () => {
    const { list, requests } = listSetup(pageOf([payment(1)]));
    await list.sort('date');    // date is already desc → asc
    await list.sort('amount');  // new column → desc
    await list.sort('resident'); // new column → asc
    await list.sort('resident'); // toggle → desc
    assert.deepEqual(requests, ['sort_order=asc', 'sort_by=amount', 'sort_by=resident&sort_order=asc', 'sort_by=resident']);
  });

  it('pagination and page size', async () => {
    const { list, requests } = listSetup(pageOf([payment(1)], 1, 10, 30));
    await list.goToPage(2);
    await list.setPageSize(25);
    assert.deepEqual(requests, ['page=2', 'page_size=25']);
  });

  it('401 → login; 403, 500 → message and retry asks again', async () => {
    const expired = listSetup(apiError(401));
    await expired.list.start();
    assert.deepEqual(expired.navigations, ['/login']);

    const forbidden = listSetup(apiError(403));
    await forbidden.list.start();
    assert.deepEqual(forbidden.states.at(-1), { kind: 'error', query: DEFAULT_QUERY, message: 'No tienes permiso para ver los pagos.' });

    let calls = 0;
    const flaky = listSetup(() => (++calls === 1 ? apiError(500) : pageOf([payment(1)])));
    await flaky.list.start();
    assert.deepEqual(flaky.states.at(-1), { kind: 'error', query: DEFAULT_QUERY, message: 'No fue posible consultar los pagos. Intenta de nuevo.' });
    await flaky.list.refresh();  // "Reintentar"
    assert.equal(flaky.states.at(-1)?.kind, 'results');
  });
});

describe('navigation (HU-018 access decision)', () => {
  it('/admin/payments: administrator only', async () => {
    const session = (role: 'ADMINISTRADOR' | 'RESIDENTE') => async () => ({
      user: { id: 1, full_name: 'X', email: 'x@example.com', role }, csrf_token: 'c'
    });
    assert.equal((await decidePageAccess('/admin/payments', session('ADMINISTRADOR'))).kind, 'allowed');
    assert.equal((await decidePageAccess('/admin/payments', session('RESIDENTE'))).kind, 'forbidden');
    assert.deepEqual(await decidePageAccess('/admin/payments', async () => null), { kind: 'redirect', location: '/login' });
  });
});
