import { apiFetch } from './client';
import type { PaymentConcept, PaymentMethod } from './payments';

/**
 * Obligations extension: obligations (charges) of the apartments, administrator only.
 *
 * Amounts travel as decimal strings ("180000.00") and periods as "YYYY-MM". The
 * status, the applied and outstanding amounts, the credit applied, the author and
 * the preview fingerprint are always computed by the backend: they are only read,
 * never sent.
 */

export type ChargeConcept = PaymentConcept;
export type ChargeStatus = 'PAID' | 'OVERDUE' | 'PARTIAL' | 'PENDING';
export type ChargeSortField = 'due_date' | 'unit' | 'concept' | 'amount' | 'outstanding' | 'status' | 'created';

export interface Charge {
  id: number;
  unit_id: number;
  unit_label: string;
  unit_deleted: boolean;
  concept: ChargeConcept;
  description: string | null;
  billing_period: string | null; // YYYY-MM, monthly fees only
  due_date: string; // YYYY-MM-DD
  amount: string;
  applied: string;
  outstanding: string;
  status: ChargeStatus;
  source_charge_id: number | null;
  created_at: string; // UTC instant
}

export interface ChargePage {
  items: Charge[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ChargeAllocation {
  payment_id: number;
  receipt_number: string;
  payment_date: string;
  payment_method: PaymentMethod;
  applied_amount: string;
  allocation_date: string; // UTC instant
}

export interface ChargeDetail extends Charge {
  created_by_user_id: number;
  source: Charge | null;
  late_fees: Charge[];
  allocations: ChargeAllocation[];
}

export interface CreditApplied {
  payment_id: number;
  receipt_number: string;
  charge_id: number; // FIFO may also cover older pending charges of the apartment
  amount: string;
}

export interface ChargeCreated {
  id: number;
  unit_id: number;
  unit_label: string;
  concept: ChargeConcept;
  description: string | null;
  billing_period: string | null;
  due_date: string;
  amount: string;
  applied: string;
  outstanding: string;
  status: ChargeStatus;
  source_charge_id: number | null;
  created_by_user_id: number;
  created_at: string;
  credit_applied: CreditApplied[];
  account: { pending_balance: string; credit_balance: string };
}

export interface MonthlyPreview {
  billing_period: string;
  amount: string;
  due_date: string;
  active_units: number;
  existing: number;
  missing: number;
  to_create: number;
  total_amount: string;
  existing_with_other_amount: number;
  preview_fingerprint: string;
}

export interface UnitIssue {
  unit_id: number;
  label: string;
  code: string;
  message: string;
}

export interface MonthlyGenerationResult {
  billing_period: string;
  amount: string;
  due_date: string;
  units_evaluated: number;
  created: number;
  existing: number;
  omitted: number;
  failed: number;
  generated_total: string;
  credit_applied_total: string;
  credit_applied_to_new_charges: string;
  units_with_credit_applied: number;
  outstanding_total: string;
  omitted_units: UnitIssue[];
  errors: UnitIssue[];
  audit_recorded: boolean;
}

/** What the administrator chooses for one obligation. Fields that do not apply are left out. */
export interface ChargePayload {
  unit_id: number;
  concept: ChargeConcept;
  amount: string;
  due_date: string;
  description?: string;
  billing_period?: string;
  source_charge_id?: number;
}

export interface MonthlyParams {
  billing_period: string;
  amount: string;
  due_date: string;
}

/** Confirms exactly what the preview showed: its count and its fingerprint, as the backend sent them. */
export interface MonthlyGenerationPayload extends MonthlyParams {
  expected_to_create: number;
  preview_fingerprint: string;
}

/** `query` is built by lib/obligations/monthly-form.ts (period, amount and due date only). */
export function previewMonthlyCharges(query: string): Promise<MonthlyPreview> {
  return apiFetch<MonthlyPreview>(`/admin/charges/monthly/preview?${query}`, { cache: 'no-store' });
}

export function generateMonthlyCharges(payload: MonthlyGenerationPayload, csrfToken: string): Promise<MonthlyGenerationResult> {
  return apiFetch<MonthlyGenerationResult>('/admin/charges/monthly', {
    method: 'POST',
    body: JSON.stringify(payload),
    csrfToken
  });
}

export function createCharge(payload: ChargePayload, csrfToken: string): Promise<ChargeCreated> {
  return apiFetch<ChargeCreated>('/admin/charges', { method: 'POST', body: JSON.stringify(payload), csrfToken });
}

/** `query` is built by lib/obligations/obligations-list.ts (whitelisted values only). */
export function listCharges(query: string): Promise<ChargePage> {
  return apiFetch<ChargePage>(`/admin/charges${query ? `?${query}` : ''}`, { cache: 'no-store' });
}

export function getCharge(id: number): Promise<ChargeDetail> {
  return apiFetch<ChargeDetail>(`/admin/charges/${id}`, { cache: 'no-store' });
}
