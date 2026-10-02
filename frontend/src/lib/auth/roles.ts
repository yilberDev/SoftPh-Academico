/**
 * Roles and their pages (HU-002 administrator, HU-007 resident; HU-018 decision in page-access.ts). Pure module.
 *
 * The role always comes from the backend (login or session); it is only used
 * here to choose the home page and to improve navigation. The backend
 * authorizes every API request on its own.
 */

export type UserRole = 'ADMINISTRADOR' | 'RESIDENTE';

/** Home page of each role, used after login and when a signed-in user opens /login. */
export const HOME_BY_ROLE: Record<UserRole, string> = {
  ADMINISTRADOR: '/admin',
  RESIDENTE: '/resident'
};

export function homePathForRole(role: UserRole): string {
  return HOME_BY_ROLE[role] ?? '/';
}

/** Protected page prefixes and the roles allowed on them (checked by the SSR middleware). */
export const PROTECTED_PAGES: readonly { prefix: string; roles: readonly UserRole[] }[] = [
  { prefix: '/admin', roles: ['ADMINISTRADOR'] },
  { prefix: '/resident', roles: ['RESIDENTE'] }
];

function isUnder(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}/`);
}

export function protectedRule(path: string): { prefix: string; roles: readonly UserRole[] } | undefined {
  return PROTECTED_PAGES.find(({ prefix }) => isUnder(path, prefix));
}

export function isLoginPage(path: string): boolean {
  return isUnder(path, '/login');
}

/** Entry point of password recovery. The recovery process itself is HU-016. */
export const FORGOT_PASSWORD_PATH = '/forgot-password';
