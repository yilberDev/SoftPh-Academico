import type { ResidentDetail } from './residents';
import { serverApiGet } from './server';

/** Server-side only (SSR): the detail of one registered resident (HU-015). */
export function loadResident(id: string, request: Request): Promise<ResidentDetail> {
  return serverApiGet<ResidentDetail>(`/admin/residents/${id}`, request);
}
