import type { FeedbackCategory, FeedbackStatus, FeedbackUrgency } from '../api/feedback.ts';
import { formatDateTime } from './feedback-detail.ts';
import { NO_SUBJECT, STATUS_LABELS } from './feedback-list.ts';

/**
 * HU-009: labels of a feedback's category, urgency and status, shared by the
 * resident's pages and the administrator's (HU-005). Pure, tested with node
 * --test. The API sends the codes; only the presentation is in Spanish. A
 * feedback sent before HU-009 has no category and no urgency (null): it says
 * so, nothing is invented.
 */

export { NO_SUBJECT, STATUS_LABELS, formatDateTime };

export const CATEGORIES: readonly FeedbackCategory[] = ['COMPLAINT', 'SUGGESTION', 'REQUEST', 'OTHER'];
export const URGENCIES: readonly FeedbackUrgency[] = ['HIGH', 'MEDIUM', 'LOW'];
export const STATUSES: readonly FeedbackStatus[] = ['PENDING', 'RESOLVED'];

export const CATEGORY_LABELS: Record<FeedbackCategory, string> = {
  COMPLAINT: 'Queja',
  SUGGESTION: 'Sugerencia',
  REQUEST: 'Solicitud',
  OTHER: 'Otros'
};

export const URGENCY_LABELS: Record<FeedbackUrgency, string> = { HIGH: 'Alta', MEDIUM: 'Media', LOW: 'Baja' };

/** Category or urgency of a feedback sent before HU-009. */
export const NOT_RECORDED = 'No registrada';

/** Status badge (same colors as the administrator's list): always with its text, never color alone. */
export const STATUS_BADGES: Record<FeedbackStatus, string> = {
  PENDING: 'bg-amber-50 text-amber-900 ring-amber-200',
  RESOLVED: 'bg-emerald-50 text-emerald-800 ring-emerald-200'
};

/** What each status means for the resident: only what the status itself says (no promised answer or time). */
export const STATUS_HELP: Record<FeedbackStatus, string> = {
  PENDING: 'Tu feedback está registrado y pendiente de gestión.',
  RESOLVED: 'Tu feedback ha sido marcado como resuelto.'
};

export function categoryText(category: FeedbackCategory | null): string {
  return category ? CATEGORY_LABELS[category] : NOT_RECORDED;
}

export function urgencyText(urgency: FeedbackUrgency | null): string {
  return urgency ? URGENCY_LABELS[urgency] : NOT_RECORDED;
}

/** Category and urgency as shown (list rows and details, resident's and administrator's). */
export function classificationOf(feedback: { category: FeedbackCategory | null; urgency: FeedbackUrgency | null }): {
  category: string;
  urgency: string;
  /** Sent before HU-009: shown in a muted style. */
  categoryRecorded: boolean;
  urgencyRecorded: boolean;
} {
  return {
    category: categoryText(feedback.category),
    urgency: urgencyText(feedback.urgency),
    categoryRecorded: feedback.category !== null,
    urgencyRecorded: feedback.urgency !== null
  };
}

export function subjectText(subject: string | null): string {
  return subject && subject.trim() ? subject : NO_SUBJECT;
}
