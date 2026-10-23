import { apiFetch } from './client';
import type { AccountStatus } from './account-statement';
import type { Payment, PaymentMethod } from './payments';

/** HU-012: financial reports (administrator only). Money is exact decimal text ("150000.00"). */

export interface PaymentsReport {
  date_from: string;
  date_to: string;
  generated_at: string;
  has_data: boolean;
  items: Payment[];
  payments: number;
  total_amount: string;
  total_applied: string;
  total_credit: string;
  by_method: { method: PaymentMethod; payments: number; amount: string }[];
}

export type PendingScope = 'ALL' | 'OVERDUE';

export interface PendingBalance {
  unit: { id: number; label: string; deleted: boolean };
  /** The current registered resident (opens the HU-011 statement); null when the apartment has none. */
  resident: { id: number; full_name: string } | null;
  status: AccountStatus;
  pending_balance: string;
  overdue_balance: string;
  credit_balance: string;
  pending_obligations: number;
  overdue_obligations: number;
  oldest_due_date: string;
}

export interface PendingBalancesReport {
  as_of: string;
  generated_at: string;
  scope: PendingScope;
  has_data: boolean;
  items: PendingBalance[];
  units: number;
  overdue_units: number;
  total_pending: string;
  total_overdue: string;
}

/** `query` is built by lib/reports/reports.ts (validated dates only). */
export function getPaymentsReport(query: string): Promise<PaymentsReport> {
  return apiFetch<PaymentsReport>(`/admin/reports/payments?${query}`, { cache: 'no-store' });
}

export function getPendingBalancesReport(scope: PendingScope): Promise<PendingBalancesReport> {
  return apiFetch<PendingBalancesReport>(`/admin/reports/pending-balances?scope=${scope}`, { cache: 'no-store' });
}
