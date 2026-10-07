/**
 * Obligations extension: monthly generation, new obligation, apartment and origin
 * pickers, obligations list and detail. The API is a fake injected into each
 * controller (no server). Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ApiError } from '../src/lib/api/errors.ts';
import type {
  Charge,
  ChargeCreated,
  ChargeDetail,
  ChargePage,
  ChargePayload,
  MonthlyGenerationPayload,
  MonthlyGenerationResult,
  MonthlyPreview
} from '../src/lib/api/charges.ts';
import type { BlockList, TowerList, UnitList } from '../src/lib/api/structure.ts';
import type { SessionInfo } from '../src/lib/api/auth.ts';
import { decidePageAccess } from '../src/lib/auth/page-access.ts';
import {
  CONNECTION_ERROR,
  CSRF_MESSAGE,
  STACKED_CELL,
  STATUS_LABELS,
  errorMessage,
  fieldErrorsOf,
  formatPeriod,
  isPeriod
} from '../src/lib/obligations/charge-format.ts';
import {
  ALL_EXIST_MESSAGE,
  FORBIDDEN_MESSAGE as MONTHLY_FORBIDDEN,
  INITIAL_STATE,
  OUTDATED_MESSAGE,
  RERUN_HINT,
  UNKNOWN_OUTCOME,
  canConfirm,
  createMonthlyGeneration,
  generationPayload,
  otherAmountWarning,
  outdatedPreview,
  periodListPath,
  previewLines,
  previewQuery,
  previewText,
  resultIssues,
  resultLines,
  validateMonthly,
  type MonthlyState,
  type MonthlyValues
} from '../src/lib/obligations/monthly-form.ts';
import {
  EMPTY_VALUES,
  FORBIDDEN_MESSAGE as CHARGE_FORBIDDEN,
  NO_SOURCES,
  SOURCE_PAGE_SIZE,
  confirmationOf,
  createChargeFlow,
  createSourcePicker,
  fieldsFor,
  sourceOptionText,
  sourceQuery,
  stateAfterError,
  validateChargeForm,
  type ChargeFormValues,
  type ChargeSubmitState,
  type SourceState
} from '../src/lib/obligations/charge-form.ts';
import {
  COLUMNS,
  CONNECTION_ERROR as LIST_CONNECTION_ERROR,
  DEFAULT_QUERY,
  FORBIDDEN_MESSAGE as LIST_FORBIDDEN,
  activeFilterCount,
  chargeCells,
  createObligationsList,
  dateRangeError,
  filtersText,
  firstOrderOf,
  parseQuery,
  rangeText,
  toQueryString,
  type ChargeQuery,
  type ObligationsListState
} from '../src/lib/obligations/obligations-list.ts';
import { allocationRows, authorText, mainLines, relationsOf, titleOf } from '../src/lib/obligations/charge-detail.ts';
import { createUnitPicker, idOf, type UnitPickerState } from '../src/lib/obligations/unit-picker.ts';

const apiError = (status: number, code = 'X', message = 'x', details: { field?: string; message: string }[] = []) =>
  new ApiError(status, { error: { code, message, details } });
const nbsp = (text: string) => text.replace(/\s/g, ' ');

/** A promise resolved by hand (a request still on its way). */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function preview(overrides: Partial<MonthlyPreview> = {}): MonthlyPreview {
  return {
    billing_period: '2026-10',
    amount: '180000.00',
    due_date: '2026-10-10',
    active_units: 336,
    existing: 0,
    missing: 336,
    to_create: 336,
    total_amount: '60480000.00',
    existing_with_other_amount: 0,
    preview_fingerprint: 'a'.repeat(64),
    ...overrides
  };
}

function generation(overrides: Partial<MonthlyGenerationResult> = {}): MonthlyGenerationResult {
  return {
    billing_period: '2026-10',
    amount: '180000.00',
    due_date: '2026-10-10',
    units_evaluated: 336,
    created: 336,
    existing: 0,
    omitted: 0,
    failed: 0,
    generated_total: '60480000.00',
    credit_applied_total: '0.00',
    credit_applied_to_new_charges: '0.00',
    units_with_credit_applied: 0,
    outstanding_total: '60480000.00',
    omitted_units: [],
    errors: [],
    audit_recorded: true,
    ...overrides
  };
}

function charge(id: number, overrides: Partial<Charge> = {}): Charge {
  return {
    id,
    unit_id: 3,
    unit_label: 'B1-TO2-APTO-104',
    unit_deleted: false,
    concept: 'MONTHLY_FEE',
    description: null,
    billing_period: '2026-09',
    due_date: '2026-09-10',
    amount: '180000.00',
    applied: '50000.00',
    outstanding: '130000.00',
    status: 'OVERDUE',
    source_charge_id: null,
    created_at: '2026-09-01T13:00:00Z',
    ...overrides
  };
}

function pageOf(items: Charge[], page = 1, pageSize = 10, total = items.length): ChargePage {
  return { items, page, page_size: pageSize, total, total_pages: Math.max(1, Math.ceil(total / pageSize)) };
}

const VALUES: MonthlyValues = { period: '2026-10', amount: '180000', dueDate: '2026-10-10' };

// --- Monthly generation -------------------------------------------------------------------------------------

type Answer<T> = T | Error | (() => T | Error | Promise<T>);

function monthlySetup(previews: Answer<MonthlyPreview>[] = [preview()], generations: Answer<MonthlyGenerationResult>[] = [generation()]) {
  const previewRequests: string[] = [];
  const sent: MonthlyGenerationPayload[] = [];
  const states: MonthlyState[] = [];
  const navigations: string[] = [];
  const generated: MonthlyGenerationResult[] = [];
  const answer = async <T>(list: Answer<T>[]): Promise<T> => {
    const next = list.length > 1 ? list.shift()! : list[0];
    const result = typeof next === 'function' ? await (next as () => T | Error | Promise<T>)() : next;
    if (result instanceof Error) throw result;
    return result;
  };
  const flow = createMonthlyGeneration({
    preview: (queryString) => {
      previewRequests.push(queryString);
      return answer(previews);
    },
    generate: (payload) => {
      sent.push(payload);
      return answer(generations);
    },
    render: (state) => states.push(state),
    navigate: (path) => navigations.push(path),
    onGenerated: (result) => generated.push(result)
  });
  return { flow, previewRequests, sent, states, navigations, generated, phases: () => states.map((s) => s.phase) };
}

