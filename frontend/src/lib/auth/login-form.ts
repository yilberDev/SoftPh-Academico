import { ApiError } from '../api/errors.ts';
import type { SessionInfo } from '../api/auth.ts';
import { homePathForRole } from './roles.ts';

/**
 * Single login form for every role (HU-002, HU-007), without DOM.
 *
 * The password is only passed through to the API: it is never kept in the
 * state, in storage or anywhere else. The session cookie is set and managed by
 * the backend; the destination depends only on the role the backend returns.
 */

export const CONNECTION_ERROR = 'No fue posible conectar con el servidor. Intenta de nuevo.';

export type LoginField = 'email' | 'password';

export type LoginState =
  | { kind: 'idle'; errors: Partial<Record<LoginField, string>> }
  | { kind: 'loading' }
  /** 401: the same generic message for every failure cause. */
  | { kind: 'invalid'; message: string }
  /** 429: too many failed attempts. */
  | { kind: 'locked'; message: string }
  | { kind: 'error'; message: string }
  | { kind: 'success'; redirectTo: string };

export interface LoginDeps {
  login: (email: string, password: string) => Promise<SessionInfo>;
  navigate: (path: string) => void;
  render: (state: LoginState) => void;
}

export interface LoginFlow {
  readonly state: LoginState;
  submit(email: string, password: string): Promise<void>;
}

/** Format checks only: required fields. The backend validates the credentials. */
export function validateLogin(email: string, password: string): Partial<Record<LoginField, string>> {
  const errors: Partial<Record<LoginField, string>> = {};
  if (!email.trim()) {
    errors.email = 'Ingresa tu correo electrónico.';
  }
  if (!password) {
    errors.password = 'Ingresa tu contraseña.';
  }
  return errors;
}

export function createLoginFlow({ login, navigate, render }: LoginDeps): LoginFlow {
  let state: LoginState = { kind: 'idle', errors: {} };

  function set(next: LoginState): void {
    state = next;
    render(state);
  }

  return {
    get state() {
      return state;
    },

    async submit(email, password) {
      // A second click while signing in (or after success) sends nothing.
      if (state.kind === 'loading' || state.kind === 'success') {
        return;
      }
      const errors = validateLogin(email, password);
      if (Object.keys(errors).length > 0) {
        set({ kind: 'idle', errors });
        return;
      }
      set({ kind: 'loading' });
      try {
        const session = await login(email.trim(), password);
        const redirectTo = homePathForRole(session.user.role);
        set({ kind: 'success', redirectTo });
        navigate(redirectTo);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          set({ kind: 'invalid', message: error.message });
        } else if (error instanceof ApiError && error.status === 429) {
          set({ kind: 'locked', message: error.message });
        } else if (error instanceof ApiError) {
          set({ kind: 'error', message: error.message });
        } else {
          set({ kind: 'error', message: CONNECTION_ERROR });
        }
      }
    }
  };
}
