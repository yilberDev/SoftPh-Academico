import { apiFetch } from './client';
import type { UserRole } from '../auth/roles';

export { homePathForRole, type UserRole } from '../auth/roles';

export interface AuthenticatedUser {
  id: number;
  full_name: string;
  email: string;
  role: UserRole;
}

export interface SessionInfo {
  user: AuthenticatedUser;
  csrf_token: string;
}

/** Single login for every role; the backend decides the role (HU-002, HU-007). */
export function login(email: string, password: string): Promise<SessionInfo> {
  return apiFetch<SessionInfo>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password })
  });
}

export function logout(csrfToken: string): Promise<null> {
  return apiFetch<null>('/auth/logout', { method: 'POST', csrfToken });
}