describe('monthly generation: form and preview', () => {
  it('starts with nothing to confirm', () => {
    const { flow } = monthlySetup();
    assert.deepEqual(flow.state, INITIAL_STATE);
    assert.equal(canConfirm(flow.state.preview), false);
  });

  it('client checks are only format help', () => {
    assert.deepEqual(validateMonthly({ period: '', amount: '', dueDate: '' }).errors, {
      billing_period: 'Selecciona el periodo.',
      amount: 'Ingresa el monto.',
      due_date: 'Selecciona la fecha de vencimiento.'
    });
    assert.ok(validateMonthly({ ...VALUES, period: '2026-13' }).errors.billing_period);
    assert.ok(validateMonthly({ ...VALUES, amount: '180.000' }).errors.amount);
    assert.ok(validateMonthly({ ...VALUES, dueDate: '2026-02-30' }).errors.due_date);
    // A due date before the period is the backend's rule (422 by field), not checked here.
    assert.deepEqual(validateMonthly({ ...VALUES, dueDate: '2026-09-01' }).errors, {});
  });

  it('"Revisar" only asks for the preview, with the three parameters', async () => {
    const t = monthlySetup();
    await t.flow.review(VALUES);
    assert.deepEqual(t.previewRequests, ['billing_period=2026-10&amount=180000&due_date=2026-10-10']);
    assert.deepEqual(t.sent, []);
    assert.deepEqual(t.phases(), ['reviewing', 'ready']);
    assert.deepEqual(t.flow.state.preview, preview());
    assert.equal(canConfirm(t.flow.state.preview), true);
  });

  it('invalid values never reach the API', async () => {
    const t = monthlySetup();
    await t.flow.review({ ...VALUES, amount: 'abc' });
    assert.deepEqual(t.previewRequests, []);
    assert.ok(t.flow.state.errors.amount);
    assert.equal(t.flow.state.preview, null);
  });

  it('the preview card shows the numbers of the API', () => {
    assert.equal(
      nbsp(previewText(preview())),
      'Octubre de 2026 · $ 180.000,00 por apartamento · vence el 10 de octubre de 2026 · 336 activos · 0 existentes · 336 por crear · total $ 60.480.000,00'
    );
    const other = preview({ active_units: 12, existing: 1, missing: 11, to_create: 11, total_amount: '1980000.00' });
    assert.match(nbsp(previewText(other)), /12 activos · 1 existente · 11 por crear · total \$ 1\.980\.000,00/);
    const lines = Object.fromEntries(previewLines(other).map(({ label, value }) => [label, value]));
    assert.equal(lines['Apartamentos activos'], '12');
    assert.equal(lines['Ya tienen la mensualidad'], '1');
    assert.equal(lines['Sin la mensualidad'], '11');
    assert.equal(lines['Por crear'], '11');
    assert.equal(lines['Huella de la vista previa'], 'a'.repeat(64));
  });

  it('existing fees with another amount: an informative warning, not a conflict', () => {
    assert.equal(otherAmountWarning(preview()), null);
    assert.match(otherAmountWarning(preview({ existing_with_other_amount: 1 }))!, /^1 apartamento ya tiene/);
    assert.match(otherAmountWarning(preview({ existing_with_other_amount: 4 }))!, /^4 apartamentos ya tienen/);
    assert.equal(canConfirm(preview({ existing_with_other_amount: 4 })), true);
  });

  it('changing period, amount or due date discards the preview and asks for a new review', async () => {
    const t = monthlySetup();
    await t.flow.review(VALUES);
    t.flow.change();
    assert.equal(t.flow.state.phase, 'idle');
    assert.equal(t.flow.state.preview, null);
    await t.flow.confirm({ ...VALUES, amount: '190000' });
    await t.flow.confirm(VALUES);
    assert.deepEqual(t.sent, [], 'no confirmation without a new review');
  });

  it('a preview for older parameters never shows up', async () => {
    const late = deferred<MonthlyPreview>();
    const t = monthlySetup([() => late.promise]);
    const reviewing = t.flow.review(VALUES);
    t.flow.change(); // the administrator typed meanwhile
    late.resolve(preview());
    await reviewing;
    assert.equal(t.flow.state.preview, null);
    assert.equal(t.flow.state.phase, 'idle');
  });

  it('a confirmation always belongs to the current form', async () => {
    const t = monthlySetup();
    await t.flow.review(VALUES);
    // The form changed without the change event (defense in depth): review again.
    await t.flow.confirm({ ...VALUES, dueDate: '2026-10-15' });
    assert.deepEqual(t.sent, []);
    assert.equal(t.flow.state.preview, null);
  });

  it('to_create = 0: "all have it" and nothing to confirm', async () => {
    const full = preview({ existing: 336, missing: 0, to_create: 0, total_amount: '0.00' });
    const t = monthlySetup([full]);
    await t.flow.review(VALUES);
    assert.equal(canConfirm(t.flow.state.preview), false);
    assert.equal(ALL_EXIST_MESSAGE, 'Todas las unidades activas ya tienen esta mensualidad');
    await t.flow.confirm(VALUES);
    assert.deepEqual(t.sent, []);
  });

  it('preview errors: by field (query.x or x), session, permission, connection', async () => {
    const byField = monthlySetup([
      apiError(422, 'VALIDATION_ERROR', 'Datos inválidos', [
        { field: 'query.billing_period', message: 'El periodo debe tener el formato AAAA-MM.' },
        { field: 'due_date', message: 'El vencimiento no puede ser anterior al inicio del periodo.' }
      ])
    ]);
    await byField.flow.review(VALUES);
    assert.deepEqual(byField.flow.state.errors, {
      billing_period: 'El periodo debe tener el formato AAAA-MM.',
      due_date: 'El vencimiento no puede ser anterior al inicio del periodo.'
    });
    assert.equal(byField.flow.state.notice, null);

    const expired = monthlySetup([apiError(401, 'NOT_AUTHENTICATED')]);
    await expired.flow.review(VALUES);
    assert.deepEqual(expired.navigations, ['/login']);

    const resident = monthlySetup([apiError(403, 'FORBIDDEN')]);
    await resident.flow.review(VALUES);
    assert.deepEqual(resident.flow.state.notice, { tone: 'error', text: MONTHLY_FORBIDDEN });

    const offline = monthlySetup([new TypeError('Failed to fetch')]);
    await offline.flow.review(VALUES);
    assert.deepEqual(offline.flow.state.notice, { tone: 'error', text: CONNECTION_ERROR });
    assert.equal(offline.flow.state.preview, null);
  });
});

