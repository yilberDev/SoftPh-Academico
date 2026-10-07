import type { Charge, ChargeDetail } from '../api/charges.ts';
import { formatDateTime } from '../feedback/feedback-detail.ts';
import { METHOD_LABELS, formatCop, formatDate } from '../payments/payment-form.ts';
import { STATUS_LABELS, capitalize, conceptText, formatPeriod } from './charge-format.ts';
import { detailPath } from './charge-form.ts';

/**
 * Obligations extension: detail of one obligation (pure, tested with node --test).
 *
 * Everything shown comes from GET /admin/charges/{id}: the status, the applied
 * and outstanding amounts are the backend's. Instants (created_at,
 * allocation_date) are UTC and shown in Bogotá time; calendar dates (due date,
 * payment date) are shown as they are.
 */

export const NOT_FOUND_MESSAGE = 'Esta obligación no existe.';
export const NO_ALLOCATIONS = 'Aún no se ha aplicado ningún pago a esta obligación.';

export function titleOf(charge: Charge): string {
  const period = charge.billing_period ? ` de ${formatPeriod(charge.billing_period)}` : '';
  return `${conceptText(charge.concept, charge.description)}${period}`;
}

/** The author is identified by the API with its user id only (no name is exposed). */
export function authorText(detail: ChargeDetail): string {
  return `Usuario n.º ${detail.created_by_user_id}`;
}

export function mainLines(detail: ChargeDetail): { label: string; value: string; mono?: boolean }[] {
  return [
    { label: 'Unidad', value: detail.unit_deleted ? `${detail.unit_label} (apartamento eliminado)` : detail.unit_label, mono: true },
    { label: 'Estado', value: STATUS_LABELS[detail.status] },
    { label: 'Concepto', value: conceptText(detail.concept, null) },
    { label: 'Descripción', value: detail.description ?? '—' },
    { label: 'Periodo', value: detail.billing_period ? capitalize(formatPeriod(detail.billing_period)) : '—' },
    { label: 'Vencimiento', value: formatDate(detail.due_date) },
    { label: 'Valor', value: formatCop(detail.amount) },
    { label: 'Aplicado', value: formatCop(detail.applied) },
    { label: 'Saldo', value: formatCop(detail.outstanding) },
    { label: 'Creada por', value: authorText(detail) },
    { label: 'Fecha de creación', value: formatDateTime(detail.created_at) }
  ];
}

export interface RelatedLink {
  href: string;
  text: string;
  status: string;
}

export function linkTo(charge: Charge): RelatedLink {
  return {
    href: detailPath(charge.id),
    text: `n.º ${charge.id} · ${titleOf(charge)} · vence el ${formatDate(charge.due_date)} · ${formatCop(charge.amount)}`,
    status: STATUS_LABELS[charge.status]
  };
}

/** The origin of a late fee and the late fees derived from this obligation. */
export function relationsOf(detail: ChargeDetail): { source: RelatedLink | null; lateFees: RelatedLink[] } {
  return { source: detail.source ? linkTo(detail.source) : null, lateFees: detail.late_fees.map(linkTo) };
}

export interface AllocationRow {
  receipt: string;
  paymentDate: string;
  method: string;
  amount: string;
  appliedAt: string;
}

export function allocationRows(detail: ChargeDetail): AllocationRow[] {
  return detail.allocations.map((allocation) => ({
    receipt: allocation.receipt_number,
    paymentDate: formatDate(allocation.payment_date),
    method: METHOD_LABELS[allocation.payment_method],
    amount: formatCop(allocation.applied_amount),
    appliedAt: formatDateTime(allocation.allocation_date)
  }));
}
