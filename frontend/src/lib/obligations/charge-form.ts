import { ApiError } from '../api/errors.ts';
import type { Charge, ChargeConcept, ChargeCreated, ChargePage, ChargePayload } from '../api/charges.ts';
import { formatCop, formatDate, isIsoDate, parseAmount } from '../payments/payment-form.ts';
import {
  CONCEPTS,
  CONCEPT_LABELS,
  CONNECTION_ERROR,
  STATUS_LABELS,
  capitalize,
  conceptText,
  errorMessage,
  fieldErrorsOf,
  formatPeriod,
  isPeriod
} from './charge-format.ts';

/**
 * Obligations extension: a new obligation of one apartment (pure, tested with node --test).
 *
 * The fields depend on the concept: a monthly fee has a period and no
 * description; a late fee may have a description and an origin; a penalty and
 * "Otro" need a description. Only the format is checked here; the backend
 * decides every rule (origin, duplicates, due date of a monthly fee) and applies
 * the credit (saldo a favor) on its own: nothing about the balance is computed.
 */

export const DESCRIPTION_MAX_LENGTH = 200;
export const SOURCE_PAGE_SIZE = 50;

export const SUCCESS_MESSAGE = 'Obligación creada correctamente.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para crear obligaciones.';
export const SOURCE_FORBIDDEN = 'No tienes permiso para consultar las obligaciones.';
export const NO_SOURCES = 'Este apartamento no tiene obligaciones que puedan originar una mora.';

export type ChargeField = 'unit_id' | 'concept' | 'amount' | 'due_date' | 'description' | 'billing_period' | 'source_charge_id';
export const CHARGE_FIELDS: readonly ChargeField[] = [
  'unit_id',
  'concept',
  'billing_period',
  'source_charge_id',
  'description',
  'amount',
  'due_date'
];
export type ChargeErrors = Partial<Record<ChargeField, string>>;

export interface ConceptFields {
  period: boolean;
  description: 'none' | 'optional' | 'required';
  source: boolean;
}

/** Which fields the form shows for a concept ("" = none chosen yet: only the common ones). */
export function fieldsFor(concept: string): ConceptFields {
  switch (concept) {
    case 'MONTHLY_FEE':
      return { period: true, description: 'none', source: false };
    case 'LATE_FEE':
      return { period: false, description: 'optional', source: true };
    case 'PENALTY':
    case 'OTHER':
      return { period: false, description: 'required', source: false };
    default:
      return { period: false, description: 'none', source: false };
  }
}

export interface ChargeFormValues {
  unitId: number | null;
  concept: string;
  amount: string;
  dueDate: string;
  description: string;
  period: string;
  sourceId: number | null;
}

export const EMPTY_VALUES: ChargeFormValues = {
  unitId: null,
  concept: '',
  amount: '',
  dueDate: '',
  description: '',
  period: '',
  sourceId: null
};

export function validateChargeForm(values: ChargeFormValues): { errors: ChargeErrors; payload: ChargePayload | null } {
  const errors: ChargeErrors = {};
  if (values.unitId === null) errors.unit_id = 'Selecciona el apartamento.';
  const known = (CONCEPTS as readonly string[]).includes(values.concept);
  if (!known) errors.concept = 'Selecciona el concepto.';
  const fields = fieldsFor(values.concept);
  const amount = parseAmount(values.amount);
  if ('error' in amount) errors.amount = amount.error;
  if (!values.dueDate) errors.due_date = 'Selecciona la fecha de vencimiento.';
  else if (!isIsoDate(values.dueDate)) errors.due_date = 'La fecha de vencimiento no es válida.';
  if (fields.period) {
    if (!values.period) errors.billing_period = 'Selecciona el periodo de la mensualidad.';
    else if (!isPeriod(values.period)) errors.billing_period = 'El periodo debe tener el formato AAAA-MM.';
  }
  const description = values.description.trim().replace(/\s+/g, ' ');
  if (fields.description !== 'none') {
    if (fields.description === 'required' && !description) errors.description = 'Escribe la descripción.';
    else if (description.length > DESCRIPTION_MAX_LENGTH) {
      errors.description = `La descripción no puede superar ${DESCRIPTION_MAX_LENGTH} caracteres.`;
    }
  }
  if (Object.keys(errors).length > 0 || 'error' in amount) return { errors, payload: null };

  // Only the fields of the chosen concept are sent.
  const payload: ChargePayload = {
    unit_id: values.unitId!,
    concept: values.concept as ChargeConcept,
    amount: amount.value,
    due_date: values.dueDate
  };
  if (fields.period) payload.billing_period = values.period;
  if (fields.description !== 'none' && description) payload.description = description;
  if (fields.source && values.sourceId !== null) payload.source_charge_id = values.sourceId;
  return { errors, payload };
}

