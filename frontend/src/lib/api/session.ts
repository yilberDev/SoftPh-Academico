import { ApiError } from './errors';
import type { SessionInfo } from './auth';
import { serverApiGet } from './server';

/**
 * Server-side only: the current session validated by the backend, or null when
 * the request has no valid session. Other errors (API down) are rethrown.
 */
export async function getSession(request: Request): Promise<SessionInfo | null> {
  try {
    return await serverApiGet<SessionInfo>('/auth/session', request);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return null;
    }
    throw error;
  }
}
