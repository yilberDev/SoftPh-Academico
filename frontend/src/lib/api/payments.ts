import { apiFetch } from './client';

/** HU-004: manual payments (administrator only). Amounts travel as decimal strings ("150000.00"). */

export type PaymentMethod = 'CASH' | 'CARD' | 'TRANSFER';
export type PaymentConcept = 'MONTHLY_FEE' | 'LATE_FEE' | 'PENALTY' | 'OTHER';
export type PaymentStatus = 'APPLIED' | 'PARTIALLY_APPLIED' | 'CREDIT';
export type ReceiptEmailStatus = 'PENDING' | 'SENT' | 'FAILED';
export type PaymentSortField = 'date' | 'resident' | 'amount' | 'concept' | 'method' | 'status';

/** What the administrator chooses. The apartment and everything else are decided by the backend. */
export interface PaymentPayload {
  resident_id: number;
  amount: string;
  method: PaymentMethod;
  concept: PaymentConcept;
  concept_description: string | null;
  payment_date: string; // YYYY-MM-DD
}

export interface Payment {
  id: number;
  receipt_number: string;
  resident: { id: number; full_name: string };
  unit: { id: number; label: string };
  amount: string;
  method: PaymentMethod;
  concept: PaymentConcept;
  concept_description: string | null;
  payment_date: string;
  registered_at: string;
  applied_amount: string;
  credit_amount: string;
  status: PaymentStatus;
  receipt_email_status: ReceiptEmailStatus;
}

export interface RegisteredPayment extends Payment {
  allocations: { charge_id: number; concept: PaymentConcept; description: string | null; due_date: string; amount: string }[];
  account: { pending_balance: string; credit_balance: string };
}

export interface PaymentPage {
  items: Payment[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export function registerPayment(payload: PaymentPayload, csrfToken: string): Promise<RegisteredPayment> {
  return apiFetch<RegisteredPayment>('/admin/payments', { method: 'POST', body: JSON.stringify(payload), csrfToken });
}

/** `query` is built by lib/payments/payments-list.ts (whitelisted values only). */
export function listPayments(query: string): Promise<PaymentPage> {
  return apiFetch<PaymentPage>(`/admin/payments${query ? `?${query}` : ''}`, { cache: 'no-store' });
}
