import { apiFetch } from './client';

/**
 * HU-017: the signed-in resident's notifications (read only).
 *
 * The resident is always the backend session's: nothing here sends a resident,
 * user or role (page and page_size only). The notifications are created by the
 * flows that notify (HU-004 payment registered, HU-010 demo payment approved,
 * HU-005 feedback responded or resolved). created_at and sent_at are UTC instants.
 */

export type NotificationEvent = 'PAYMENT_REGISTERED' | 'FEEDBACK_RESPONDED' | 'FEEDBACK_RESOLVED';
/** NOT_REQUIRED: the event has no email (an approved demo payment, HU-010). */
export type NotificationEmailStatus = 'PENDING' | 'SENT' | 'FAILED' | 'NO_CONTACT' | 'NOT_REQUIRED';

export interface ResidentNotification {
  id: number;
  event_type: NotificationEvent;
  entity_type: 'payment' | 'feedback';
  entity_id: number; // the resident's own payment or feedback
  title: string;
  message: string;
  email_status: NotificationEmailStatus;
  created_at: string; // UTC instant
  sent_at: string | null; // UTC instant, only when SENT
}

export interface ResidentNotificationPage {
  items: ResidentNotification[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

/** `query` is built by lib/resident/notifications-list.ts (page and page_size only). */
export function listMyNotifications(query: string): Promise<ResidentNotificationPage> {
  return apiFetch<ResidentNotificationPage>(`/resident/notifications${query ? `?${query}` : ''}`, { cache: 'no-store' });
}
