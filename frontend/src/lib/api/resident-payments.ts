import { apiFetch } from './client';
import type { PaymentConcept, PaymentMethod, PaymentStatus } from './payments';

/**
 * HU-008: the signed-in resident's own payments (read only).
 *
 * The resident is always the backend session's: nothing here sends a resident,
 * user or apartment id. Amounts travel as decimal strings ("150000.00"),
 * billing periods as "YYYY-MM", payment dates as "YYYY-MM-DD" and instants
 * (registered_at, allocated_at) in UTC. The status, the applied amount and the
 * credit are computed by the backend; they are only read.
 */

export type { PaymentConcept, PaymentMethod, PaymentStatus };

export interface ResidentPayment {
  id: number;
  receipt_number: string; // RC-00000012, never the transaction reference
  payment_date: string; // YYYY-MM-DD
  concept: PaymentConcept;
  concept_description: string | null; // "Otro" only
  amount: string;
  method: PaymentMethod;
  status: PaymentStatus;
  transaction_reference: string | null;
}

export interface ResidentPaymentPage {
  items: ResidentPayment[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ResidentPaymentAllocation {
  charge_id: number;
  concept: PaymentConcept;
  description: string | null;
  billing_period: string | null; // YYYY-MM, monthly fees only
  due_date: string; // YYYY-MM-DD
  charge_amount: string; // the obligation's original amount
  allocated_amount: string; // what this payment covered of it
  allocated_at: string; // UTC instant
}

export interface ResidentPaymentDetail extends ResidentPayment {
  registered_at: string; // UTC instant
  notes: string | null; // written by the administration, visible to the resident
  unit: { id: number; label: string }; // as on the receipt
  applied_amount: string;
  credit_amount: string;
  allocations: ResidentPaymentAllocation[];
}

/** `query` is built by lib/resident/payments-list.ts (whitelisted values only, never an id of a person). */
export function listMyPayments(query: string): Promise<ResidentPaymentPage> {
  return apiFetch<ResidentPaymentPage>(`/resident/payments${query ? `?${query}` : ''}`, { cache: 'no-store' });
}
