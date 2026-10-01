import { apiFetch } from './client';

export type UserRole = 'ADMINISTRADOR' | 'RESIDENTE';

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

/** Home page for each role. RESIDENTE is added with HU-007. */
const HOME_BY_ROLE: Partial<Record<UserRole, string>> = {
  ADMINISTRADOR: '/admin'
};

export function homePathForRole(role: UserRole): string {
  return HOME_BY_ROLE[role] ?? '/';
}

/** Single login for every role; the backend decides the role (HU-002). */
export function login(email: string, password: string): Promise<SessionInfo> {
  return apiFetch<SessionInfo>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password })
  });
}

export function logout(csrfToken: string): Promise<null> {
  return apiFetch<null>('/auth/logout', { method: 'POST', csrfToken });
}
