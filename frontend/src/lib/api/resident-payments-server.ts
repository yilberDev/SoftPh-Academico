import type { ResidentPaymentDetail } from './resident-payments';
import { serverApiGet } from './server';

/** Server-side only (SSR): one payment of the signed-in resident (the session's cookie decides who). */
export function loadMyPayment(id: string, request: Request): Promise<ResidentPaymentDetail> {
  return serverApiGet<ResidentPaymentDetail>(`/resident/payments/${id}`, request);
}