describe('monthly generation: confirmation', () => {
  it('sends exactly the parameters, the count and the fingerprint of the preview', async () => {
    const t = monthlySetup([preview({ to_create: 330, preview_fingerprint: 'b'.repeat(64) })]);
    await t.flow.review(VALUES);
    await t.flow.confirm(VALUES);
    assert.deepEqual(t.sent, [
      {
        billing_period: '2026-10',
        amount: '180000',
        due_date: '2026-10-10',
        expected_to_create: 330,
        preview_fingerprint: 'b'.repeat(64)
      }
    ]);
    assert.deepEqual(Object.keys(t.sent[0]).sort(), [
      'amount',
      'billing_period',
      'due_date',
      'expected_to_create',
      'preview_fingerprint'
    ]);
    assert.deepEqual(generationPayload({ billing_period: '2026-10', amount: '1', due_date: '2026-10-10' }, preview()), {
      billing_period: '2026-10',
      amount: '1',
      due_date: '2026-10-10',
      expected_to_create: 336,
      preview_fingerprint: 'a'.repeat(64)
    });
  });

  it('double click: one request, "Generando…" while it runs', async () => {
    const answer = deferred<MonthlyGenerationResult>();
    const t = monthlySetup([preview()], [() => answer.promise]);
    await t.flow.review(VALUES);
    const first = t.flow.confirm(VALUES);
    const second = t.flow.confirm(VALUES);
    await t.flow.review(VALUES); // and "Revisar" is ignored while generating
    t.flow.change();
    assert.equal(t.flow.state.phase, 'generating');
    answer.resolve(generation());
    await Promise.all([first, second]);
    assert.equal(t.sent.length, 1);
    assert.equal(t.previewRequests.length, 1);
    assert.equal(t.flow.state.phase, 'done');
  });

  it('the result: totals, the list is refreshed and the preview is discarded', async () => {
    const t = monthlySetup();
    await t.flow.review(VALUES);
    await t.flow.confirm(VALUES);
    assert.equal(t.generated.length, 1);
    assert.equal(t.flow.state.preview, null);
    await t.flow.confirm(VALUES);
    assert.equal(t.sent.length, 1, 'a new generation needs a new review');
    const result = generation({
      units_evaluated: 336,
      created: 330,
      existing: 4,
      omitted: 1,
      failed: 1,
      generated_total: '59400000.00',
      credit_applied_total: '950000.00',
      credit_applied_to_new_charges: '900000.00',
      units_with_credit_applied: 5,
      outstanding_total: '58500000.00'
    });
    const lines = Object.fromEntries(resultLines(result).map(({ label, value }) => [label, nbsp(value)]));
    assert.deepEqual(lines, {
      'Apartamentos evaluados': '336',
      Creadas: '330',
      'Ya existían': '4',
      Omitidas: '1',
      Fallidas: '1',
      'Total generado': '$ 59.400.000,00',
      'Saldo a favor aplicado': '$ 950.000,00',
      'Saldo a favor aplicado a las nuevas': '$ 900.000,00',
      'Apartamentos con saldo a favor aplicado': '5',
      'Saldo pendiente generado': '$ 58.500.000,00'
    });
    assert.equal(periodListPath('2026-10'), '/admin/obligations?billing_period=2026-10');
  });

  it('omitted units and errors: labels, reasons and the rerun hint', () => {
    assert.equal(resultIssues(generation()), null);
    const issues = resultIssues(
      generation({
        omitted: 1,
        failed: 1,
        omitted_units: [{ unit_id: 51, label: 'B2-TO1-APTO-301', code: 'UNIT_DELETED', message: 'El apartamento fue eliminado.' }],
        errors: [{ unit_id: 77, label: 'B3-TO2-APTO-104', code: 'INTERNAL_ERROR', message: 'No fue posible generar la mensualidad.' }]
      })
    );
    assert.deepEqual(issues, {
      omitted: [{ label: 'B2-TO1-APTO-301', message: 'El apartamento fue eliminado.' }],
      failed: [{ label: 'B3-TO2-APTO-104', message: 'No fue posible generar la mensualidad.' }],
      hint: RERUN_HINT
    });
    assert.equal(RERUN_HINT, 'Puedes volver a ejecutar la generación: solo se crearán las faltantes.');
  });

  it('409 MONTHLY_PREVIEW_OUTDATED: the new preview replaces the old one, nothing is generated automatically', async () => {
    const current = preview({ existing: 2, missing: 334, to_create: 334, total_amount: '60120000.00', preview_fingerprint: 'c'.repeat(64) });
    const outdated = new ApiError(409, {
      error: { code: 'MONTHLY_PREVIEW_OUTDATED', message: OUTDATED_MESSAGE, details: [], preview: current } as never
    });
    const t = monthlySetup([preview()], [outdated, generation({ created: 334 })]);
    await t.flow.review(VALUES);
    await t.flow.confirm(VALUES);
    assert.equal(t.sent.length, 1, 'no automatic retry');
    assert.deepEqual(t.flow.state.preview, current);
    assert.equal(t.flow.state.phase, 'ready');
    assert.deepEqual(t.flow.state.notice, { tone: 'warning', text: 'Los datos cambiaron desde la vista previa. Revisa y confirma de nuevo.' });
    assert.equal(t.previewRequests.length, 1, 'the fingerprint is never recomputed or asked for again');
    // The administrator confirms again, explicitly: with the new count and fingerprint.
    await t.flow.confirm(VALUES);
    assert.equal(t.sent[1].expected_to_create, 334);
    assert.equal(t.sent[1].preview_fingerprint, 'c'.repeat(64));
    assert.equal(t.flow.state.phase, 'done');
  });

  it('409 outdated whose new preview creates nothing: no confirmation offered', async () => {
    const full = preview({ existing: 336, missing: 0, to_create: 0, total_amount: '0.00' });
    const outdated = new ApiError(409, {
      error: { code: 'MONTHLY_PREVIEW_OUTDATED', message: OUTDATED_MESSAGE, details: [], preview: full } as never
    });
    const t = monthlySetup([preview()], [outdated]);
    await t.flow.review(VALUES);
    await t.flow.confirm(VALUES);
    assert.equal(canConfirm(t.flow.state.preview), false);
    await t.flow.confirm(VALUES);
    assert.equal(t.sent.length, 1);
  });

  it('outdatedPreview reads only that 409', () => {
    const body = (code: string, extra: object) => ({ error: { code, message: 'x', details: [], ...extra } }) as never;
    assert.deepEqual(outdatedPreview(new ApiError(409, body('MONTHLY_PREVIEW_OUTDATED', { preview: preview() }))), preview());
    assert.equal(outdatedPreview(new ApiError(409, body('MONTHLY_FEE_EXISTS', { preview: preview() }))), null);
    assert.equal(outdatedPreview(new ApiError(409, body('MONTHLY_PREVIEW_OUTDATED', {}))), null);
    assert.equal(outdatedPreview(new ApiError(409, null)), null);
    assert.equal(outdatedPreview(new TypeError('x')), null);
  });

  it('confirmation errors: CSRF, permission, 422 and an unknown outcome ask for a new review', async () => {
    for (const [error, text] of [
      [apiError(403, 'CSRF_FAILED', 'Solicitud no válida'), CSRF_MESSAGE],
      [apiError(403, 'FORBIDDEN'), MONTHLY_FORBIDDEN],
      [new TypeError('Failed to fetch'), UNKNOWN_OUTCOME],
      [apiError(500, 'INTERNAL_ERROR', 'Ocurrió un error interno.'), UNKNOWN_OUTCOME]
    ] as const) {
      const t = monthlySetup([preview()], [error]);
      await t.flow.review(VALUES);
      await t.flow.confirm(VALUES);
      assert.deepEqual(t.flow.state.notice, { tone: 'error', text }, String(error));
      assert.equal(t.flow.state.preview, null);
      assert.equal(t.flow.state.phase, 'idle');
    }
    const invalid = monthlySetup(
      [preview()],
      [apiError(422, 'VALIDATION_ERROR', 'Revisa la vista previa antes de confirmar.', [{ field: 'preview_fingerprint', message: 'Revisa la vista previa antes de confirmar.' }])]
    );
    await invalid.flow.review(VALUES);
    await invalid.flow.confirm(VALUES);
    assert.deepEqual(invalid.flow.state.notice, { tone: 'error', text: 'Revisa la vista previa antes de confirmar.' });
    assert.equal(invalid.flow.state.preview, null);

    const expired = monthlySetup([preview()], [apiError(401, 'NOT_AUTHENTICATED')]);
    await expired.flow.review(VALUES);
    await expired.flow.confirm(VALUES);
    assert.deepEqual(expired.navigations, ['/login']);
  });

  it('the preview query string carries only the three parameters', () => {
    assert.equal(previewQuery({ billing_period: '2026-10', amount: '180000.50', due_date: '2026-10-10' }),
      'billing_period=2026-10&amount=180000.50&due_date=2026-10-10');
  });
});

