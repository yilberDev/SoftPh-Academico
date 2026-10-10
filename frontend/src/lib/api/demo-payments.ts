import { apiFetch } from './client';
import type { ChargeConcept, ChargeStatus } from './charges';

/**
 * HU-010: the signed-in resident's demo payments (internal demo gateway: no real money, no
 * external provider, no card or bank data).
 *
 * The resident is always the backend session's: nothing here sends a resident, a reference, a
 * status or a balance. Starting sends exactly the obligation and the amount, as a decimal string
 * ("150000.50"), never a JS number; completing sends only the simulated outcome. The routes exist
 * only while the backend has DEMO_PAYMENTS_ENABLED: otherwise every call answers 404 NOT_FOUND.
 * Dates are UTC instants; due dates are YYYY-MM-DD.
 */

export type DemoTransactionStatus = 'INITIATED' | 'APPROVED' | 'REJECTED' | 'FAILED' | 'CANCELLED' | 'EXPIRED';
export type DemoOutcome = 'APPROVED' | 'REJECTED' | 'FAILED';

/** An obligation of the resident's current apartment that can still be paid (outstanding > 0). */
export interface PayableObligation {
  charge_id: number;
  concept: ChargeConcept;
  description: string | null;
  billing_period: string | null; // "YYYY-MM"
  due_date: string; // YYYY-MM-DD
  amount: string;
  applied_amount: string;
  outstanding_amount: string;
  status: Exclude<ChargeStatus, 'PAID'>;
  /** The resident's own live transaction of this obligation, if any. */
  in_progress_transaction_id: number | null;
}

export interface PayableObligationList {
  items: PayableObligation[];
}

export interface DemoTransaction {
  id: number;
  reference: string; // DEMO-…, never a real provider's
  charge_id: number;
  amount: string;
  status: DemoTransactionStatus;
  created_at: string;
  expires_at: string;
  finished_at: string | null;
  payment_id: number | null; // APPROVED only
  receipt_number: string | null; // APPROVED only: the payment's number in the history, not a document
}

const BASE = '/resident/demo-payments';

export function listPayableObligations(): Promise<PayableObligationList> {
  return apiFetch<PayableObligationList>(`${BASE}/obligations`, { cache: 'no-store' });
}

/** One POST, never retried automatically. 201: the INITIATED transaction. */
export function startDemoPayment(chargeId: number, amount: string, csrfToken: string): Promise<DemoTransaction> {
  return apiFetch<DemoTransaction>(BASE, {
    method: 'POST',
    body: JSON.stringify({ charge_id: chargeId, amount }), // exactly these two fields; amount as text
    csrfToken
  });
}

export function getDemoPayment(id: number): Promise<DemoTransaction> {
  return apiFetch<DemoTransaction>(`${BASE}/${id}`, { cache: 'no-store' });
}

export function completeDemoPayment(id: number, outcome: DemoOutcome, csrfToken: string): Promise<DemoTransaction> {
  return apiFetch<DemoTransaction>(`${BASE}/${id}/complete`, {
    method: 'POST',
    body: JSON.stringify({ outcome }),
    csrfToken
  });
}

export function cancelDemoPayment(id: number, csrfToken: string): Promise<DemoTransaction> {
  return apiFetch<DemoTransaction>(`${BASE}/${id}/cancel`, { method: 'POST', csrfToken });
}
