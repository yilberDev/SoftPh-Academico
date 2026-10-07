/**
 * HU-008: "Mis pagos" of the resident — formats, list (query, filters, pages,
 * states, errors) and detail. The API is a fake injected into each controller
 * (no server). Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError, type ApiErrorBody } from '../src/lib/api/errors.ts';
import type { ResidentPayment, ResidentPaymentDetail, ResidentPaymentPage } from '../src/lib/api/resident-payments.ts';
import {
  FORBIDDEN_MESSAGE as DETAIL_FORBIDDEN,
  NO_ALLOCATIONS,
  allocationRows,
  financialLines,
  isPaymentId,
  loadDetailPage,
  mainLines,
  notesOf
} from '../src/lib/resident/payment-detail.ts';
import {
  NO_NOTES,
  NO_REFERENCE,
  STATUS_BADGES,
  STATUS_HELP,
  STATUS_LABELS,
  STATUSES,
  formatCop,
  formatDate,
  formatDateTime,
  periodText,
  referenceText
} from '../src/lib/resident/payment-format.ts';
import {
  CONNECTION_ERROR,
  DEFAULT_QUERY,
  EMPTY_MESSAGE,
  FORBIDDEN_MESSAGE,
  INVALID_FILTERS_MESSAGE,
  NO_RESULTS_MESSAGE,
  SESSION_EXPIRED_MESSAGE,
  activeFilterCount,
  createResidentPaymentsList,
  parseQuery,
  paymentRow,
  rangeText,
  toQueryString,
  type ResidentPaymentsState
} from '../src/lib/resident/payments-list.ts';

const spaces = (text: string) => text.replace(/\s/g, ' ');

function apiError(status: number, code: string, message: string, details: ApiErrorBody['error']['details'] = []): ApiError {
  return new ApiError(status, { error: { code, message, details } });
}

function payment(id: number, overrides: Partial<ResidentPayment> = {}): ResidentPayment {
  return {
    id,
    receipt_number: `RC-${String(id).padStart(8, '0')}`,
    payment_date: '2026-01-15',
    concept: 'MONTHLY_FEE',
    concept_description: null,
    amount: '150000.00',
    method: 'TRANSFER',
    status: 'APPLIED',
    transaction_reference: null,
    ...overrides
  };
}

function pageOf(items: ResidentPayment[], page = 1, total = items.length, pageSize = 10): ResidentPaymentPage {
  return { items, page, page_size: pageSize, total, total_pages: Math.ceil(total / pageSize) };
}

const DETAIL: ResidentPaymentDetail = {
  ...payment(12, { amount: '80000.00', status: 'PARTIALLY_APPLIED', transaction_reference: 'TRF-2026-0042' }),
  registered_at: '2026-01-15T20:05:00Z',
  notes: 'Pagó en la portería.\nTrajo el soporte.',
  unit: { id: 7, label: 'B1-TO1-APTO-101' },
  applied_amount: '50000.00',
  credit_amount: '30000.00',
  allocations: [
    {
      charge_id: 3, concept: 'MONTHLY_FEE', description: null, billing_period: '2026-01', due_date: '2026-01-05',
      charge_amount: '100000.00', allocated_amount: '20000.00', allocated_at: '2026-01-15T20:05:00.123456Z'
    },
    {
      charge_id: 4, concept: 'PENALTY', description: 'Ruido', billing_period: null, due_date: '2026-02-10',
      charge_amount: '50000.00', allocated_amount: '30000.00', allocated_at: '2026-01-15T20:05:00.123456Z'
    }
  ]
};

/** A list whose API answers are queued by the test; every request and state is recorded. */
function harness(answers: (ResidentPaymentPage | Error)[], url = '') {
  const requests: string[] = [];
  const remembered: string[] = [];
  const states: ResidentPaymentsState[] = [];
  const list = createResidentPaymentsList(parseQuery(new URLSearchParams(url)), {
    load: async (queryString) => {
      requests.push(queryString);
      const answer = answers.shift() ?? pageOf([]);
      if (answer instanceof Error) throw answer;
      return answer;
    },
    render: (state) => states.push(state),
    remember: (queryString) => remembered.push(queryString)
  });
  return { list, requests, remembered, states, last: () => states[states.length - 1] };
}