// --- New obligation -----------------------------------------------------------------------------------------

const FORM: ChargeFormValues = { ...EMPTY_VALUES, unitId: 3, amount: '50000', dueDate: '2026-10-20' };

function created(overrides: Partial<ChargeCreated> = {}): ChargeCreated {
  return {
    id: 91,
    unit_id: 3,
    unit_label: 'B1-TO2-APTO-104',
    concept: 'PENALTY',
    description: 'Ruido',
    billing_period: null,
    due_date: '2026-10-20',
    amount: '50000.00',
    applied: '20000.00',
    outstanding: '30000.00',
    status: 'PARTIAL',
    source_charge_id: null,
    created_by_user_id: 1,
    created_at: '2026-10-06T15:00:00Z',
    credit_applied: [{ payment_id: 5, receipt_number: 'RC-00000005', charge_id: 91, amount: '20000.00' }],
    account: { pending_balance: '30000.00', credit_balance: '0.00' },
    ...overrides
  };
}

describe('new obligation: fields by concept', () => {
  it('Mensualidad: period; Mora: optional description and origin; Penalización and Otro: required description', () => {
    assert.deepEqual(fieldsFor('MONTHLY_FEE'), { period: true, description: 'none', source: false });
    assert.deepEqual(fieldsFor('LATE_FEE'), { period: false, description: 'optional', source: true });
    assert.deepEqual(fieldsFor('PENALTY'), { period: false, description: 'required', source: false });
    assert.deepEqual(fieldsFor('OTHER'), { period: false, description: 'required', source: false });
    assert.deepEqual(fieldsFor(''), { period: false, description: 'none', source: false });
  });

  it('common required fields', () => {
    assert.deepEqual(validateChargeForm(EMPTY_VALUES).errors, {
      unit_id: 'Selecciona el apartamento.',
      concept: 'Selecciona el concepto.',
      amount: 'Ingresa el monto.',
      due_date: 'Selecciona la fecha de vencimiento.'
    });
    assert.ok(validateChargeForm({ ...FORM, concept: 'OTHER', description: 'x', amount: '0' }).errors.amount);
    assert.ok(validateChargeForm({ ...FORM, concept: 'OTHER', description: 'x', dueDate: '2026-13-01' }).errors.due_date);
    assert.ok(validateChargeForm({ ...FORM, concept: 'REFUND' }).errors.concept);
  });

  it('Mensualidad needs its period and never sends a description or an origin', () => {
    assert.deepEqual(validateChargeForm({ ...FORM, concept: 'MONTHLY_FEE' }).errors, {
      billing_period: 'Selecciona el periodo de la mensualidad.'
    });
    const { payload } = validateChargeForm({ ...FORM, concept: 'MONTHLY_FEE', period: '2026-10', description: 'x', sourceId: 4 });
    assert.deepEqual(payload, { unit_id: 3, concept: 'MONTHLY_FEE', amount: '50000', due_date: '2026-10-20', billing_period: '2026-10' });
  });

  it('Mora: the chosen origin and an optional description', () => {
    assert.deepEqual(validateChargeForm({ ...FORM, concept: 'LATE_FEE', sourceId: 12, description: '  Mora  de   septiembre ' }).payload, {
      unit_id: 3,
      concept: 'LATE_FEE',
      amount: '50000',
      due_date: '2026-10-20',
      description: 'Mora de septiembre',
      source_charge_id: 12
    });
    assert.deepEqual(validateChargeForm({ ...FORM, concept: 'LATE_FEE' }).payload, {
      unit_id: 3,
      concept: 'LATE_FEE',
      amount: '50000',
      due_date: '2026-10-20'
    });
  });

  it('Penalización and Otro require the description; the origin is never sent for them', () => {
    for (const concept of ['PENALTY', 'OTHER']) {
      assert.deepEqual(validateChargeForm({ ...FORM, concept }).errors, { description: 'Escribe la descripción.' }, concept);
      assert.ok(validateChargeForm({ ...FORM, concept, description: 'x'.repeat(201) }).errors.description);
      assert.deepEqual(validateChargeForm({ ...FORM, concept, description: 'Ruido', sourceId: 4, period: '2026-10' }).payload, {
        unit_id: 3,
        concept,
        amount: '50000',
        due_date: '2026-10-20',
        description: 'Ruido'
      });
    }
  });

  it('never sends values the server computes', () => {
    const { payload } = validateChargeForm({ ...FORM, concept: 'OTHER', description: 'Llaves' });
    for (const field of ['id', 'status', 'applied', 'outstanding', 'created_by_user_id', 'credit_applied', 'account']) {
      assert.equal(field in payload!, false, field);
    }
  });
});

function chargeSetup(answers: (ChargeCreated | Error | Promise<ChargeCreated>)[]) {
  const sent: ChargePayload[] = [];
  const states: ChargeSubmitState[] = [];
  const navigations: string[] = [];
  const createdList: ChargeCreated[] = [];
  const flow = createChargeFlow({
    create: async (payload) => {
      sent.push(payload);
      const next = answers.length > 1 ? answers.shift()! : answers[0];
      const result = await next;
      if (result instanceof Error) throw result;
      return result;
    },
    render: (state) => states.push(state),
    navigate: (path) => navigations.push(path),
    onCreated: (c) => createdList.push(c)
  });
  return { flow, sent, states, navigations, createdList };
}

