import type { SessionInfo } from './auth';
import { serverApiGet } from './server';
import { sessionOrNull } from '../auth/page-access';

/**
 * Server-side only: the current session validated by the backend, or null when
 * the request has no valid session. Other errors (API down) are rethrown.
 */
export function getSession(request: Request): Promise<SessionInfo | null> {
  return sessionOrNull(() => serverApiGet<SessionInfo>('/auth/session', request));
}
