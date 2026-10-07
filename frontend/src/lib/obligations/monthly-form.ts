import { ApiError } from '../api/errors.ts';
import type { MonthlyGenerationPayload, MonthlyGenerationResult, MonthlyParams, MonthlyPreview } from '../api/charges.ts';
import { formatCop, formatDate, isIsoDate, parseAmount } from '../payments/payment-form.ts';
import { capitalize, errorMessage, fieldErrorsOf, formatPeriod, isPeriod } from './charge-format.ts';

/**
 * Obligations extension: monthly generation with a preview (pure, tested with node --test).
 *
 * "Revisar" only asks for the preview: nothing is created. "Confirmar y generar"
 * sends the same parameters with the count and the fingerprint the backend gave
 * (never computed here). Any change of period, amount or due date discards the
 * preview, and a confirmation always belongs to the preview being shown. If the
 * data changed meanwhile (409 MONTHLY_PREVIEW_OUTDATED), the new preview replaces
 * the old one and the administrator has to confirm again: never automatically.
 * Only the format is checked here; the backend decides every rule.
 */

export const OUTDATED_MESSAGE = 'Los datos cambiaron desde la vista previa. Revisa y confirma de nuevo.';
export const ALL_EXIST_MESSAGE = 'Todas las unidades activas ya tienen esta mensualidad';
export const RERUN_HINT = 'Puedes volver a ejecutar la generación: solo se crearán las faltantes.';
export const SUCCESS_MESSAGE = 'Generación terminada.';
export const UNKNOWN_OUTCOME =
  'No fue posible confirmar el resultado de la generación. Revisa de nuevo antes de volver a confirmar.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para generar mensualidades.';

export type MonthlyField = 'billing_period' | 'amount' | 'due_date';
export const MONTHLY_FIELDS: readonly MonthlyField[] = ['billing_period', 'amount', 'due_date'];
export type MonthlyErrors = Partial<Record<MonthlyField, string>>;

export interface MonthlyValues {
  period: string; // <input type="month">: YYYY-MM
  amount: string;
  dueDate: string; // <input type="date">: YYYY-MM-DD
}

export type MonthlyPhase = 'idle' | 'reviewing' | 'ready' | 'generating' | 'done';

export interface MonthlyState {
  phase: MonthlyPhase;
  /** The preview being shown; it always belongs to the current parameters. */
  preview: MonthlyPreview | null;
  result: MonthlyGenerationResult | null;
  errors: MonthlyErrors;
  notice: { tone: 'error' | 'warning'; text: string } | null;
}

export const INITIAL_STATE: MonthlyState = { phase: 'idle', preview: null, result: null, errors: {}, notice: null };

export function validateMonthly(values: MonthlyValues): { errors: MonthlyErrors; params: MonthlyParams | null } {
  const errors: MonthlyErrors = {};
  if (!values.period) errors.billing_period = 'Selecciona el periodo.';
  else if (!isPeriod(values.period)) errors.billing_period = 'El periodo debe tener el formato AAAA-MM.';
  const amount = parseAmount(values.amount);
  if ('error' in amount) errors.amount = amount.error;
  if (!values.dueDate) errors.due_date = 'Selecciona la fecha de vencimiento.';
  else if (!isIsoDate(values.dueDate)) errors.due_date = 'La fecha de vencimiento no es válida.';
  if (Object.keys(errors).length > 0 || 'error' in amount) return { errors, params: null };
  return { errors, params: { billing_period: values.period, amount: amount.value, due_date: values.dueDate } };
}

export const PREVIEW_OUTDATED = 'MONTHLY_PREVIEW_OUTDATED';

/** Query string of the preview: the three parameters, nothing else. */
export function previewQuery(params: MonthlyParams): string {
  return new URLSearchParams({
    billing_period: params.billing_period,
    amount: params.amount,
    due_date: params.due_date
  }).toString();
}

/** The current preview sent with 409 MONTHLY_PREVIEW_OUTDATED (nothing was created), or null. */
export function outdatedPreview(error: unknown): MonthlyPreview | null {
  if (!(error instanceof ApiError) || error.status !== 409 || error.code !== PREVIEW_OUTDATED) return null;
  const preview = (error.body?.error as { preview?: unknown } | undefined)?.preview;
  return typeof preview === 'object' && preview !== null && typeof (preview as MonthlyPreview).preview_fingerprint === 'string'
    ? (preview as MonthlyPreview)
    : null;
}

