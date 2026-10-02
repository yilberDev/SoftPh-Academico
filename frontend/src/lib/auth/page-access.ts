import { ApiError } from '../api/errors.ts';
import type { SessionInfo } from '../api/auth.ts';
import { homePathForRole, isLoginPage, protectedRule } from './roles.ts';

/**
 * Access decision for a page request (HU-018), applied by the SSR middleware.
 *
 * Pure module: the session always comes from the backend (GET /auth/session,
 * through `loadSession`), never from the browser. This only improves
 * navigation; the backend authorizes every API request on its own.
 */
export type PageAccess =
  /** No session needed (the session endpoint is not even called), or /login without a session. */
  | { kind: 'public' }
  /** Protected page without a valid session (→ /login), or a signed-in user on /login (→ home). */
  | { kind: 'redirect'; location: string }
  /** Valid session of another role: "Acceso no autorizado" (403), without the page or its data. */
  | { kind: 'forbidden' }
  | { kind: 'allowed'; session: SessionInfo }
  /** The session could not be checked (API down): 503, never the page. */
  | { kind: 'unavailable' };

/** The session endpoint answers 401 without a valid session: that is "no session", not an error. */
export async function sessionOrNull(load: () => Promise<SessionInfo>): Promise<SessionInfo | null> {
  try {
    return await load();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return null;
    }
    throw error;
  }
}

export async function decidePageAccess(
  path: string,
  loadSession: () => Promise<SessionInfo | null>
): Promise<PageAccess> {
  const rule = protectedRule(path);
  const loginPage = isLoginPage(path);
  if (!rule && !loginPage) {
    return { kind: 'public' };
  }

  let session: SessionInfo | null;
  try {
    session = await loadSession();
  } catch {
    return { kind: 'unavailable' };
  }

  if (!rule) {
    // The login page: a signed-in user goes to the home page of the role.
    return session ? { kind: 'redirect', location: homePathForRole(session.user.role) } : { kind: 'public' };
  }
  if (!session) {
    return { kind: 'redirect', location: '/login' };
  }
  if (!rule.roles.includes(session.user.role)) {
    return { kind: 'forbidden' };
  }
  return { kind: 'allowed', session };
}
