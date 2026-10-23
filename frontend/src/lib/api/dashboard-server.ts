import type { AdminSummarySources } from '../admin/dashboard';
import type { PendingBalancesReport } from './reports';
import { serverApiGet } from './server';

interface Total {
  total: number;
}

const valueOf = <T>(result: PromiseSettledResult<T>): T | null => (result.status === 'fulfilled' ? result.value : null);

/**
 * Server-side only (SSR): the numbers of the administrator's home page, read from existing endpoints
 * with the user's cookie. Each request may fail on its own; the page then shows that number as
 * unavailable and everything else as usual. Only the totals of the reports are kept (no list reaches the page).
 */
export async function loadAdminSummary(request: Request): Promise<AdminSummarySources> {
  const [residents, feedback, balances] = await Promise.allSettled([
    serverApiGet<Total>('/admin/residents?page_size=1', request),
    serverApiGet<Total>('/admin/feedback?status=PENDING&page_size=1', request),
    serverApiGet<PendingBalancesReport>('/admin/reports/pending-balances?scope=ALL', request)
  ]);
  const report = valueOf(balances);
  return {
    residents: valueOf(residents)?.total ?? null,
    pendingFeedback: valueOf(feedback)?.total ?? null,
    balances: report
      ? {
          units: report.units,
          overdue_units: report.overdue_units,
          total_pending: report.total_pending,
          total_overdue: report.total_overdue
        }
      : null
  };
}