// --- Formats ------------------------------------------------------------------------------------------

describe('resident payments: formats', () => {
  it('the three API statuses in Spanish, with a badge and a help text (never color alone)', () => {
    assert.deepEqual(STATUSES, ['APPLIED', 'PARTIALLY_APPLIED', 'CREDIT']);
    assert.deepEqual(STATUSES.map((s) => STATUS_LABELS[s]), ['Aplicado', 'Aplicado parcialmente', 'Saldo a favor']);
    for (const status of STATUSES) {
      assert.ok(STATUS_BADGES[status].length > 0);
      assert.ok(STATUS_HELP[status].length > 0);
    }
  });

  it('COP amounts, calendar dates without a time-zone shift and UTC instants in Bogotá time', () => {
    assert.equal(spaces(formatCop('150000.00')), '$ 150.000,00');
    assert.equal(formatDate('2026-01-01'), '1 de enero de 2026'); // never the 31st of December
    assert.equal(formatDate('2026-12-31'), '31 de diciembre de 2026');
    // 03:05 UTC on the 3rd is still the 2nd in Bogotá (UTC-5).
    assert.match(spaces(formatDateTime('2026-10-03T03:05:00Z')), /^2 de octubre de 2026\b.*10:05 p\. m\.$/);
  });

  it('periods stay the same month (no time zone): "2026-01" is January 2026', () => {
    assert.equal(periodText('2026-01'), 'Enero de 2026');
    assert.equal(periodText('2026-12'), 'Diciembre de 2026');
    assert.equal(periodText(null), '—');
  });

  it('the transaction reference is never invented', () => {
    assert.equal(referenceText(null), NO_REFERENCE);
    assert.equal(referenceText('   '), NO_REFERENCE);
    assert.equal(referenceText('TRF-2026-0042'), 'TRF-2026-0042');
  });
});

// --- List: query --------------------------------------------------------------------------------------

describe('resident payments: query', () => {
  it('only the options with a value travel, never an id of a person', () => {
    assert.equal(toQueryString(DEFAULT_QUERY), '');
    const query = { ...DEFAULT_QUERY, page: 2, pageSize: 25, status: 'CREDIT' as const, dateFrom: '2026-01-01', dateTo: '2026-01-31', search: ' RC-00000012 ' };
    assert.equal(toQueryString(query), 'page=2&page_size=25&status=CREDIT&date_from=2026-01-01&date_to=2026-01-31&search=RC-00000012');
  });

  it('the page URL: valid options are kept; unknown parameters (resident_id, user_id, unit_id) are dropped', () => {
    const query = parseQuery(new URLSearchParams('page=3&page_size=50&status=PARTIALLY_APPLIED&date_from=2026-01-01&date_to=2026-02-01&search=12&resident_id=9&user_id=9&unit_id=9'));
    assert.deepEqual(query, { ...DEFAULT_QUERY, page: 3, pageSize: 50, status: 'PARTIALLY_APPLIED', dateFrom: '2026-01-01', dateTo: '2026-02-01', search: '12' });
    assert.equal(toQueryString(query).includes('_id'), false);
  });

  it('invalid URL options fall back to the defaults', () => {
    const query = parseQuery(new URLSearchParams('page=0&page_size=7&status=Aplicado&date_from=2026-02-30&date_to=x'));
    assert.deepEqual(query, DEFAULT_QUERY);
    // A reversed range is ignored as a whole.
    assert.deepEqual(parseQuery(new URLSearchParams('date_from=2026-02-01&date_to=2026-01-01')), DEFAULT_QUERY);
  });

  it('counts the filters (the date range once)', () => {
    assert.equal(activeFilterCount(DEFAULT_QUERY), 0);
    assert.equal(activeFilterCount({ search: ' ', status: '', dateFrom: '', dateTo: '' }), 0);
    assert.equal(activeFilterCount({ search: '12', status: 'CREDIT', dateFrom: '2026-01-01', dateTo: '2026-01-31' }), 3);
  });
});

