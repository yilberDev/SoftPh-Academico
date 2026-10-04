import type { FeedbackDetail } from './feedback';
import { serverApiGet } from './server';

/** Server-side only (SSR): the detail of one feedback (HU-005). */
export function loadFeedback(id: string, request: Request): Promise<FeedbackDetail> {
  return serverApiGet<FeedbackDetail>(`/admin/feedback/${id}`, request);
}
