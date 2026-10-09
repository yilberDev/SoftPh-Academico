import { apiFetch } from './client';

/** HU-005: feedback of residents, managed by the administrator. */

export type FeedbackStatus = 'PENDING' | 'RESOLVED';
/** HU-009: chosen by the resident; null for the feedback sent before HU-009 (never invented). */
export type FeedbackCategory = 'COMPLAINT' | 'SUGGESTION' | 'REQUEST' | 'OTHER';
export type FeedbackUrgency = 'HIGH' | 'MEDIUM' | 'LOW';
export type FeedbackSortField = 'created_at' | 'resident' | 'subject' | 'status';
export type NotificationStatus = 'SENT' | 'FAILED' | 'UNCONFIRMED';

export interface FeedbackListItem {
  id: number;
  resident: { id: number; full_name: string };
  subject: string | null;
  category: FeedbackCategory | null;
  urgency: FeedbackUrgency | null;
  status: FeedbackStatus;
  created_at: string;
  resolved_at: string | null;
}

export interface FeedbackPage {
  items: FeedbackListItem[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface FeedbackAction {
  action: string;
  actor: { id: number | null; full_name: string | null };
  at: string;
}

export interface FeedbackDetail {
  id: number;
  resident: { id: number; full_name: string; email: string; unit_label: string | null };
  subject: string | null;
  message: string;
  category: FeedbackCategory | null;
  urgency: FeedbackUrgency | null;
  status: FeedbackStatus;
  admin_response: string | null;
  resolution_note: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  version: number;
  action_history: FeedbackAction[];
}

export interface FeedbackActionResult {
  feedback: FeedbackDetail;
  notification_status: NotificationStatus;
}

/** `query` is built by lib/feedback/feedback-list.ts (whitelisted values only). */
export function listFeedback(query: string): Promise<FeedbackPage> {
  return apiFetch<FeedbackPage>(`/admin/feedback${query ? `?${query}` : ''}`, { cache: 'no-store' });
}

export function getFeedback(id: number): Promise<FeedbackDetail> {
  return apiFetch<FeedbackDetail>(`/admin/feedback/${id}`, { cache: 'no-store' });
}

export function respondFeedback(id: number, response: string, version: number, csrfToken: string): Promise<FeedbackActionResult> {
  return apiFetch<FeedbackActionResult>(`/admin/feedback/${id}/response`, {
    method: 'POST',
    body: JSON.stringify({ response, version }),
    csrfToken
  });
}

export function resolveFeedback(id: number, resolutionNote: string, version: number, csrfToken: string): Promise<FeedbackActionResult> {
  return apiFetch<FeedbackActionResult>(`/admin/feedback/${id}/resolve`, {
    method: 'POST',
    body: JSON.stringify({ resolution_note: resolutionNote, version }),
    csrfToken
  });
}