describe('new obligation: submit', () => {
  const PENALTY: ChargeFormValues = { ...FORM, concept: 'PENALTY', description: 'Ruido' };

  it('success: the confirmation, a link to its detail and the list is refreshed', async () => {
    const t = chargeSetup([created()]);
    await t.flow.submit(PENALTY);
    assert.deepEqual(t.states.map((s) => s.kind), ['submitting', 'done']);
    assert.equal(t.createdList.length, 1);
    const done = t.states[1] as Extract<ChargeSubmitState, { kind: 'done' }>;
    assert.equal(done.confirmation.href, '/admin/obligations/91');
    assert.equal(done.confirmation.title, 'Obligación creada correctamente. Obligación n.º 91.');
    const lines = Object.fromEntries(done.confirmation.lines.map(({ label, value }) => [label, nbsp(value)]));
    assert.equal(lines.Concepto, 'Penalización: Ruido');
    assert.equal(lines.Aplicado, '$ 20.000,00');
    assert.equal(lines.Saldo, '$ 30.000,00');
    assert.equal(lines.Estado, 'Parcial');
    assert.equal(lines.Periodo, undefined);
    // The credit is shown as the backend applied it, never computed here.
    assert.deepEqual(done.confirmation.credit.map(nbsp), [
      'Saldo a favor del recibo RC-00000005: $ 20.000,00 aplicado a la obligación n.º 91.'
    ]);
  });

  it('a monthly fee shows its period in the confirmation', () => {
    const fee = confirmationOf(created({ concept: 'MONTHLY_FEE', description: null, billing_period: '2026-10', credit_applied: [] }));
    assert.equal(fee.lines.find((l) => l.label === 'Periodo')?.value, 'Octubre de 2026');
    assert.deepEqual(fee.credit, []);
  });

  it('no double submission', async () => {
    const answer = deferred<ChargeCreated>();
    const t = chargeSetup([answer.promise]);
    const first = t.flow.submit(PENALTY);
    const second = t.flow.submit(PENALTY);
    assert.equal(t.flow.busy, true);
    answer.resolve(created());
    await Promise.all([first, second]);
    assert.equal(t.sent.length, 1);
    assert.equal(t.flow.busy, false);
  });

  it('invalid values are not sent', async () => {
    const t = chargeSetup([created()]);
    await t.flow.submit({ ...FORM, concept: 'OTHER' });
    assert.deepEqual(t.sent, []);
    assert.deepEqual(t.states, [{ kind: 'invalid', errors: { description: 'Escribe la descripción.' } }]);
  });

  it('422 by field, with or without a prefix', () => {
    const error = apiError(422, 'VALIDATION_ERROR', 'Datos inválidos', [
      { field: 'source_charge_id', message: 'La obligación de origen todavía no está vencida.' },
      { field: 'query.unit_id', message: 'Selecciona un apartamento existente.' },
      { field: 'unknown', message: 'ignorado' }
    ]);
    assert.deepEqual(stateAfterError(error), {
      kind: 'error',
      message: null,
      errors: {
        source_charge_id: 'La obligación de origen todavía no está vencida.',
        unit_id: 'Selecciona un apartamento existente.'
      }
    });
    // A 422 without a known field: its message.
    assert.deepEqual(stateAfterError(apiError(422, 'VALIDATION_ERROR', 'Datos inválidos')), {
      kind: 'error',
      message: 'Datos inválidos',
      errors: {}
    });
  });

  it('409 rules: the duplicated monthly fee and late fee point to their field', () => {
    assert.deepEqual(stateAfterError(apiError(409, 'MONTHLY_FEE_EXISTS', 'B1-TO2-APTO-104 ya tiene la mensualidad de 2026-10.')), {
      kind: 'error',
      message: 'B1-TO2-APTO-104 ya tiene la mensualidad de 2026-10.',
      errors: { billing_period: 'B1-TO2-APTO-104 ya tiene la mensualidad de 2026-10.' }
    });
    assert.deepEqual(stateAfterError(apiError(409, 'LATE_FEE_EXISTS', 'La obligación de origen ya tiene una mora.')).errors, {
      source_charge_id: 'La obligación de origen ya tiene una mora.'
    });
    assert.deepEqual(stateAfterError(apiError(409, 'CONFLICT', 'Otro conflicto.')), {
      kind: 'error',
      message: 'Otro conflicto.',
      errors: {}
    });
  });

  it('403, CSRF and unexpected errors: clear messages, never technical ones', () => {
    assert.deepEqual(stateAfterError(apiError(403, 'FORBIDDEN', 'x')), { kind: 'error', message: CHARGE_FORBIDDEN, errors: {} });
    assert.deepEqual(stateAfterError(apiError(403, 'CSRF_FAILED', 'x')), { kind: 'error', message: CSRF_MESSAGE, errors: {} });
    assert.deepEqual(stateAfterError(new TypeError('Failed to fetch')), { kind: 'error', message: CONNECTION_ERROR, errors: {} });
    assert.deepEqual(stateAfterError(new ApiError(502, null)), { kind: 'error', message: CONNECTION_ERROR, errors: {} });
    assert.deepEqual(stateAfterError(apiError(500, 'INTERNAL_ERROR', 'Traceback…')), { kind: 'error', message: CONNECTION_ERROR, errors: {} });
  });

  it('401: back to the login page', async () => {
    const t = chargeSetup([apiError(401, 'NOT_AUTHENTICATED')]);
    await t.flow.submit(PENALTY);
    assert.deepEqual(t.navigations, ['/login']);
    assert.equal(t.flow.busy, false);
  });
});

