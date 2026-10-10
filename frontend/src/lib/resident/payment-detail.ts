import { ApiError } from '../api/errors.ts';
import type { PaymentMethod, ResidentPaymentDetail } from '../api/resident-payments.ts';
import { conceptText } from '../obligations/charge-format.ts';
import {
  METHOD_LABELS,
  formatCop,
  formatDate,
  formatDateTime,
  notesText,
  paymentConceptText,
  periodText,
  referenceText
} from './payment-format.ts';

/**
 * HU-008: detail of one payment of the signed-in resident (pure, tested with node --test).
 *
 * Everything shown comes from GET /resident/payments/{id}: the status, the
 * applied amount and the credit are the backend's and are never recalculated.
 * The payment date is a calendar date (shown as it is); registered_at and
 * allocated_at are UTC instants, shown in Bogotá time.
 */

export const NOT_FOUND_MESSAGE = 'Pago no encontrado.';
export const NOT_FOUND_HINT = 'Es posible que el enlace sea incorrecto o que el pago no esté registrado en tu cuenta.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para acceder a esta sección.';
export const LOAD_ERROR = 'No fue posible cargar el pago. Intenta nuevamente.';
export const NO_ALLOCATIONS = 'Este pago no se ha aplicado a ninguna obligación.';
export const NOTES_SOURCE = 'Registrada por la administración.';

// Database ids are positive BIGINTs; anything else never reaches the API path.
const ID_PATTERN = /^[1-9]\d{0,18}$/;

/**
 * HU-010: the PDF receipt exists only for a demo payment (DEMO_GATEWAY), and its URL uses the
 * persisted payment's id (never the demo transaction's). The backend decides again: another
 * resident's payment or a manual one is "not found" there.
 */
export const DEMO_RECEIPT_LINK = 'Descargar comprobante de pago (demostración)';
export const DEMO_RECEIPT_HINT =
  'PDF. Comprobante de una transacción de demostración académica: no corresponde a un cobro real.';

export function demoReceiptPath(paymentId: number): string | null {
  return Number.isSafeInteger(paymentId) && paymentId > 0 ? `/api/v1/resident/payments/${paymentId}/receipt` : null;
}

export function demoReceiptOf(payment: { id: number; method: PaymentMethod }): string | null {
  return payment.method === 'DEMO_GATEWAY' ? demoReceiptPath(payment.id) : null;
}

export function isPaymentId(value: string | undefined): value is string {
  return value !== undefined && ID_PATTERN.test(value);
}

/** What the detail page shows. A payment of another resident is "not found", exactly like a missing one. */
export type DetailPage =
  | { kind: 'payment'; payment: ResidentPaymentDetail }
  | { kind: 'not-found' }
  /** The session ended between the page check and the API call: back to the login. */
  | { kind: 'unauthenticated' }
  | { kind: 'forbidden' }
  | { kind: 'error' };

export async function loadDetailPage(id: string | undefined, load: (id: string) => Promise<ResidentPaymentDetail>): Promise<DetailPage> {
  if (!isPaymentId(id)) return { kind: 'not-found' };
  try {
    return { kind: 'payment', payment: await load(id) };
  } catch (error) {
    if (!(error instanceof ApiError)) return { kind: 'error' };
    if (error.status === 404 || error.status === 422) return { kind: 'not-found' };
    if (error.status === 401) return { kind: 'unauthenticated' };
    if (error.status === 403) return { kind: 'forbidden' };
    return { kind: 'error' };
  }
}

export interface Line {
  label: string;
  value: string;
  mono?: boolean;
}

export function mainLines(payment: ResidentPaymentDetail): Line[] {
  return [
    { label: 'Fecha del pago', value: formatDate(payment.payment_date) },
    { label: 'Concepto', value: paymentConceptText(payment.concept, payment.concept_description) },
    { label: 'Monto', value: formatCop(payment.amount) },
    { label: 'Método', value: METHOD_LABELS[payment.method] },
    { label: 'Unidad', value: payment.unit.label, mono: true },
    { label: 'Registrado el', value: formatDateTime(payment.registered_at) },
    { label: 'Número de recibo', value: payment.receipt_number, mono: true },
    { label: 'Referencia de transacción', value: referenceText(payment.transaction_reference), mono: Boolean(payment.transaction_reference) }
  ];
}

/** Paid, applied and credit exactly as the API sent them. */
export function financialLines(payment: ResidentPaymentDetail): Line[] {
  return [
    { label: 'Monto pagado', value: formatCop(payment.amount) },
    { label: 'Monto aplicado', value: formatCop(payment.applied_amount) },
    { label: 'Saldo a favor', value: formatCop(payment.credit_amount) }
  ];
}

export function notesOf(payment: ResidentPaymentDetail): { text: string; empty: boolean } {
  const text = notesText(payment.notes);
  return { text, empty: text !== payment.notes };
}

export interface AllocationRow {
  concept: string;
  period: string;
  dueDate: string;
  chargeAmount: string;
  allocatedAmount: string;
}

/** The obligations the payment covered, in the order they were applied. */
export function allocationRows(payment: ResidentPaymentDetail): AllocationRow[] {
  return payment.allocations.map((allocation) => ({
    // An obligation's description (penalty, other) goes with its concept: "Penalización: Ruido".
    concept: conceptText(allocation.concept, allocation.description),
    period: periodText(allocation.billing_period),
    dueDate: formatDate(allocation.due_date),
    chargeAmount: formatCop(allocation.charge_amount),
    allocatedAmount: formatCop(allocation.allocated_amount)
  }));
}
