import type { PaymentConcept, PaymentStatus } from '../api/resident-payments.ts';
import { formatDateTime } from '../feedback/feedback-detail.ts';
import { capitalize, conceptText, formatPeriod } from '../obligations/charge-format.ts';
import { CONCEPT_LABELS, METHOD_LABELS, STATUS_LABELS, formatCop, formatDate } from '../payments/payment-form.ts';

/**
 * HU-008: labels and display formats of the resident's payments (pure, tested
 * with node --test). The existing formats are reused: COP amounts, calendar
 * dates (never shifted by a time zone), UTC instants shown in Bogotá time,
 * periods and the labels of HU-004. Nothing here computes a status, an applied
 * amount or a credit: they come from the API.
 */

export { CONCEPT_LABELS, METHOD_LABELS, STATUS_LABELS, formatCop, formatDate, formatDateTime };

export const STATUSES: readonly PaymentStatus[] = ['APPLIED', 'PARTIALLY_APPLIED', 'CREDIT'];

/** Status badge (same colors as the administrator's payments list): always with its text, never color alone. */
export const STATUS_BADGES: Record<PaymentStatus, string> = {
  APPLIED: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  PARTIALLY_APPLIED: 'bg-amber-50 text-amber-900 ring-amber-200',
  CREDIT: 'bg-sky-50 text-sky-800 ring-sky-200'
};

/** What each status means, in plain words (only what the status itself says). */
export const STATUS_HELP: Record<PaymentStatus, string> = {
  APPLIED: 'Todo el valor del pago se usó para cubrir obligaciones.',
  PARTIALLY_APPLIED: 'Una parte del pago cubrió obligaciones y el resto quedó como saldo a favor.',
  CREDIT: 'El pago aún no se ha usado para cubrir obligaciones: su valor quedó disponible para obligaciones posteriores.'
};

/** The status is not fixed: credit used later changes it. Shown so that "Saldo a favor" is not read as an error. */
export const STATUS_CHANGES_NOTE =
  'El estado puede cambiar con el tiempo: cuando el saldo a favor se usa para cubrir una nueva obligación, el pago pasa a «Aplicado parcialmente» o «Aplicado». Esto es normal y no indica un error.';

export function isStatus(value: string): value is PaymentStatus {
  return (STATUSES as readonly string[]).includes(value);
}

/** "Mensualidad", "Otro: Llaves" — the concept and the description of "Otro". */
export function paymentConceptText(concept: PaymentConcept, description: string | null): string {
  return conceptText(concept, concept === 'OTHER' ? description : null);
}

/** "Enero de 2026" for "2026-01" (formatPeriod of the obligations, no time zone involved); "—" without a period. */
export function periodText(period: string | null): string {
  return period ? capitalize(formatPeriod(period)) : '—';
}

export const NO_REFERENCE = 'No registrada';
export const NO_NOTES = 'Sin notas';

/** The transaction reference exactly as registered; never invented, never the receipt number. */
export function referenceText(reference: string | null): string {
  return reference && reference.trim() ? reference : NO_REFERENCE;
}

export function notesText(notes: string | null): string {
  return notes && notes.trim() ? notes : NO_NOTES;
}