describe('origin of a late fee', () => {
  function sourceSetup(pages: (ChargePage | Error)[]) {
    const requests: string[] = [];
    const states: SourceState[] = [];
    const navigations: string[] = [];
    const picker = createSourcePicker({
      load: async (queryString) => {
        requests.push(queryString);
        const next = pages.length > 1 ? pages.shift()! : pages[0];
        if (next instanceof Error) throw next;
        return next;
      },
      render: (state) => states.push(state),
      navigate: (path) => navigations.push(path)
    });
    return { picker, requests, states, navigations };
  }

  it('only obligations of the same apartment that are not late fees, from the API pages', async () => {
    const lateFee = charge(3, { concept: 'LATE_FEE', billing_period: null, source_charge_id: 1 });
    const t = sourceSetup([
      pageOf([charge(1), lateFee], 1, SOURCE_PAGE_SIZE, 60),
      pageOf([charge(2, { concept: 'PENALTY', description: 'Ruido', billing_period: null })], 2, SOURCE_PAGE_SIZE, 60)
    ]);
    await t.picker.forUnit(3);
    assert.deepEqual(t.requests, ['unit_id=3&sort_by=due_date&sort_order=desc&page_size=50']);
    assert.equal(t.picker.state.kind, 'ready');
    const ready = t.picker.state as Extract<SourceState, { kind: 'ready' }>;
    assert.deepEqual(ready.options.map((c) => c.id), [1]);
    assert.equal(ready.hasMore, true);
    await t.picker.more();
    assert.equal(t.requests[1], sourceQuery(3, 2));
    assert.match(t.requests[1], /page=2/);
    const after = t.picker.state as Extract<SourceState, { kind: 'ready' }>;
    assert.deepEqual(after.options.map((c) => c.id), [1, 2]);
    assert.equal(after.hasMore, false);
  });

  it('an apartment without possible origins, an error, no apartment', async () => {
    const none = sourceSetup([pageOf([charge(3, { concept: 'LATE_FEE' })])]);
    await none.picker.forUnit(3);
    assert.deepEqual(none.picker.state, { kind: 'none' });
    assert.match(NO_SOURCES, /no tiene obligaciones/);
    const failed = sourceSetup([apiError(403, 'FORBIDDEN')]);
    await failed.picker.forUnit(3);
    assert.equal(failed.picker.state.kind, 'error');
    const expired = sourceSetup([apiError(401, 'NOT_AUTHENTICATED')]);
    await expired.picker.forUnit(3);
    assert.deepEqual(expired.navigations, ['/login']);
    await none.picker.forUnit(null);
    assert.deepEqual(none.picker.state, { kind: 'idle' });
  });

  it('the option text uses the values of the API', () => {
    assert.equal(
      nbsp(sourceOptionText(charge(7))),
      'n.º 7 · Mensualidad · septiembre de 2026 · vence el 10 de septiembre de 2026 · Vencida · saldo $ 130.000,00'
    );
  });
});

describe('apartment picker (HU-013 endpoints)', () => {
  const ref = (id: number, name: string, label = name) => ({ id, number: id, name, label });
  const blocks: BlockList = { items: [{ ...ref(1, 'B1'), version: 1, active_towers: 1, active_units: 2 }], child_label_prefix: 'B1-TO' };
  const towers: TowerList = {
    block: blocks.items[0],
    items: [{ ...ref(10, 'TO2', 'B1-TO2'), block_id: 1, version: 1, active_units: 2, block: ref(1, 'B1') }],
    child_label_prefix: 'B1-TO2-APTO-'
  };
  const units: UnitList = {
    block: ref(1, 'B1'),
    tower: towers.items[0],
    items: [
      { ...ref(100, 'APTO-104', 'B1-TO2-APTO-104'), block_id: 1, tower_id: 10, version: 1, block: ref(1, 'B1'), tower: ref(10, 'TO2', 'B1-TO2') }
    ],
    child_label_prefix: ''
  };

  function pickerSetup(overrides: { towers?: () => Promise<TowerList> } = {}) {
    const calls: string[] = [];
    const states: UnitPickerState[] = [];
    const navigations: string[] = [];
    const picker = createUnitPicker({
      loadBlocks: async () => {
        calls.push('blocks');
        return blocks;
      },
      loadTowers: overrides.towers ?? (async (id) => {
        calls.push(`towers ${id}`);
        return towers;
      }),
      loadUnits: async (id) => {
        calls.push(`units ${id}`);
        return units;
      },
      render: (state) => states.push(state),
      navigate: (path) => navigations.push(path)
    });
    return { picker, calls, states, navigations };
  }

  it('Bloque → Torre → Apartamento; choosing a level clears the ones below', async () => {
    const t = pickerSetup();
    await t.picker.start();
    await t.picker.selectBlock(1);
    await t.picker.selectTower(10);
    t.picker.selectUnit(100);
    assert.deepEqual(t.calls, ['blocks', 'towers 1', 'units 10']);
    assert.deepEqual(t.picker.selection(), { blockId: 1, towerId: 10, unitId: 100 });
    assert.equal(t.picker.unitLabel(), 'B1-TO2-APTO-104');
    await t.picker.selectBlock(1);
    assert.deepEqual(t.picker.selection(), { blockId: 1, towerId: null, unitId: null });
    t.picker.selectUnit(999); // not an option
    assert.equal(t.picker.state.unitId, null);
  });

  it('restores the choice of the page URL', async () => {
    const t = pickerSetup();
    await t.picker.start({ blockId: 1, towerId: 10, unitId: 100 });
    assert.deepEqual(t.picker.selection(), { blockId: 1, towerId: 10, unitId: 100 });
    const gone = pickerSetup();
    await gone.picker.start({ blockId: 5, towerId: 10, unitId: 100 });
    assert.deepEqual(gone.picker.selection(), { blockId: null, towerId: null, unitId: null });
  });

  it('an older answer never replaces a newer choice; 401 → login', async () => {
    const late = deferred<TowerList>();
    const t = pickerSetup({ towers: () => late.promise });
    await t.picker.start();
    const first = t.picker.selectBlock(1);
    t.picker.clear();
    late.resolve(towers);
    await first;
    assert.deepEqual(t.picker.state.towers, []);
    const expired = createUnitPicker({
      loadBlocks: async () => {
        throw apiError(401, 'NOT_AUTHENTICATED');
      },
      loadTowers: async () => towers,
      loadUnits: async () => units,
      render: () => {},
      navigate: (path) => t.navigations.push(path)
    });
    await expired.start();
    assert.deepEqual(t.navigations, ['/login']);
  });

  it('select values', () => {
    assert.equal(idOf(''), null);
    assert.equal(idOf('0'), null);
    assert.equal(idOf('12'), 12);
    assert.equal(idOf('1e3'), null);
  });
});

// --- List ----------------------------------------------------------------------------------------------------

function listSetup(answer: ChargePage | Error | ((queryString: string) => ChargePage | Error), initial: ChargeQuery = DEFAULT_QUERY) {
  const requests: string[] = [];
  const states: ObligationsListState[] = [];
  const urls: string[] = [];
  const navigations: string[] = [];
  const list = createObligationsList(initial, {
    load: async (queryString) => {
      requests.push(queryString);
      const result = typeof answer === 'function' ? answer(queryString) : answer;
      if (result instanceof Error) throw result;
      return result;
    },
    render: (state) => states.push(state),
    remember: (queryString) => urls.push(queryString),
    navigate: (path) => navigations.push(path)
  });
  return { list, requests, states, urls, navigations, last: () => states[states.length - 1] };
}

