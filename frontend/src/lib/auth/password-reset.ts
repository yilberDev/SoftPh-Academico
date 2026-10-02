import { ApiError } from '../api/errors.ts';
import type { ResetConfirmPayload, ResetPreview } from '../api/password-reset.ts';
import { readToken } from './fragment-token.ts';
import { validateNewPassword, type NewPasswordErrors } from './password-policy.ts';

/**
 * Password reset of residents (HU-016), without DOM.
 *
 * /forgot-password: the answer is always the same message, so the page never
 * reveals whether an email belongs to a resident.
 * /reset-password: the token is read once from the fragment, removed from the
 * address bar and kept only in memory (never in storage or cookies).
 */

export const CONNECTION_ERROR = 'No fue posible conectar con el servidor. Intenta de nuevo.';
/** The only answer to a request, for any email. */
export const REQUEST_RECEIVED =
  'Si la cuenta corresponde a un residente registrado, recibirás en tu correo un enlace para restablecer la contraseña. El enlace vence en 1 hora.';

const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const EMAIL_MAX_LENGTH = 254;

// --- /forgot-password ----------------------------------------------------------------------------

export type ForgotState =
  | { kind: 'idle'; error: string | null }
  | { kind: 'sending' }
  | { kind: 'received'; message: string }
  | { kind: 'error'; message: string };

export function validateResetEmail(email: string): string | null {
  const value = email.trim();
  if (!value) {
    return 'Ingresa tu correo electrónico.';
  }
  if (value.length > EMAIL_MAX_LENGTH || !EMAIL_SHAPE.test(value)) {
    return 'Ingresa un correo electrónico válido.';
  }
  return null;
}

export interface ForgotDeps {
  request: (email: string) => Promise<unknown>;
  render: (state: ForgotState) => void;
}

export function createForgotFlow({ request, render }: ForgotDeps) {
  let state: ForgotState = { kind: 'idle', error: null };
  const set = (next: ForgotState): void => {
    state = next;
    render(state);
  };
  return {
    get state() {
      return state;
    },
    async submit(email: string): Promise<void> {
      if (state.kind === 'sending' || state.kind === 'received') {
        return; // no double submission
      }
      const error = validateResetEmail(email);
      if (error) {
        set({ kind: 'idle', error });
        return;
      }
      set({ kind: 'sending' });
      try {
        await request(email.trim());
        set({ kind: 'received', message: REQUEST_RECEIVED });
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 422) {
          set({ kind: 'idle', error: caught.details[0]?.message ?? caught.message });
        } else {
          set({ kind: 'error', message: caught instanceof ApiError ? caught.message : CONNECTION_ERROR });
        }
      }
    }
  };
}

// --- /reset-password -------------------------------------------------------------------------------

export type ResetState =
  | { kind: 'verifying' }
  | { kind: 'invalid' }
  | { kind: 'expired' }
  | { kind: 'used' }
  | { kind: 'revoked' }
  /** Valid link: the new password form. */
  | { kind: 'ready'; preview: ResetPreview; errors: NewPasswordErrors; alert: string | null }
  | { kind: 'updating'; preview: ResetPreview }
  | { kind: 'done' }
  | { kind: 'error'; message: string };

const LINK_STATES: Record<string, ResetState> = {
  PASSWORD_RESET_INVALID: { kind: 'invalid' },
  PASSWORD_RESET_EXPIRED: { kind: 'expired' },
  PASSWORD_RESET_USED: { kind: 'used' },
  PASSWORD_RESET_REVOKED: { kind: 'revoked' }
};

function linkState(error: unknown): ResetState | null {
  if (error instanceof ApiError && (error.status === 404 || error.status === 410)) {
    return LINK_STATES[error.code] ?? null;
  }
  return null;
}

export interface ResetDeps {
  verify: (token: string) => Promise<ResetPreview>;
  confirm: (payload: ResetConfirmPayload) => Promise<unknown>;
  /** Removes the fragment from the address bar (history.replaceState). */
  clearToken: () => void;
  render: (state: ResetState) => void;
}

export function createResetFlow({ verify, confirm, clearToken, render }: ResetDeps) {
  let state: ResetState = { kind: 'verifying' };
  let token: string | null = null; // memory only
  const set = (next: ResetState): void => {
    state = next;
    render(state);
  };
  return {
    get state() {
      return state;
    },

    async start(hash: string): Promise<void> {
      token = readToken(hash);
      clearToken(); // before any network call, whatever the fragment contained
      if (token === null) {
        set({ kind: 'invalid' });
        return;
      }
      set({ kind: 'verifying' });
      try {
        set({ kind: 'ready', preview: await verify(token), errors: {}, alert: null });
      } catch (error) {
        set(linkState(error) ?? { kind: 'error', message: error instanceof ApiError ? error.message : CONNECTION_ERROR });
      }
    },

    async submit(password: string, confirmation: string): Promise<void> {
      if (state.kind !== 'ready' || token === null) {
        return; // only from the form: a second click while updating is ignored
      }
      const { preview } = state;
      const errors = validateNewPassword(password, confirmation);
      if (Object.keys(errors).length > 0) {
        set({ kind: 'ready', preview, errors, alert: null });
        return;
      }
      set({ kind: 'updating', preview });
      try {
        await confirm({ token, password, password_confirmation: confirmation });
        token = null;
        set({ kind: 'done' });
      } catch (error) {
        const link = linkState(error);
        if (link) {
          token = null;
          set(link);
        } else if (error instanceof ApiError && error.status === 422) {
          const fieldErrors: NewPasswordErrors = {};
          for (const detail of error.details) {
            if (detail.field === 'password' || detail.field === 'password_confirmation') {
              fieldErrors[detail.field] ??= detail.message;
            }
          }
          set({ kind: 'ready', preview, errors: fieldErrors, alert: Object.keys(fieldErrors).length ? null : error.message });
        } else {
          set({ kind: 'ready', preview, errors: {}, alert: error instanceof ApiError ? error.message : CONNECTION_ERROR });
        }
      }
    }
  };
}
