import { apiFetch } from './client';
import type { ChargeConcept, ChargeStatus } from './charges';

/**
 * HU-010: the signed-in resident's demo payments (internal demo gateway: no real money, no
 * external provider, no card or bank data).
 *
 * The resident is always the backend session's: nothing here sends a resident, a reference, a
 * status or a balance. Starting sends exactly the obligation and the amount, as a decimal string
 * ("150000.50"), never a JS number; completing sends only the id of a fictitious test instrument of
 * the backend's catalog, and the backend decides the result. The routes exist only while the backend
 * has DEMO_PAYMENTS_ENABLED: otherwise every call answers 404 NOT_FOUND. Dates are UTC instants; due
 * dates are YYYY-MM-DD.
 */

export type DemoTransactionStatus = 'INITIATED' | 'APPROVED' | 'REJECTED' | 'FAILED' | 'CANCELLED' | 'EXPIRED';
export type DemoInstrumentKind = 'CARD' | 'ACCOUNT';
/** Why a transaction ended as it did: simulated by the instrument, or NOT_APPLIED (the backend could not apply it). */
export type DemoResultReason = 'INSUFFICIENT_FUNDS' | 'TECHNICAL_ERROR' | 'NOT_APPLIED';

/** A fictitious test instrument of the demo gateway: never a real card or account, never payment data. */
export interface DemoInstrument {
  id: string; // stable: TEST_CARD_APPROVE, ...
  label: string;
  kind: DemoInstrumentKind;
  ending: string | null; // fictitious last digits of a test card, never a card number
  /** What the demo gateway simulates for it; the transaction's status (the backend's) is the result. */
  expected_result: 'APPROVED' | 'REJECTED' | 'FAILED';
  description: string;
}

export interface DemoInstrumentList {
  items: DemoInstrument[];
}

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
  test_instrument: string | null; // the instrument that resolved it; null if none did (cancelled, expired, older ones)
  result_reason: DemoResultReason | null;
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

/** The backend's catalog of test instruments, in the order to show them. */
export function listDemoInstruments(): Promise<DemoInstrumentList> {
  return apiFetch<DemoInstrumentList>(`${BASE}/instruments`, { cache: 'no-store' });
}

export function getDemoPayment(id: number): Promise<DemoTransaction> {
  return apiFetch<DemoTransaction>(`${BASE}/${id}`, { cache: 'no-store' });
}

/** One POST, never retried automatically: exactly the instrument's id, never a status or a result. */
export function completeDemoPayment(id: number, instrumentId: string, csrfToken: string): Promise<DemoTransaction> {
  return apiFetch<DemoTransaction>(`${BASE}/${id}/complete`, {
    method: 'POST',
    body: JSON.stringify({ instrument_id: instrumentId }),
    csrfToken
  });
}

export function cancelDemoPayment(id: number, csrfToken: string): Promise<DemoTransaction> {
  return apiFetch<DemoTransaction>(`${BASE}/${id}/cancel`, { method: 'POST', csrfToken });
}