// --- Confirmation -----------------------------------------------------------------------------------

export function detailPath(id: number): string {
  return `/admin/obligations/${id}`;
}

export interface CreatedConfirmation {
  title: string;
  href: string;
  lines: { label: string; value: string }[];
  /** The credit (saldo a favor) the backend applied, as it answered: receipt, obligation and amount. */
  credit: string[];
}

export function confirmationOf(created: ChargeCreated): CreatedConfirmation {
  const lines = [
    { label: 'Apartamento', value: created.unit_label },
    { label: 'Concepto', value: conceptText(created.concept, created.description) }
  ];
  if (created.billing_period) lines.push({ label: 'Periodo', value: capitalize(formatPeriod(created.billing_period)) });
  lines.push(
    { label: 'Vence', value: formatDate(created.due_date) },
    { label: 'Valor', value: formatCop(created.amount) },
    { label: 'Aplicado', value: formatCop(created.applied) },
    { label: 'Saldo', value: formatCop(created.outstanding) },
    { label: 'Estado', value: STATUS_LABELS[created.status] },
    { label: 'Saldo pendiente del apartamento', value: formatCop(created.account.pending_balance) },
    { label: 'Saldo a favor del apartamento', value: formatCop(created.account.credit_balance) }
  );
  const credit = created.credit_applied.map(
    (c) => `Saldo a favor del recibo ${c.receipt_number}: ${formatCop(c.amount)} aplicado a la obligación n.º ${c.charge_id}.`
  );
  return { title: `${SUCCESS_MESSAGE} Obligación n.º ${created.id}.`, href: detailPath(created.id), lines, credit };
}

// --- Submit flow --------------------------------------------------------------------------------------

export type ChargeSubmitState =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'done'; created: ChargeCreated; confirmation: CreatedConfirmation }
  | { kind: 'invalid'; errors: ChargeErrors }
  | { kind: 'error'; message: string | null; errors: ChargeErrors };

/** 409 codes that belong to a field of the form. */
const CONFLICT_FIELDS: Record<string, ChargeField> = {
  MONTHLY_FEE_EXISTS: 'billing_period',
  LATE_FEE_EXISTS: 'source_charge_id'
};

export function stateAfterError(error: unknown): Extract<ChargeSubmitState, { kind: 'error' }> {
  if (!(error instanceof ApiError)) return { kind: 'error', message: CONNECTION_ERROR, errors: {} };
  if (error.status === 409) {
    const field = CONFLICT_FIELDS[error.code];
    return { kind: 'error', message: error.message, errors: field ? { [field]: error.message } : {} };
  }
  if (error.status === 422) {
    const errors = fieldErrorsOf(error, CHARGE_FIELDS);
    return { kind: 'error', message: Object.keys(errors).length > 0 ? null : error.message, errors };
  }
  return { kind: 'error', message: errorMessage(error, FORBIDDEN_MESSAGE), errors: {} };
}