describe('obligations list: query', () => {
  it('defaults are left out; every filter has its API name', () => {
    assert.equal(toQueryString(DEFAULT_QUERY), '');
    const query: ChargeQuery = {
      ...DEFAULT_QUERY,
      page: 2,
      pageSize: 25,
      blockId: 1,
      towerId: 10,
      unitId: 100,
      concept: 'LATE_FEE',
      status: 'OVERDUE',
      period: '2026-10',
      dueFrom: '2026-10-01',
      dueTo: '2026-10-31',
      sortBy: 'outstanding',
      sortOrder: 'desc'
    };
    const text = toQueryString(query);
    assert.equal(
      text,
      'page=2&page_size=25&block_id=1&tower_id=10&unit_id=100&concept=LATE_FEE&status=OVERDUE&billing_period=2026-10&due_from=2026-10-01&due_to=2026-10-31&sort_by=outstanding&sort_order=desc'
    );
    assert.deepEqual(parseQuery(new URLSearchParams(text)), query);
    assert.doesNotMatch(text, /search/, 'no free search');
  });

  it('invalid or unknown options from the URL fall back to the default', () => {
    const query = parseQuery(
      new URLSearchParams(
        'page=0&page_size=7&concept=REFUND&status=LATE&billing_period=2026-13&due_from=2026-10-31&due_to=2026-10-01&sort_by=id;drop&sort_order=up&search=laura&tower_id=10&unit_id=100'
      )
    );
    assert.deepEqual(query, DEFAULT_QUERY);
    // A tower or an apartment only with its block.
    assert.equal(parseQuery(new URLSearchParams('block_id=1&unit_id=100')).unitId, null);
  });

  it('active filters', () => {
    assert.equal(activeFilterCount(DEFAULT_QUERY), 0);
    assert.equal(activeFilterCount({ ...DEFAULT_QUERY, blockId: 1, towerId: 10, unitId: 100 }), 1);
    assert.equal(activeFilterCount({ ...DEFAULT_QUERY, concept: 'OTHER', status: 'PAID', period: '2026-10', dueFrom: '2026-10-01' }), 4);
    assert.equal(filtersText(0), 'Sin filtros');
    assert.equal(filtersText(1), '1 filtro aplicado');
    assert.equal(filtersText(3), '3 filtros aplicados');
    assert.equal(dateRangeError('2026-10-31', '2026-10-01'), 'La fecha final no puede ser anterior a la inicial.');
    assert.equal(dateRangeError('2026-10-01', ''), null);
  });
});

describe('obligations list: server side', () => {
  const ten = pageOf(Array.from({ length: 10 }, (_, i) => charge(i + 1)), 1, 10, 42);

  it('loads, then shows the page of the API', async () => {
    const t = listSetup(ten);
    await t.list.start();
    assert.deepEqual(t.requests, ['']);
    assert.deepEqual(t.states.map((s) => s.kind), ['loading', 'results']);
    assert.equal(rangeText(ten), 'Mostrando 1–10 de 42 obligaciones');
  });

  it('a filter change goes back to page 1', async () => {
    const t = listSetup(ten, { ...DEFAULT_QUERY, page: 3 });
    await t.list.filter({ status: 'OVERDUE' });
    assert.equal(t.requests[0], 'status=OVERDUE');
    await t.list.goToPage(2);
    await t.list.filter({ blockId: 1, towerId: null, unitId: null });
    assert.equal(t.requests[2], 'block_id=1&status=OVERDUE');
    await t.list.clearFilters();
    assert.equal(t.requests[3], '');
    assert.deepEqual(t.urls, t.requests, 'the options live in the page URL');
  });

  it('sort by the API fields, toggling the direction, back to page 1', async () => {
    const t = listSetup(ten, { ...DEFAULT_QUERY, page: 4 });
    await t.list.sort('amount');
    await t.list.sort('amount');
    await t.list.sort('unit');
    await t.list.sortExactly('created', 'asc');
    await t.list.sortExactly('due_date', 'asc');
    assert.deepEqual(t.requests, ['sort_by=amount&sort_order=desc', 'sort_by=amount', 'sort_by=unit', 'sort_by=created', '']);
    assert.equal(firstOrderOf('due_date'), 'asc');
    assert.equal(firstOrderOf('outstanding'), 'desc');
    assert.equal(firstOrderOf('created'), 'desc');
  });

  it('pagination and page size, always in the server', async () => {
    const t = listSetup(ten);
    await t.list.goToPage(3);
    await t.list.setPageSize(25);
    await t.list.setPageSize(1000);
    assert.deepEqual(t.requests, ['page=3', 'page_size=25', '']);
  });

  it('a creation or a generation refreshes the same page and filters', async () => {
    const t = listSetup(ten, { ...DEFAULT_QUERY, page: 2, concept: 'MONTHLY_FEE' });
    await t.list.start();
    await t.list.refresh();
    assert.deepEqual(t.requests, ['page=2&concept=MONTHLY_FEE', 'page=2&concept=MONTHLY_FEE']);
  });

  it('empty list and no results', async () => {
    const empty = listSetup(pageOf([], 1, 10, 0));
    await empty.list.start();
    assert.equal(empty.last().kind, 'empty');
    await empty.list.filter({ period: '2026-10' });
    assert.equal(empty.last().kind, 'no-results');
  });

  it('errors: session, permission, invalid filter, connection', async () => {
    const expired = listSetup(apiError(401, 'NOT_AUTHENTICATED'));
    await expired.list.start();
    assert.deepEqual(expired.navigations, ['/login']);
    for (const [error, message] of [
      [apiError(403, 'FORBIDDEN'), LIST_FORBIDDEN],
      [apiError(422, 'VALIDATION_ERROR', 'La fecha final no puede ser anterior a la inicial.'), 'La fecha final no puede ser anterior a la inicial.'],
      [new TypeError('Failed to fetch'), LIST_CONNECTION_ERROR]
    ] as const) {
      const t = listSetup(error);
      await t.list.start();
      assert.deepEqual(t.last(), { kind: 'error', query: DEFAULT_QUERY, message });
    }
  });

  it('a page that no longer exists goes to the last one', async () => {
    const t = listSetup((q) => (q === 'page=9' ? pageOf([], 9, 10, 12) : pageOf([charge(11), charge(12)], 2, 10, 12)), {
      ...DEFAULT_QUERY,
      page: 9
    });
    await t.list.start();
    assert.deepEqual(t.requests, ['page=9', 'page=2']);
    assert.equal(t.last().kind, 'results');
  });
});