/** The confirmation is offered only when it would create something. */
export function canConfirm(preview: MonthlyPreview | null): boolean {
  return preview !== null && preview.to_create > 0;
}

/** Exactly what is sent to confirm: the parameters plus the count and the fingerprint of the preview. */
export function generationPayload(params: MonthlyParams, preview: MonthlyPreview): MonthlyGenerationPayload {
  return {
    billing_period: params.billing_period,
    amount: params.amount,
    due_date: params.due_date,
    expected_to_create: preview.to_create,
    preview_fingerprint: preview.preview_fingerprint
  };
}

const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** "Octubre de 2026 · $ 180.000,00 por apartamento · vence el 10 de octubre de 2026 · 336 activos · …" */
export function previewText(preview: MonthlyPreview): string {
  return [
    capitalize(formatPeriod(preview.billing_period)),
    `${formatCop(preview.amount)} por apartamento`,
    `vence el ${formatDate(preview.due_date)}`,
    count(preview.active_units, 'activo', 'activos'),
    count(preview.existing, 'existente', 'existentes'),
    `${preview.to_create} por crear`,
    `total ${formatCop(preview.total_amount)}`
  ].join(' · ');
}

export function previewLines(preview: MonthlyPreview): { label: string; value: string }[] {
  return [
    { label: 'Periodo', value: capitalize(formatPeriod(preview.billing_period)) },
    { label: 'Monto por apartamento', value: formatCop(preview.amount) },
    { label: 'Vencimiento', value: formatDate(preview.due_date) },
    { label: 'Apartamentos activos', value: String(preview.active_units) },
    { label: 'Ya tienen la mensualidad', value: String(preview.existing) },
    { label: 'Sin la mensualidad', value: String(preview.missing) },
    { label: 'Por crear', value: String(preview.to_create) },
    { label: 'Total a generar', value: formatCop(preview.total_amount) },
    { label: 'Huella de la vista previa', value: preview.preview_fingerprint }
  ];
}

/** Informative only (the API does not treat it as a conflict): those fees are not changed. */
export function otherAmountWarning(preview: MonthlyPreview): string | null {
  const n = preview.existing_with_other_amount;
  if (n === 0) return null;
  return n === 1
    ? '1 apartamento ya tiene esta mensualidad con otro monto. No se modificará.'
    : `${n} apartamentos ya tienen esta mensualidad con otro monto. No se modificarán.`;
}

export function resultLines(result: MonthlyGenerationResult): { label: string; value: string }[] {
  return [
    { label: 'Apartamentos evaluados', value: String(result.units_evaluated) },
    { label: 'Creadas', value: String(result.created) },
    { label: 'Ya existían', value: String(result.existing) },
    { label: 'Omitidas', value: String(result.omitted) },
    { label: 'Fallidas', value: String(result.failed) },
    { label: 'Total generado', value: formatCop(result.generated_total) },
    { label: 'Saldo a favor aplicado', value: formatCop(result.credit_applied_total) },
    { label: 'Saldo a favor aplicado a las nuevas', value: formatCop(result.credit_applied_to_new_charges) },
    { label: 'Apartamentos con saldo a favor aplicado', value: String(result.units_with_credit_applied) },
    { label: 'Saldo pendiente generado', value: formatCop(result.outstanding_total) }
  ];
}

export interface ResultIssues {
  omitted: { label: string; message: string }[];
  failed: { label: string; message: string }[];
  hint: string;
}

/** Omitted units and errors with their labels and reasons, or null when every unit was handled. */
export function resultIssues(result: MonthlyGenerationResult): ResultIssues | null {
  if (result.omitted_units.length === 0 && result.errors.length === 0 && result.omitted === 0 && result.failed === 0) {
    return null;
  }
  return {
    omitted: result.omitted_units.map(({ label, message }) => ({ label, message })),
    failed: result.errors.map(({ label, message }) => ({ label, message })),
    hint: RERUN_HINT
  };
}

/** The list filtered by the generated period. */
export function periodListPath(period: string): string {
  return `/admin/obligations?${new URLSearchParams({ billing_period: period }).toString()}`;
}

const sameParams = (a: MonthlyParams, b: MonthlyParams) =>
  a.billing_period === b.billing_period && a.amount === b.amount && a.due_date === b.due_date;

