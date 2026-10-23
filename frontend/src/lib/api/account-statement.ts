import { apiFetch } from './client';
import type { ChargeConcept, ChargeStatus } from './charges';
import type { PaymentMethod, PaymentStatus } from './payments';
import type { ResidentStatus } from './residents';

/**
 * HU-011 / HU-014: the account statement of a registered resident's current apartment.
 * Money is exact decimal text ("150000.00"); every amount, status and total comes from the API.
 */

export type AccountStatus = 'UP_TO_DATE' | 'PENDING' | 'OVERDUE';
export type PaymentSource = 'MANUAL' | 'DEMO_GATEWAY';

export interface StatementObligation {
  id: number;
  concept: ChargeConcept;
  description: string | null;
  billing_period: string | null;
  due_date: string;
  amount: string;
  applied_amount: string;
  outstanding_amount: string;
  status: ChargeStatus;
}

export interface StatementPayment {
  id: number;
  receipt_number: string;
  payment_date: string;
  registered_at: string;
  amount: string;
  applied_amount: string;
  credit_amount: string;
  status: PaymentStatus;
  method: PaymentMethod;
  source: PaymentSource;
  concept: ChargeConcept;
  concept_description: string | null;
  by_this_resident: boolean;
  /** The name recorded on the payment: administrator only (null for the resident). */
  payer_name: string | null;
  allocations: { charge_id: number; amount: string }[];
}

export interface AccountStatement {
  resident: { id: number; full_name: string; status: ResidentStatus; unit: { id: number; label: string } };
  as_of: string;
  generated_at: string;
  has_movements: boolean;
  summary: {
    status: AccountStatus;
    total_charged: string;
    total_applied: string;
    pending_balance: string;
    overdue_balance: string;
    credit_balance: string;
    obligations: number;
    pending_obligations: number;
    overdue_obligations: number;
    payments: number;
  };
  obligations: StatementObligation[];
  payments: StatementPayment[];
}

/** HU-011: the administrator reads a registered resident's statement. */
export function getResidentAccountStatement(residentId: number): Promise<AccountStatement> {
  return apiFetch<AccountStatement>(`/admin/residents/${residentId}/account-statement`, { cache: 'no-store' });
}

/** HU-014: the signed-in resident reads their own statement (the server takes the resident from the session). */
export function getMyAccountStatement(): Promise<AccountStatement> {
  return apiFetch<AccountStatement>('/resident/account-statement', { cache: 'no-store' });
}
