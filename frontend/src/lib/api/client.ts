import { parseResponse } from './errors';

/**
 * Browser-side API client. Always uses relative /api/v1 paths so requests
 * stay on the single public origin (Vite proxy in dev, reverse proxy in prod).
 */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: 'same-origin',
    headers: { Accept: 'application/json', ...init.headers }
  });
  return parseResponse<T>(response);
}