describe('obligations list: rows and narrow screens', () => {
  it('the 8 columns, with the values of the API (nothing recalculated)', () => {
    // Inconsistent on purpose: the status shown is the API's, not one derived here.
    const row = chargeCells(charge(1, { applied: '180000.00', outstanding: '0.00', status: 'PENDING' }));
    assert.deepEqual(
      row.map((cell) => [cell.label, nbsp(cell.text)]),
      [
        ['Unidad', 'B1-TO2-APTO-104'],
        ['Concepto', 'Mensualidad'],
        ['Periodo', 'septiembre de 2026'],
        ['Vence', '10 de septiembre de 2026'],
        ['Valor', '$ 180.000,00'],
        ['Aplicado', '$ 180.000,00'],
        ['Saldo', '$ 0,00'],
        ['Estado', STATUS_LABELS.PENDING]
      ]
    );
    assert.deepEqual(row.map((cell) => cell.label), COLUMNS.map((column) => column.label));
  });

  it('description, no period and a deleted apartment', () => {
    const row = chargeCells(charge(2, { concept: 'PENALTY', description: 'Ruido', billing_period: null, unit_deleted: true }));
    assert.equal(row[0].note, 'Apartamento eliminado');
    assert.equal(row[1].text, 'Penalización: Ruido');
    assert.equal(row[2].text, '—');
  });

  it('stacked rows: every cell carries its label and no fixed width below 640 px', () => {
    for (const cell of chargeCells(charge(1))) assert.ok(cell.label.length > 0);
    assert.match(STACKED_CELL, /max-sm:before:content-\[attr\(data-label\)\]/);
    assert.match(STACKED_CELL, /max-sm:flex/);
    assert.doesNotMatch(STACKED_CELL, /min-w-\[|w-\[/);
    // Amount columns are aligned to the right in the table.
    assert.deepEqual(COLUMNS.filter((c) => c.numeric).map((c) => c.label), ['Valor', 'Aplicado', 'Saldo']);
  });

  it('every API sort field is reachable from the page', () => {
    const fromHeader = COLUMNS.map((c) => c.field).filter(Boolean);
    assert.deepEqual(new Set([...fromHeader, 'created']), new Set(['due_date', 'unit', 'concept', 'amount', 'outstanding', 'status', 'created']));
  });
});

// --- Detail ----------------------------------------------------------------------------------------------------

function detail(overrides: Partial<ChargeDetail> = {}): ChargeDetail {
  return {
    ...charge(5),
    created_by_user_id: 1,
    source: null,
    late_fees: [charge(9, { concept: 'LATE_FEE', billing_period: null, source_charge_id: 5, amount: '18000.00', status: 'PENDING', due_date: '2026-10-20' })],
    allocations: [
      {
        payment_id: 4,
        receipt_number: 'RC-00000004',
        payment_date: '2026-09-15',
        payment_method: 'TRANSFER',
        applied_amount: '50000.00',
        allocation_date: '2026-09-16T02:30:00Z'
      }
    ],
    ...overrides
  };
}

describe('obligation detail', () => {
  it('main information from the API', () => {
    const lines = Object.fromEntries(mainLines(detail()).map(({ label, value }) => [label, nbsp(value)]));
    assert.deepEqual(lines, {
      Unidad: 'B1-TO2-APTO-104',
      Estado: 'Vencida',
      Concepto: 'Mensualidad',
      Descripción: '—',
      Periodo: 'Septiembre de 2026',
      Vencimiento: '10 de septiembre de 2026',
      Valor: '$ 180.000,00',
      Aplicado: '$ 50.000,00',
      Saldo: '$ 130.000,00',
      'Creada por': authorText(detail()),
      'Fecha de creación': '1 de septiembre de 2026 a las 8:00 a. m.'
    });
    assert.equal(titleOf(detail()), 'Mensualidad de septiembre de 2026');
    assert.match(mainLines(detail({ unit_deleted: true }))[0].value, /apartamento eliminado/);
  });

  it('origin and derived late fees, with links', () => {
    const fee = relationsOf(detail());
    assert.equal(fee.source, null);
    assert.equal(fee.lateFees.length, 1);
    assert.equal(fee.lateFees[0].href, '/admin/obligations/9');
    assert.equal(fee.lateFees[0].status, 'Pendiente');
    assert.match(nbsp(fee.lateFees[0].text), /^n\.º 9 · Mora · vence el 20 de octubre de 2026 · \$ 18\.000,00$/);
    const lateFee = relationsOf(detail({ ...charge(9, { concept: 'LATE_FEE' }), source: charge(5), late_fees: [] }));
    assert.equal(lateFee.source?.href, '/admin/obligations/5');
  });

  it('payments applied: receipt, dates (UTC instant in Bogotá time), method and amount', () => {
    assert.deepEqual(allocationRows(detail()).map((row) => ({ ...row, amount: nbsp(row.amount), appliedAt: nbsp(row.appliedAt) })), [
      {
        receipt: 'RC-00000004',
        paymentDate: '15 de septiembre de 2026',
        method: 'Transferencia',
        amount: '$ 50.000,00',
        appliedAt: '15 de septiembre de 2026 a las 9:30 p. m.'
      }
    ]);
    assert.deepEqual(allocationRows(detail({ allocations: [] })), []);
  });
});

// --- Shared helpers and access ------------------------------------------------------------------------------------

describe('shared helpers', () => {
  it('periods', () => {
    assert.equal(isPeriod('2026-10'), true);
    assert.equal(isPeriod('2026-1'), false);
    assert.equal(isPeriod('2026-00'), false);
    assert.equal(formatPeriod('2026-01'), 'enero de 2026');
  });

  it('field names with or without the query prefix', () => {
    const error = apiError(422, 'VALIDATION_ERROR', 'x', [
      { field: 'query.due_from', message: 'a' },
      { field: 'due_from', message: 'b' },
      { message: 'sin campo' }
    ]);
    assert.deepEqual(fieldErrorsOf(error, ['due_from'] as const), { due_from: 'a' });
  });

  it('error messages', () => {
    assert.equal(errorMessage(apiError(403, 'CSRF_FAILED'), 'f'), CSRF_MESSAGE);
    assert.equal(errorMessage(apiError(403, 'FORBIDDEN'), 'f'), 'f');
    assert.equal(errorMessage(apiError(409, 'X', 'Conflicto.'), 'f'), 'Conflicto.');
    assert.equal(errorMessage(new ApiError(200, null), 'f'), CONNECTION_ERROR);
    assert.equal(errorMessage(new Error('stack'), 'f'), CONNECTION_ERROR);
  });
});

describe('page access', () => {
  const session = (role: 'ADMINISTRADOR' | 'RESIDENTE'): SessionInfo => ({
    user: { id: 1, full_name: 'Admin', email: 'admin@example.com', role },
    csrf_token: 'csrf'
  });

  it('/admin/obligations and its detail: administrator only', async () => {
    for (const path of ['/admin/obligations', '/admin/obligations/5']) {
      assert.equal((await decidePageAccess(path, async () => session('ADMINISTRADOR'))).kind, 'allowed');
      assert.deepEqual(await decidePageAccess(path, async () => session('RESIDENTE')), { kind: 'forbidden' });
      assert.deepEqual(await decidePageAccess(path, async () => null), { kind: 'redirect', location: '/login' });
    }
  });
});
