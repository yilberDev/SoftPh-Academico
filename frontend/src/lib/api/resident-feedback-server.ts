import type { ResidentFeedbackDetail } from './resident-feedback';
import { serverApiGet } from './server';

/** Server-side only (SSR): one feedback of the signed-in resident (the session's cookie decides who). */
export function loadMyFeedback(id: string, request: Request): Promise<ResidentFeedbackDetail> {
  return serverApiGet<ResidentFeedbackDetail>(`/resident/feedback/${id}`, request);
}
