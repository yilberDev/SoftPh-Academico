import { parseResponse } from './errors';

export const CSRF_HEADER_NAME = 'X-CSRF-Token';

interface ApiFetchOptions extends RequestInit {
  /** Required for state-changing requests made with a session. */
  csrfToken?: string;
}

/**
 * Browser-side API client. Always uses relative /api/v1 paths so requests
 * stay on the single public origin (Vite proxy in dev, reverse proxy in prod).
 */
export async function apiFetch<T>(path: string, { csrfToken, ...init }: ApiFetchOptions = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (csrfToken) {
    headers.set(CSRF_HEADER_NAME, csrfToken);
  }
  const response = await fetch(`/api/v1${path}`, { ...init, headers, credentials: 'same-origin' });
  return parseResponse<T>(response);
}
