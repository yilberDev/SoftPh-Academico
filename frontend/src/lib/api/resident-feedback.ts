import { apiFetch } from './client';
import type { FeedbackCategory, FeedbackStatus, FeedbackUrgency } from './feedback';

/**
 * HU-009: the signed-in resident sends feedback and reads their own.
 *
 * The resident is always the backend session's: nothing here sends a resident,
 * user or role. The body of a new feedback has exactly four fields; its status
 * (PENDING) is decided by the backend. The answers never carry the
 * administrator's response, the internal note or the resolution date.
 * created_at is a UTC instant.
 */

export type { FeedbackCategory, FeedbackStatus, FeedbackUrgency };

export interface ResidentFeedback {
  id: number;
  subject: string | null;
  category: FeedbackCategory | null; // null: sent before HU-009
  urgency: FeedbackUrgency | null; // null: sent before HU-009
  status: FeedbackStatus;
  created_at: string; // UTC instant
}

export interface ResidentFeedbackPage {
  items: ResidentFeedback[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ResidentFeedbackDetail extends ResidentFeedback {
  message: string;
  /** The administrator's response (HU-005 3.2); null until it exists. The internal note is never sent. */
  admin_response: string | null;
  /** Resolution date, UTC instant; null until it is resolved. */
  resolved_at: string | null;
}

/** The only fields the API accepts (anything else is rejected with 422). */
export interface NewFeedbackPayload {
  subject: string | null;
  message: string;
  category: FeedbackCategory;
  urgency: FeedbackUrgency;
}

/** `query` is built by lib/resident/feedback-list.ts (page and page_size only). */
export function listMyFeedback(query: string): Promise<ResidentFeedbackPage> {
  return apiFetch<ResidentFeedbackPage>(`/resident/feedback${query ? `?${query}` : ''}`, { cache: 'no-store' });
}

/** One POST, never retried automatically. 201: the stored feedback (PENDING). */
export function sendFeedback(payload: NewFeedbackPayload, csrfToken: string): Promise<ResidentFeedbackDetail> {
  const { subject, message, category, urgency } = payload; // exactly these four fields
  return apiFetch<ResidentFeedbackDetail>('/resident/feedback', {
    method: 'POST',
    body: JSON.stringify({ subject, message, category, urgency }),
    csrfToken
  });
}
