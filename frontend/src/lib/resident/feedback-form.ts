import { ApiError } from '../api/errors.ts';
import type { FeedbackCategory, FeedbackUrgency, NewFeedbackPayload, ResidentFeedbackDetail } from '../api/resident-feedback.ts';
import { CATEGORIES, URGENCIES } from '../feedback/feedback-format.ts';
import { CSRF_MESSAGE, fieldErrorsOf } from '../obligations/charge-format.ts';

/**
 * HU-009: "Enviar feedback" form of the resident (pure, tested with node --test).
 *
 * The checks are the backend's own rules, with its messages, only to help
 * before sending: an optional subject on one line (at most 150 characters), a
 * required message (at most 2000), a category and an urgency. The backend
 * decides again. The body has exactly four fields: never a resident, a status,
 * a response or a note. One POST at a time, never retried automatically.
 */

export const SUBJECT_MAX_LENGTH = 150;
export const MESSAGE_MAX_LENGTH = 2000;

// The backend's messages (schemas/feedback.py).
export const MESSAGE_REQUIRED = 'Escribe el mensaje.';
export const MESSAGE_TOO_LONG = `El mensaje no puede superar ${MESSAGE_MAX_LENGTH} caracteres.`;
export const SUBJECT_TOO_LONG = `El asunto no puede superar ${SUBJECT_MAX_LENGTH} caracteres.`;
export const CATEGORY_MESSAGE = 'Selecciona una categoría: queja, sugerencia, solicitud u otros.';
export const URGENCY_MESSAGE = 'Selecciona la urgencia: alta, media o baja.';

export const SUCCESS_TITLE = 'Feedback enviado correctamente.';
export const SUCCESS_DETAIL = 'Tu feedback quedó registrado como pendiente.';
export const SENDING_LABEL = 'Enviando...';
export const SUBMIT_LABEL = 'Enviar feedback';
export const SESSION_EXPIRED_MESSAGE = 'Tu sesión ha expirado. Inicia sesión nuevamente.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para enviar feedback.';
export const SEND_ERROR = 'No fue posible enviar el feedback. Intenta nuevamente.';
export const INVALID_MESSAGE = 'Revisa los campos señalados.';
export const DISCARD_QUESTION = '¿Descartar el feedback que estás escribiendo?';
export const LIST_PATH = '/resident/feedback';

export type FeedbackField = 'subject' | 'message' | 'category' | 'urgency';
/** In the order of the form (the first error gets the focus). */
export const FEEDBACK_FIELDS: readonly FeedbackField[] = ['subject', 'message', 'category', 'urgency'];
export type FeedbackErrors = Partial<Record<FeedbackField, string>>;

export interface FeedbackFormValues {
  subject: string;
  message: string;
  category: string;
  urgency: string;
}

export const EMPTY_VALUES: FeedbackFormValues = { subject: '', message: '', category: '', urgency: '' };

/** Characters as the backend counts them (Python's len: code points, so an emoji is one). */
export function textLength(text: string): number {
  return [...text].length;
}

/** "120 de 2000 caracteres" for the message counter. */
export function counterText(message: string): string {
  return `${textLength(message)} de ${MESSAGE_MAX_LENGTH} caracteres`;
}

function isCategory(value: string): value is FeedbackCategory {
  return (CATEGORIES as readonly string[]).includes(value);
}

function isUrgency(value: string): value is FeedbackUrgency {
  return (URGENCIES as readonly string[]).includes(value);
}

/** Returns the errors per field and, when there are none, the exact body of the POST. */
export function validateFeedbackForm(values: FeedbackFormValues): { errors: FeedbackErrors; payload: NewFeedbackPayload | null } {
  const errors: FeedbackErrors = {};
  // Same cleaning as the backend: the subject on one line, the message trimmed (its line breaks kept).
  const subject = values.subject.split(/\s+/).filter(Boolean).join(' ');
  const message = values.message.trim();
  if (textLength(subject) > SUBJECT_MAX_LENGTH) errors.subject = SUBJECT_TOO_LONG;
  if (!message) errors.message = MESSAGE_REQUIRED;
  else if (textLength(message) > MESSAGE_MAX_LENGTH) errors.message = MESSAGE_TOO_LONG;
  if (!isCategory(values.category)) errors.category = CATEGORY_MESSAGE;
  if (!isUrgency(values.urgency)) errors.urgency = URGENCY_MESSAGE;
  if (Object.keys(errors).length > 0 || !isCategory(values.category) || !isUrgency(values.urgency)) {
    return { errors, payload: null };
  }
  return { errors, payload: { subject: subject || null, message, category: values.category, urgency: values.urgency } };
}

/** Anything written or chosen: leaving asks first. */
export function hasContent(values: FeedbackFormValues): boolean {
  return Boolean(values.subject.trim() || values.message.trim() || values.category || values.urgency);
}

/** "Cancelar": an empty form leaves at once (no request); one with content asks first. Nothing is ever stored. */
export function cancelAction(values: FeedbackFormValues): 'leave' | 'confirm' {
  return hasContent(values) ? 'confirm' : 'leave';
}

export function detailPath(id: number): string {
  return `${LIST_PATH}/${id}`;
}

// --- Submit -------------------------------------------------------------------------------------------

export type FeedbackSubmitState =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'done'; feedback: ResidentFeedbackDetail }
  | { kind: 'invalid'; errors: FeedbackErrors }
  /** message: shown above the form (null when every error is next to its field). */
  | { kind: 'error'; message: string | null; errors: FeedbackErrors; sessionExpired: boolean };

export function stateAfterError(error: unknown): Extract<FeedbackSubmitState, { kind: 'error' }> {
  const failed = (message: string | null, errors: FeedbackErrors = {}, sessionExpired = false) =>
    ({ kind: 'error', message, errors, sessionExpired }) as const;
  if (!(error instanceof ApiError)) return failed(SEND_ERROR);
  // 401: the draft stays on screen; the page offers the login.
  if (error.status === 401) return failed(SESSION_EXPIRED_MESSAGE, {}, true);
  if (error.status === 403) return failed(error.code === 'CSRF_FAILED' ? CSRF_MESSAGE : FORBIDDEN_MESSAGE);
  if (error.status === 422) {
    const errors = fieldErrorsOf(error, FEEDBACK_FIELDS);
    return failed(Object.keys(errors).length > 0 ? null : INVALID_MESSAGE, errors);
  }
  return failed(SEND_ERROR); // 5xx or an unexpected answer: never a technical text
}

export function createFeedbackFlow(deps: {
  send: (payload: NewFeedbackPayload) => Promise<ResidentFeedbackDetail>;
  render: (state: FeedbackSubmitState) => void;
}) {
  let busy = false;
  return {
    get busy(): boolean {
      return busy;
    },
    async submit(values: FeedbackFormValues): Promise<void> {
      if (busy) return; // no double submission
      const { errors, payload } = validateFeedbackForm(values);
      if (!payload) {
        deps.render({ kind: 'invalid', errors });
        return;
      }
      busy = true;
      deps.render({ kind: 'submitting' });
      try {
        deps.render({ kind: 'done', feedback: await deps.send(payload) });
      } catch (error) {
        deps.render(stateAfterError(error));
      } finally {
        busy = false;
      }
    }
  };
}
