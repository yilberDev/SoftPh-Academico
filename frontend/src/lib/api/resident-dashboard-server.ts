import type { ResidentSummarySources, StatementSource } from '../resident/dashboard';
import { ACTIVITY_SIZE, FEEDBACK_COUNT_PAGE_SIZE } from '../resident/dashboard';
import type { AccountStatement } from './account-statement';
import { ApiError } from './errors';
import type { ResidentNotification, ResidentNotificationPage } from './notifications';
import type { ResidentFeedbackPage } from './resident-feedback';
import { serverApiGet } from './server';

export interface ResidentDashboardSources extends ResidentSummarySources {
  /** The latest notifications; null where the request failed. */
  notifications: ResidentNotification[] | null;
}

const valueOf = <T>(result: PromiseSettledResult<T>): T | null => (result.status === 'fulfilled' ? result.value : null);

function statementOf(result: PromiseSettledResult<AccountStatement>): StatementSource {
  if (result.status === 'rejected') {
    const error = result.reason;
    return error instanceof ApiError && error.code === 'ACCOUNT_STATEMENT_UNAVAILABLE' ? { kind: 'no-unit' } : { kind: 'error' };
  }
  const { resident, summary } = result.value;
  return {
    kind: 'ok',
    unit: resident.unit.label,
    status: summary.status,
    pending_balance: summary.pending_balance,
    pending_obligations: summary.pending_obligations
  };
}

/**
 * Server-side only (SSR): what the resident's home page shows, read from the resident's own endpoints with
 * the user's cookie (the session decides who; nothing here selects a resident). Each request may fail on
 * its own; the page then shows that part as unavailable. Only the summary of the statement and the status
 * of each feedback are kept (no obligation, payment or message reaches the page).
 */
export async function loadResidentDashboard(request: Request): Promise<ResidentDashboardSources> {
  const [statement, feedback, notifications] = await Promise.allSettled([
    serverApiGet<AccountStatement>('/resident/account-statement', request),
    serverApiGet<ResidentFeedbackPage>(`/resident/feedback?page_size=${FEEDBACK_COUNT_PAGE_SIZE}`, request),
    serverApiGet<ResidentNotificationPage>(`/resident/notifications?page_size=${ACTIVITY_SIZE}`, request)
  ]);
  const feedbackPage = valueOf(feedback);
  return {
    statement: statementOf(statement),
    feedback: feedbackPage ? { items: feedbackPage.items.map(({ status }) => ({ status })), total: feedbackPage.total } : null,
    notifications: valueOf(notifications)?.items ?? null
  };
}
