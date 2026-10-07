import { ApiError } from '../api/errors.ts';
import type { ChargeConcept, ChargeStatus } from '../api/charges.ts';
import { CONCEPT_LABELS } from '../payments/payment-form.ts';

/**
 * Obligations extension: labels and display formats shared by the obligations
 * pages (pure, tested with node --test). Every value shown comes from the API;
 * nothing here computes a status, an applied or an outstanding amount.
 */

export { CONCEPT_LABELS };
export const CONCEPTS: readonly ChargeConcept[] = ['MONTHLY_FEE', 'LATE_FEE', 'PENALTY', 'OTHER'];

export const STATUS_LABELS: Record<ChargeStatus, string> = {
  PENDING: 'Pendiente',
  PARTIAL: 'Parcial',
  OVERDUE: 'Vencida',
  PAID: 'Pagada'
};
export const STATUSES: readonly ChargeStatus[] = ['PENDING', 'PARTIAL', 'OVERDUE', 'PAID'];

/** Status badge: always with its text, never color alone. */
export const STATUS_BADGES: Record<ChargeStatus, string> = {
  PENDING: 'bg-slate-100 text-slate-800 ring-slate-300',
  PARTIAL: 'bg-amber-50 text-amber-900 ring-amber-200',
  OVERDUE: 'bg-red-50 text-red-800 ring-red-200',
  PAID: 'bg-emerald-50 text-emerald-800 ring-emerald-200'
};

/**
 * Narrow screens (< 640 px): table rows stack and each cell shows its column label
 * (data-label) next to the value, so there is no horizontal scroll; from 640 px the
 * table scrolls inside its card, as in the other lists.
 */
export const STACKED_TABLE = 'w-full text-left text-sm max-sm:block sm:divide-y sm:divide-slate-200';
export const STACKED_ROW = 'max-sm:block max-sm:space-y-0.5 max-sm:py-3';
export const STACKED_CELL =
  'max-sm:flex max-sm:min-w-0 max-sm:justify-between max-sm:gap-3 max-sm:px-0 max-sm:py-0.5 max-sm:text-right max-sm:before:shrink-0 max-sm:before:text-left max-sm:before:font-medium max-sm:before:text-slate-500 max-sm:before:content-[attr(data-label)]';

export const CONNECTION_ERROR = 'No fue posible conectar con el servidor. Intenta de nuevo.';
export const CSRF_MESSAGE = 'No fue posible validar la solicitud. Recarga la página e intenta de nuevo.';

/** "2026-10": a month written as YYYY-MM (what <input type="month"> gives). */
export function isPeriod(text: string): boolean {
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(text);
}

const MONTH_YEAR = new Intl.DateTimeFormat('es-CO', { month: 'long', year: 'numeric', timeZone: 'UTC' });

/** "octubre de 2026" for "2026-10" (no time zone involved). */
export function formatPeriod(period: string): string {
  if (!isPeriod(period)) return period;
  const [year, month] = period.split('-').map(Number);
  return MONTH_YEAR.format(new Date(Date.UTC(year, month - 1, 1)));
}

export function capitalize(text: string): string {
  return text.charAt(0).toLocaleUpperCase('es-CO') + text.slice(1);
}

/** "Mensualidad", "Penalización: Ruido" — the concept and its description when it has one. */
export function conceptText(concept: ChargeConcept, description: string | null): string {
  return description ? `${CONCEPT_LABELS[concept]}: ${description}` : CONCEPT_LABELS[concept];
}

/**
 * 422 details by field. Query parameters come as "query.<field>" and the service
 * rules as "<field>": both forms are accepted. Unknown fields are ignored.
 */
export function fieldErrorsOf<F extends string>(error: ApiError, fields: readonly F[]): Partial<Record<F, string>> {
  const errors: Partial<Record<F, string>> = {};
  for (const detail of error.details) {
    const field = detail.field?.replace(/^(query|body)\./, '');
    if (field && (fields as readonly string[]).includes(field)) errors[field as F] ??= detail.message;
  }
  return errors;
}

/** The message for an error that is not a field error: never a stack trace or a technical text. */
export function errorMessage(error: unknown, forbidden: string): string {
  if (!(error instanceof ApiError)) return CONNECTION_ERROR;
  if (error.status === 403) return error.code === 'CSRF_FAILED' ? CSRF_MESSAGE : forbidden;
  if (error.status >= 500 || error.code === 'UNEXPECTED_RESPONSE') return CONNECTION_ERROR;
  return error.message;
}
