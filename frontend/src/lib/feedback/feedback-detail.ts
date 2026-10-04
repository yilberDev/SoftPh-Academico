import { ApiError } from '../api/errors.ts';
import type { FeedbackActionResult, FeedbackDetail, NotificationStatus } from '../api/feedback.ts';

/**
 * HU-005 criteria 2-4: feedback detail, response and resolution (pure, tested
 * with node --test).
 *
 * Every change carries the version the administrator saw. If someone changed
 * the feedback meanwhile (409 VERSION_CONFLICT) or it was already resolved
 * (409 FEEDBACK_ALREADY_RESOLVED), the detail is reloaded and nothing is lost
 * silently. The response (sent to the resident) and the resolution note
 * (internal) are always kept apart.
 */

export const TEXT_MAX_LENGTH = 2000;

export const ACTION_LABELS: Record<string, string> = {
  FEEDBACK_RESPONDED: 'Respuesta registrada',
  FEEDBACK_RESOLVED: 'Marcado como resuelto',
  FEEDBACK_NOTIFICATION_SENT: 'Notificación enviada al residente',
  FEEDBACK_NOTIFICATION_FAILED: 'No fue posible notificar al residente'
};

export const VERSION_CONFLICT_MESSAGE =
  'El feedback fue actualizado en otra sesión. Vuelve a cargar la información antes de realizar cambios.';
export const ALREADY_RESOLVED_MESSAGE = 'Este feedback ya fue marcado como resuelto.';
export const NOT_FOUND_MESSAGE = 'Este feedback ya no existe.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para gestionar el feedback.';
export const CONNECTION_ERROR = 'No fue posible conectar con el servidor. Intenta de nuevo.';
export const RESPONSE_SAVED = 'Respuesta guardada y enviada al residente.';
export const RESOLVED_DONE = 'Feedback marcado como resuelto. Se notificó al residente.';
export const NOTIFICATION_FAILED =
  'Los cambios quedaron guardados, pero no fue posible enviar la notificación al residente.';
export const RESPONSE_REQUIRED = 'Escribe la respuesta para el residente.';
export const NOTE_REQUIRED = 'Escribe la nota de resolución.';

const DATE_TIME = new Intl.DateTimeFormat('es-CO', {
  timeZone: 'America/Bogota',
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit'
});

/** "2 de octubre de 2026, 3:05 p. m." in Bogotá time, for an ISO UTC instant. */
export function formatDateTime(iso: string): string {
  return DATE_TIME.format(new Date(iso));
}

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

/** Same rule as the backend (format only): trimmed, required, at most 2000 characters. */
export function validateText(text: string, required: string): { value: string } | { error: string } {
  const value = text.trim();
  if (!value) return { error: required };
  if (value.length > TEXT_MAX_LENGTH) return { error: `El texto no puede superar ${TEXT_MAX_LENGTH} caracteres.` };
  return { value };
}

export type Field = 'response' | 'resolution_note';

export interface DetailState {
  feedback: FeedbackDetail;
  busy: 'response' | 'resolve' | null;
  /** The resolution waits for the administrator's confirmation. */
  confirming: boolean;
  notice: { tone: 'success' | 'warning' | 'error'; text: string } | null;
  errors: Partial<Record<Field, string>>;
}

export interface DetailDeps {
  respond: (id: number, response: string, version: number) => Promise<FeedbackActionResult>;
  resolve: (id: number, note: string, version: number) => Promise<FeedbackActionResult>;
  reload: (id: number) => Promise<FeedbackDetail>;
  render: (state: DetailState) => void;
  navigate: (path: string) => void;
}

function successNotice(notification: NotificationStatus, success: string): DetailState['notice'] {
  return notification === 'SENT' ? { tone: 'success', text: success } : { tone: 'warning', text: NOTIFICATION_FAILED };
}

export function createFeedbackDetail(initial: FeedbackDetail, deps: DetailDeps) {
  let state: DetailState = { feedback: initial, busy: null, confirming: false, notice: null, errors: {} };
  let pendingNote = '';

  function set(changes: Partial<DetailState>): void {
    state = { ...state, ...changes };
    deps.render(state);
  }

  async function reloadAfter(text: string, tone: 'warning' | 'error' = 'warning'): Promise<void> {
    try {
      set({ feedback: await deps.reload(state.feedback.id), busy: null, confirming: false, notice: { tone, text }, errors: {} });
    } catch {
      set({ busy: null, confirming: false, notice: { tone: 'error', text } });
    }
  }

  async function handleError(error: unknown, field: Field): Promise<void> {
    if (error instanceof ApiError) {
      if (error.status === 401) {
        deps.navigate('/login');
        return;
      }
      if (error.status === 409 && error.code === 'VERSION_CONFLICT') return reloadAfter(VERSION_CONFLICT_MESSAGE);
      if (error.status === 409 && error.code === 'FEEDBACK_ALREADY_RESOLVED') return reloadAfter(ALREADY_RESOLVED_MESSAGE);
      if (error.status === 404) {
        set({ busy: null, confirming: false, notice: { tone: 'error', text: NOT_FOUND_MESSAGE } });
        return;
      }
      if (error.status === 403 && error.code === 'FORBIDDEN') {
        set({ busy: null, confirming: false, notice: { tone: 'error', text: FORBIDDEN_MESSAGE } });
        return;
      }
      if (error.status === 422) {
        const detail = error.details.find((d) => d.field === field);
        set({ busy: null, confirming: false, notice: detail ? null : { tone: 'error', text: error.message },
              errors: detail ? { [field]: detail.message } : {} });
        return;
      }
      set({ busy: null, confirming: false, notice: { tone: 'error', text: error.message } });
      return;
    }
    set({ busy: null, confirming: false, notice: { tone: 'error', text: CONNECTION_ERROR } });
  }

  return {
    get state(): DetailState {
      return state;
    },
    /** Saves (or replaces, while pending) the response sent to the resident. */
    async respond(text: string): Promise<void> {
      if (state.busy || state.feedback.status !== 'PENDING') return;
      const checked = validateText(text, RESPONSE_REQUIRED);
      if ('error' in checked) {
        set({ errors: { response: checked.error }, notice: null });
        return;
      }
      set({ busy: 'response', notice: null, errors: {} });
      try {
        const result = await deps.respond(state.feedback.id, checked.value, state.feedback.version);
        set({ feedback: result.feedback, busy: null, notice: successNotice(result.notification_status, RESPONSE_SAVED) });
      } catch (error) {
        await handleError(error, 'response');
      }
    },
    /** First step: validates the note and asks for confirmation (nothing is sent yet). */
    askResolve(note: string): void {
      if (state.busy || state.feedback.status !== 'PENDING') return;
      const checked = validateText(note, NOTE_REQUIRED);
      if ('error' in checked) {
        set({ errors: { resolution_note: checked.error }, notice: null });
        return;
      }
      pendingNote = checked.value;
      set({ confirming: true, errors: {}, notice: null });
    },
    cancelResolve(): void {
      pendingNote = '';
      set({ confirming: false });
    },
    /** Second step: sends the resolution, once. */
    async confirmResolve(): Promise<void> {
      if (!state.confirming || state.busy) return;
      set({ busy: 'resolve' });
      try {
        const result = await deps.resolve(state.feedback.id, pendingNote, state.feedback.version);
        pendingNote = '';
        set({ feedback: result.feedback, busy: null, confirming: false,
              notice: successNotice(result.notification_status, RESOLVED_DONE) });
      } catch (error) {
        await handleError(error, 'resolution_note');
      }
    }
  };
}
