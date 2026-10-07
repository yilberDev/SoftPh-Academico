import type { ChargeDetail } from './charges';
import { serverApiGet } from './server';

/** Server-side only (SSR): the detail of one obligation. */
export function loadCharge(id: string, request: Request): Promise<ChargeDetail> {
  return serverApiGet<ChargeDetail>(`/admin/charges/${id}`, request);
}