// --- List: rows ---------------------------------------------------------------------------------------

describe('resident payments: rows', () => {
  it('six columns as the API sent them, the receipt number and a descriptive link; no notes', () => {
    const row = paymentRow(payment(12, { concept: 'OTHER', concept_description: 'Llaves', method: 'CASH', status: 'CREDIT', transaction_reference: 'TRF-1' }));
    assert.deepEqual(row.cells.map((c) => [c.label, spaces(c.text)]), [
      ['Fecha', '15 de enero de 2026'],
      ['Concepto', 'Otro: Llaves'],
      ['Monto', '$ 150.000,00'],
      ['Método', 'Efectivo'],
      ['Estado', 'Saldo a favor'],
      ['Recibo', 'RC-00000012']
    ]);
    assert.equal(row.href, '/resident/payments/12');
    assert.equal(row.linkLabel, 'Ver detalle del pago RC-00000012 del 15 de enero de 2026');
  });

  it('"Mostrando 11–20 de 42 pagos"', () => {
    const items = Array.from({ length: 10 }, (_, i) => payment(i + 1));
    assert.equal(rangeText(pageOf(items, 2, 42)), 'Mostrando 11–20 de 42 pagos');
    assert.equal(rangeText(pageOf([payment(1)])), 'Mostrando 1–1 de 1 pago');
  });
});

// --- List: controller ---------------------------------------------------------------------------------

