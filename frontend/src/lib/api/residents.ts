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

/**
 * HU-015: one registered resident (administrator only). updated_at is the optimistic-locking
 * token: the update and the deletion send back the value they read (409 VERSION_CONFLICT if
 * someone changed the resident meanwhile).
 */
export interface ResidentDetail extends Resident {
  updated_at: string;
}

/** The registration data the administrator may correct; never the email, role, status or apartment. */
export interface ResidentUpdatePayload {
  full_name: string;
  document_number: string;
  phone: string | null;
  updated_at: string;
}

export function getResident(id: number): Promise<ResidentDetail> {
  return apiFetch<ResidentDetail>(`/admin/residents/${id}`, { cache: 'no-store' });
}

export function updateResident(id: number, payload: ResidentUpdatePayload, csrfToken: string): Promise<ResidentDetail> {
  return apiFetch<ResidentDetail>(`/admin/residents/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
    csrfToken
  });
}

/** Logical deletion (204, no body). */
export async function deleteResident(id: number, updatedAt: string, csrfToken: string): Promise<void> {
  await apiFetch<null>(`/admin/residents/${id}?updated_at=${encodeURIComponent(updatedAt)}`, {
    method: 'DELETE',
    csrfToken
  });
}
