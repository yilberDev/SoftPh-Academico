import { apiFetch } from './client';

/** HU-006: registered residents (GET /api/v1/admin/residents, administrator only). */

export type ResidentStatus = 'ACTIVE' | 'INACTIVE';
export type ResidentSortField = 'name' | 'document' | 'unit' | 'phone' | 'email' | 'status';
export type SortOrder = 'asc' | 'desc';

export interface Resident {
  id: number;
  full_name: string;
  document_number: string | null;
  phone: string | null;
  email: string;
  unit: { id: number; label: string };
  status: ResidentStatus;
}

export interface ResidentPage {
  items: Resident[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

/** `query` is the query string built by lib/residents/residents-list.ts (whitelisted values only). */
export function listResidents(query: string): Promise<ResidentPage> {
  return apiFetch<ResidentPage>(`/admin/residents${query ? `?${query}` : ''}`, { cache: 'no-store' });
}