export function createMonthlyGeneration(deps: {
  /** GET the preview with the query string built by previewQuery. */
  preview: (queryString: string) => Promise<MonthlyPreview>;
  generate: (payload: MonthlyGenerationPayload) => Promise<MonthlyGenerationResult>;
  render: (state: MonthlyState) => void;
  navigate: (path: string) => void;
  /** Invalidation: the list is queried again after a generation. */
  onGenerated: (result: MonthlyGenerationResult) => void;
}) {
  let state: MonthlyState = INITIAL_STATE;
  /** The parameters of the preview being shown (or being asked for). */
  let reviewed: MonthlyParams | null = null;
  let latest = 0;
  let generating = false;

  function set(next: Partial<MonthlyState>): void {
    state = { ...state, ...next };
    deps.render(state);
  }

  function failure(error: unknown): boolean {
    if (error instanceof ApiError && error.status === 401) {
      deps.navigate('/login');
      return true;
    }
    return false;
  }

  return {
    get state(): MonthlyState {
      return state;
    },
    /** Period, amount or due date changed: the preview and any confirmation are discarded. */
    change(): void {
      if (generating) return; // the form is disabled while generating
      latest++; // a preview still on its way belongs to the old parameters
      reviewed = null;
      if (state.phase === 'idle' && !state.preview && !state.notice) return;
      // The last result stays visible; only "Revisar" can lead to a new confirmation.
      set({ ...INITIAL_STATE, errors: state.errors, result: state.result });
    },
    /** "Revisar": asks for the preview; nothing is created. */
    async review(values: MonthlyValues): Promise<void> {
      if (generating) return;
      const { errors, params } = validateMonthly(values);
      if (!params) {
        latest++;
        reviewed = null;
        set({ ...INITIAL_STATE, errors });
        return;
      }
      const request = ++latest;
      reviewed = params;
      set({ ...INITIAL_STATE, phase: 'reviewing' });
      try {
        const preview = await deps.preview(previewQuery(params));
        if (request !== latest) return; // the parameters changed meanwhile
        set({ phase: 'ready', preview, errors: {}, notice: null });
      } catch (error) {
        if (request !== latest) return;
        reviewed = null;
        if (failure(error)) return;
        const fieldErrors = error instanceof ApiError && error.status === 422 ? fieldErrorsOf(error, MONTHLY_FIELDS) : {};
        const message = Object.keys(fieldErrors).length > 0 ? null : errorMessage(error, FORBIDDEN_MESSAGE);
        set({ ...INITIAL_STATE, errors: fieldErrors, notice: message ? { tone: 'error', text: message } : null });
      }
    },
    /** "Confirmar y generar": once, only for the preview being shown, with the current parameters. */
    async confirm(values: MonthlyValues): Promise<void> {
      if (generating || state.phase !== 'ready' || !reviewed || !canConfirm(state.preview)) return;
      const { params } = validateMonthly(values);
      if (!params || !sameParams(params, reviewed)) {
        // The form no longer matches the preview: review again.
        latest++;
        reviewed = null;
        set({ ...INITIAL_STATE });
        return;
      }
      generating = true;
      const payload = generationPayload(reviewed, state.preview!);
      set({ phase: 'generating', notice: null, errors: {} });
      try {
        const result = await deps.generate(payload);
        reviewed = null;
        set({ phase: 'done', preview: null, result, errors: {}, notice: null });
        deps.onGenerated(result);
      } catch (error) {
        if (failure(error)) return;
        const current = outdatedPreview(error);
        if (current) {
          // Nothing was created: the new preview replaces the old one and waits for a new confirmation.
          set({ phase: 'ready', preview: current, notice: { tone: 'warning', text: OUTDATED_MESSAGE } });
          return;
        }
        reviewed = null;
        if (error instanceof ApiError && error.status === 422) {
          const fieldErrors = fieldErrorsOf(error, MONTHLY_FIELDS);
          set({ ...INITIAL_STATE, errors: fieldErrors, notice: { tone: 'error', text: error.message } });
          return;
        }
        if (error instanceof ApiError && error.status === 403) {
          set({ ...INITIAL_STATE, notice: { tone: 'error', text: errorMessage(error, FORBIDDEN_MESSAGE) } });
          return;
        }
        // The outcome is unknown (connection, server): a new review shows what exists now.
        set({ ...INITIAL_STATE, notice: { tone: 'error', text: UNKNOWN_OUTCOME } });
      } finally {
        generating = false;
      }
    }
  };
}