export function createChargeFlow(deps: {
  create: (payload: ChargePayload) => Promise<ChargeCreated>;
  render: (state: ChargeSubmitState) => void;
  navigate: (path: string) => void;
  /** Invalidation: the list is queried again after a new obligation. */
  onCreated: (created: ChargeCreated) => void;
}) {
  let busy = false;
  return {
    get busy(): boolean {
      return busy;
    },
    async submit(values: ChargeFormValues): Promise<void> {
      if (busy) return; // no double submission
      const { errors, payload } = validateChargeForm(values);
      if (!payload) {
        deps.render({ kind: 'invalid', errors });
        return;
      }
      busy = true;
      deps.render({ kind: 'submitting' });
      try {
        const created = await deps.create(payload);
        deps.render({ kind: 'done', created, confirmation: confirmationOf(created) });
        deps.onCreated(created);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          deps.navigate('/login');
          return;
        }
        deps.render(stateAfterError(error));
      } finally {
        busy = false;
      }
    }
  };
}

// --- Origin of a late fee (the obligations list of the same apartment) ----------------------------------

export type SourceState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; options: Charge[]; hasMore: boolean; loadingMore: boolean }
  | { kind: 'none' }
  | { kind: 'error'; message: string };

/** One page of the apartment's obligations, newest due date first (the API pages and sorts). */
export function sourceQuery(unitId: number, page: number): string {
  const params = new URLSearchParams({
    unit_id: String(unitId),
    sort_by: 'due_date',
    sort_order: 'desc',
    page_size: String(SOURCE_PAGE_SIZE)
  });
  if (page > 1) params.set('page', String(page));
  return params.toString();
}

/** "Mensualidad · octubre de 2026 · vence el 10 de octubre de 2026 · Vencida · saldo $ 180.000,00" */
export function sourceOptionText(charge: Charge): string {
  const parts = [conceptText(charge.concept, charge.description)];
  if (charge.billing_period) parts.push(formatPeriod(charge.billing_period));
  parts.push(`vence el ${formatDate(charge.due_date)}`, STATUS_LABELS[charge.status], `saldo ${formatCop(charge.outstanding)}`);
  return `n.º ${charge.id} · ${parts.join(' · ')}`;
}

export function createSourcePicker(deps: {
  load: (queryString: string) => Promise<ChargePage>;
  render: (state: SourceState) => void;
  navigate: (path: string) => void;
}) {
  let state: SourceState = { kind: 'idle' };
  let unit: number | null = null;
  let page = 0;
  let latest = 0;

  function set(next: SourceState): void {
    state = next;
    deps.render(state);
  }

  async function fetchPage(unitId: number, number: number, previous: Charge[]): Promise<void> {
    const request = ++latest;
    try {
      const result = await deps.load(sourceQuery(unitId, number));
      if (request !== latest) return;
      page = result.page;
      // A late fee never originates another one: those are not offered.
      const options = [...previous, ...result.items.filter((charge) => charge.concept !== 'LATE_FEE')];
      const hasMore = result.page < result.total_pages;
      set(options.length === 0 && !hasMore ? { kind: 'none' } : { kind: 'ready', options, hasMore, loadingMore: false });
    } catch (error) {
      if (request !== latest) return;
      if (error instanceof ApiError && error.status === 401) {
        deps.navigate('/login');
        return;
      }
      set({ kind: 'error', message: errorMessage(error, SOURCE_FORBIDDEN) });
    }
  }

  return {
    get state(): SourceState {
      return state;
    },
    /** The apartment changed (or the late fee was chosen): its obligations, from the first page. */
    async forUnit(unitId: number | null): Promise<void> {
      unit = unitId;
      page = 0;
      if (unitId === null) {
        latest++;
        set({ kind: 'idle' });
        return;
      }
      set({ kind: 'loading' });
      await fetchPage(unitId, 1, []);
    },
    /** "Cargar más": the next page of the API, added to the options. */
    async more(): Promise<void> {
      if (state.kind !== 'ready' || !state.hasMore || state.loadingMore || unit === null) return;
      set({ ...state, loadingMore: true });
      await fetchPage(unit, page + 1, state.options);
    },
    reset(): void {
      unit = null;
      page = 0;
      latest++;
      set({ kind: 'idle' });
    }
  };
}

export { CONCEPTS, CONCEPT_LABELS };
