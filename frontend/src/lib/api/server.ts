import { API_INTERNAL_URL } from 'astro:env/server';
import { parseResponse } from './errors';

/**
 * Server-side (SSR/middleware) API client. Calls FastAPI through the internal
 * URL and forwards the user's cookies. Read-only: only GET requests are allowed.
 */
export async function serverApiGet<T>(path: string, request?: Request): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const cookie = request?.headers.get('cookie');
  if (cookie) {
    headers.cookie = cookie;
  }
  const response = await fetch(new URL(`/api/v1${path}`, API_INTERNAL_URL), {
    method: 'GET',
    headers
  });
  return parseResponse<T>(response);
}