describe('resident payments: list', () => {
  it('loading, then the page as the API ordered it (newest first)', async () => {
    const t = harness([pageOf([payment(9), payment(4)])]);
    await t.list.start();
    assert.deepEqual(t.states.map((s) => s.kind), ['loading', 'results']);
    const last = t.last();
    assert.ok(last.kind === 'results');
    assert.deepEqual(last.page.items.map((p) => p.id), [9, 4]);
    assert.deepEqual(t.requests, ['']);
  });

  it('no payments at all vs. no payments for the filters', async () => {
    const empty = harness([pageOf([])]);
    await empty.list.start();
    assert.equal(empty.last().kind, 'empty');
    assert.equal(EMPTY_MESSAGE, 'No tienes pagos registrados.');

    const filtered = harness([pageOf([])], 'status=CREDIT');
    await filtered.list.start();
    assert.equal(filtered.last().kind, 'no-results');
    assert.equal(NO_RESULTS_MESSAGE, 'No encontramos pagos con los filtros seleccionados.');
  });

  it('applying filters or a search goes back to page 1; the page URL keeps them', async () => {
    const t = harness([pageOf([payment(1)], 3, 30), pageOf([payment(2)])], 'page=3');
    await t.list.start();
    await t.list.apply({ search: '  RC-00000012 ', status: 'APPLIED', dateFrom: '2026-01-01', dateTo: '' });
    assert.deepEqual(t.requests, ['page=3', 'status=APPLIED&date_from=2026-01-01&search=RC-00000012']);
    assert.deepEqual(t.remembered, t.requests);
  });

  it('the search is sent as typed (receipt with or without prefix, or a date): the API reads it', async () => {
    const t = harness([]);
    for (const search of ['RC-00000012', '12', '2026-01-15']) await t.list.apply({ search, status: '', dateFrom: '', dateTo: '' });
    assert.deepEqual(t.requests, ['search=RC-00000012', 'search=12', 'search=2026-01-15']);
  });

  it('changing the page keeps the filters; clearing removes them', async () => {
    const t = harness([pageOf([payment(1)], 1, 25), pageOf([payment(2)], 2, 25), pageOf([])], 'status=CREDIT');
    await t.list.start();
    await t.list.goToPage(2);
    await t.list.clear();
    assert.deepEqual(t.requests, ['status=CREDIT', 'page=2&status=CREDIT', '']);
  });

  it('401: the session ended, said once, with no data', async () => {
    const t = harness([apiError(401, 'NOT_AUTHENTICATED', 'Debes iniciar sesión para continuar.')]);
    await t.list.start();
    const last = t.last();
    assert.ok(last.kind === 'error');
    assert.equal(last.message, SESSION_EXPIRED_MESSAGE);
    assert.equal(last.sessionExpired, true);
  });

  it('403, a server error and no connection: plain messages, never technical text', async () => {
    for (const [error, message] of [
      [apiError(403, 'FORBIDDEN', 'No tienes permiso para realizar esta acción.'), FORBIDDEN_MESSAGE],
      [apiError(500, 'INTERNAL_ERROR', 'Traceback (most recent call last)'), CONNECTION_ERROR],
      [new TypeError('Failed to fetch'), CONNECTION_ERROR]
    ] as const) {
      const t = harness([error]);
      await t.list.start();
      const last = t.last();
      assert.ok(last.kind === 'error');
      assert.equal(last.message, message);
      assert.deepEqual(last.fieldErrors, {});
    }
    assert.equal(FORBIDDEN_MESSAGE, 'No tienes permiso para acceder a esta sección.');
    assert.equal(CONNECTION_ERROR, 'No fue posible cargar tus pagos. Intenta nuevamente.');
  });

  it('422: the API message goes next to its filter', async () => {
    const t = harness([apiError(422, 'VALIDATION_ERROR', 'Los datos enviados no son válidos.', [
      { field: 'query.search', message: 'Busca por número de recibo (p. ej., RC-00000012 o 12) o por fecha (AAAA-MM-DD).' }
    ])]);
    await t.list.apply({ search: 'Laura', status: '', dateFrom: '', dateTo: '' });
    const last = t.last();
    assert.ok(last.kind === 'error');
    assert.equal(last.message, INVALID_FILTERS_MESSAGE);
    assert.deepEqual(last.fieldErrors, { search: 'Busca por número de recibo (p. ej., RC-00000012 o 12) o por fecha (AAAA-MM-DD).' });
  });

  it('an older answer never replaces a newer one', async () => {
    let release: (page: ResidentPaymentPage) => void = () => {};
    const states: ResidentPaymentsState[] = [];
    let calls = 0;
    const list = createResidentPaymentsList(DEFAULT_QUERY, {
      load: () => (++calls === 1 ? new Promise((resolve) => { release = resolve; }) : Promise.resolve(pageOf([payment(2)]))),
      render: (state) => states.push(state),
      remember: () => {}
    });
    const first = list.start();
    await list.apply({ search: '2', status: '', dateFrom: '', dateTo: '' });
    release(pageOf([payment(1)]));
    await first;
    const last = states[states.length - 1];
    assert.ok(last.kind === 'results');
    assert.deepEqual(last.page.items.map((p) => p.id), [2]);
  });
});

// --- Detail -------------------------------------------------------------------------------------------

