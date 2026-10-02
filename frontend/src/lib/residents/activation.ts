import { ApiError } from '../api/errors.ts';
import type { ActivationPayload, InvitationPreview } from '../api/activation.ts';

/**
 * /activate-account (HU-019), without DOM.
 *
 * The token is read once from the URL fragment (#token=...), removed from the
 * address bar right away and kept only in memory (never in storage or
 * cookies). It is sent to the backend only in POST bodies. Format checks here
 * improve the experience; the backend decides everything else.
 */

const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const DOCUMENT_PATTERN = /^[A-Za-z0-9]{5,20}$/;
const PHONE_PATTERN = /^\+?\d{7,15}$/;
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;
export const CONNECTION_ERROR = 'No fue posible conectar con el servidor. Intenta de nuevo.';

export type ActivationField = 'document_number' | 'phone' | 'password' | 'password_confirmation';
export type FieldErrors = Partial<Record<ActivationField, string>>;
const FIELDS: ActivationField[] = ['document_number', 'phone', 'password', 'password_confirmation'];

export interface ActivationFormValues {
  documentNumber: string;
  phone: string;
  password: string;
  passwordConfirmation: string;
}

export type ActivationState =
  | { kind: 'verifying' }
  | { kind: 'invalid' }
  | { kind: 'expired' }
  | { kind: 'used' }
  /** Valid invitation: its data and the activation form. */
  | { kind: 'ready'; invitation: InvitationPreview; errors: FieldErrors; alert: string | null }
  | { kind: 'activating'; invitation: InvitationPreview }
  | { kind: 'activated' }
  | { kind: 'error'; message: string };

/** The token of "#token=..." if it looks like one; null otherwise. */
export function readToken(hash: string): string | null {
  const token = new URLSearchParams(hash.replace(/^#/, '')).get('token');
  return token !== null && TOKEN_PATTERN.test(token) ? token : null;
}

/** Format checks only. Returns the errors and, if there are none, the data to send (without token). */
export function validateActivationForm(values: ActivationFormValues): {
  errors: FieldErrors;
  payload: Omit<ActivationPayload, 'token'> | null;
} {
  const errors: FieldErrors = {};
  const document = values.documentNumber.trim();
  const phone = values.phone.replace(/[\s-]/g, '');

  if (!document) {
    errors.document_number = 'Ingresa tu número de documento.';
  } else if (!DOCUMENT_PATTERN.test(document)) {
    errors.document_number = 'El número de documento debe tener entre 5 y 20 letras o dígitos, sin espacios ni símbolos.';
  }
  if (phone && !PHONE_PATTERN.test(phone)) {
    errors.phone = 'El teléfono debe tener entre 7 y 15 dígitos, con un «+» inicial opcional.';
  }
  if (!values.password) {
    errors.password = 'Crea una contraseña.';
  } else if (values.password.length < PASSWORD_MIN_LENGTH || values.password.length > PASSWORD_MAX_LENGTH) {
    errors.password = `La contraseña debe tener entre ${PASSWORD_MIN_LENGTH} y ${PASSWORD_MAX_LENGTH} caracteres.`;
  } else if (!values.password.trim()) {
    errors.password = 'La contraseña no puede estar formada solo por espacios.';
  }
  if (!values.passwordConfirmation) {
    errors.password_confirmation = 'Confirma tu contraseña.';
  } else if (values.passwordConfirmation !== values.password) {
    errors.password_confirmation = 'La confirmación no coincide con la contraseña.';
  }

  if (Object.keys(errors).length > 0) {
    return { errors, payload: null };
  }
  return {
    errors,
    payload: {
      document_number: document,
      phone: phone || null,
      password: values.password,
      password_confirmation: values.passwordConfirmation
    }
  };
}

/** States for a link that cannot be used (404 / 410); null for other errors. */
function linkState(error: unknown): ActivationState | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  if (error.status === 404 && error.code === 'INVITATION_INVALID') {
    return { kind: 'invalid' };
  }
  if (error.status === 410 && error.code === 'INVITATION_EXPIRED') {
    return { kind: 'expired' };
  }
  if (error.status === 410 && error.code === 'INVITATION_USED') {
    return { kind: 'used' };
  }
  return null;
}

export interface ActivationDeps {
  verify: (token: string) => Promise<InvitationPreview>;
  activate: (payload: ActivationPayload) => Promise<unknown>;
  /** Removes the fragment from the address bar (history.replaceState). */
  clearToken: () => void;
  render: (state: ActivationState) => void;
}

export interface ActivationFlow {
  readonly state: ActivationState;
  start(hash: string): Promise<void>;
  submit(values: ActivationFormValues): Promise<void>;
}

export function createActivationFlow({ verify, activate, clearToken, render }: ActivationDeps): ActivationFlow {
  let state: ActivationState = { kind: 'verifying' };
  let token: string | null = null; // memory only

  function set(next: ActivationState): void {
    state = next;
    render(state);
  }

  return {
    get state() {
      return state;
    },

    async start(hash) {
      token = readToken(hash);
      clearToken(); // before any network call, whatever the fragment contained
      if (token === null) {
        set({ kind: 'invalid' });
        return;
      }
      set({ kind: 'verifying' });
      try {
        const invitation = await verify(token);
        set({ kind: 'ready', invitation, errors: {}, alert: null });
      } catch (error) {
        set(linkState(error) ?? { kind: 'error', message: error instanceof ApiError ? error.message : CONNECTION_ERROR });
      }
    },

    async submit(values) {
      // Only from the form: a second click while activating is ignored.
      if (state.kind !== 'ready' || token === null) {
        return;
      }
      const { invitation } = state;
      const { errors, payload } = validateActivationForm(values);
      if (!payload) {
        set({ kind: 'ready', invitation, errors, alert: null });
        return;
      }
      set({ kind: 'activating', invitation });
      try {
        await activate({ token, ...payload });
        token = null;
        set({ kind: 'activated' });
      } catch (error) {
        const link = linkState(error);
        if (link) {
          token = null;
          set(link);
        } else if (error instanceof ApiError && error.code === 'DOCUMENT_ALREADY_REGISTERED') {
          set({ kind: 'ready', invitation, errors: { document_number: error.message }, alert: null });
        } else if (error instanceof ApiError && error.status === 422) {
          const fieldErrors: FieldErrors = {};
          for (const detail of error.details) {
            if (detail.field && (FIELDS as string[]).includes(detail.field)) {
              fieldErrors[detail.field as ActivationField] ??= detail.message;
            }
          }
          const alert = Object.keys(fieldErrors).length > 0 ? null : error.message;
          set({ kind: 'ready', invitation, errors: fieldErrors, alert });
        } else if (error instanceof ApiError && error.code === 'UNIT_NOT_AVAILABLE') {
          set({ kind: 'error', message: `${error.message} Comunícate con la administración.` });
        } else {
          set({ kind: 'ready', invitation, errors: {}, alert: error instanceof ApiError ? error.message : CONNECTION_ERROR });
        }
      }
    }
  };
}