describe('resident payments: detail', () => {
  it('main data: receipt number and transaction reference are two different things', () => {
    const lines = Object.fromEntries(mainLines(DETAIL).map(({ label, value }) => [label, spaces(value)]));
    assert.deepEqual(lines, {
      'Fecha del pago': '15 de enero de 2026',
      Concepto: 'Mensualidad',
      Monto: '$ 80.000,00',
      Método: 'Transferencia',
      Unidad: 'B1-TO1-APTO-101',
      'Registrado el': spaces(formatDateTime('2026-01-15T20:05:00Z')),
      'Número de recibo': 'RC-00000012',
      'Referencia de transacción': 'TRF-2026-0042'
    });
    const withoutReference = Object.fromEntries(mainLines({ ...DETAIL, transaction_reference: null }).map((l) => [l.label, l.value]));
    assert.equal(withoutReference['Referencia de transacción'], 'No registrada');
    assert.equal(withoutReference['Número de recibo'], 'RC-00000012');
  });

  it('paid, applied and credit exactly as the API sent them (never recalculated)', () => {
    assert.deepEqual(financialLines(DETAIL).map((l) => [l.label, spaces(l.value)]), [
      ['Monto pagado', '$ 80.000,00'],
      ['Monto aplicado', '$ 50.000,00'],
      ['Saldo a favor', '$ 30.000,00']
    ]);
    // Inconsistent numbers from the API are shown as they are: the backend is the authority.
    assert.equal(spaces(financialLines({ ...DETAIL, credit_amount: '1.00' })[2].value), '$ 1,00');
  });

  it('the administration\'s note, or "Sin notas"', () => {
    assert.deepEqual(notesOf(DETAIL), { text: 'Pagó en la portería.\nTrajo el soporte.', empty: false });
    assert.deepEqual(notesOf({ ...DETAIL, notes: null }), { text: NO_NOTES, empty: true });
    assert.deepEqual(notesOf({ ...DETAIL, notes: '  ' }), { text: NO_NOTES, empty: true });
  });

  it('the obligations covered, with period, due date, the obligation\'s value and what was applied', () => {
    assert.deepEqual(allocationRows(DETAIL).map((r) => ({ ...r, chargeAmount: spaces(r.chargeAmount), allocatedAmount: spaces(r.allocatedAmount) })), [
      { concept: 'Mensualidad', period: 'Enero de 2026', dueDate: '5 de enero de 2026', chargeAmount: '$ 100.000,00', allocatedAmount: '$ 20.000,00' },
      { concept: 'Penalización: Ruido', period: '—', dueDate: '10 de febrero de 2026', chargeAmount: '$ 50.000,00', allocatedAmount: '$ 30.000,00' }
    ]);
    assert.deepEqual(allocationRows({ ...DETAIL, allocations: [] }), []);
    assert.equal(NO_ALLOCATIONS, 'Este pago no se ha aplicado a ninguna obligación.');
  });

  it('loading the page: 404 (missing or of another resident) and invalid ids are "not found"; 401, 403, errors', async () => {
    const requested: string[] = [];
    const failing = (error: Error) => async (id: string) => {
      requested.push(id);
      throw error;
    };
    assert.deepEqual(await loadDetailPage('12', async () => DETAIL), { kind: 'payment', payment: DETAIL });
    assert.deepEqual(await loadDetailPage('12', failing(apiError(404, 'PAYMENT_NOT_FOUND', 'El pago no existe.'))), { kind: 'not-found' });
    assert.deepEqual(await loadDetailPage('12', failing(apiError(401, 'NOT_AUTHENTICATED', 'x'))), { kind: 'unauthenticated' });
    assert.deepEqual(await loadDetailPage('12', failing(apiError(403, 'FORBIDDEN', 'x'))), { kind: 'forbidden' });
    assert.deepEqual(await loadDetailPage('12', failing(apiError(500, 'INTERNAL_ERROR', 'x'))), { kind: 'error' });
    assert.deepEqual(await loadDetailPage('12', failing(new TypeError('fetch failed'))), { kind: 'error' });
    assert.deepEqual(requested, ['12', '12', '12', '12', '12']);
    // Invalid ids never reach the API.
    for (const id of [undefined, '0', '-1', 'abc', '12abc', '1'.repeat(20), '../admin']) {
      assert.deepEqual(await loadDetailPage(id, failing(new Error('never called'))), { kind: 'not-found' });
      assert.equal(isPaymentId(id), false);
    }
    assert.equal(requested.length, 5);
    assert.equal(DETAIL_FORBIDDEN, 'No tienes permiso para acceder a esta sección.');
  });
});
