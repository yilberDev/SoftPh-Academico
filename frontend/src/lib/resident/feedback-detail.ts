import { ApiError } from '../api/errors.ts';
import type { ResidentFeedbackDetail } from '../api/resident-feedback.ts';
import { STATUS_LABELS, classificationOf, formatDateTime, subjectText } from '../feedback/feedback-format.ts';

/**
 * HU-009: detail of one feedback of the signed-in resident (pure, tested with node --test).
 *
 * Everything shown comes from GET /resident/feedback/{id}: sending date,
 * subject, message, category, urgency and current status. The API never sends
 * the administrator's response, the internal note or the resolution date, and
 * nothing here asks for them elsewhere. created_at is a UTC instant, shown in
 * Bogotá time.
 */

export const NOT_FOUND_MESSAGE = 'Feedback no encontrado.';
export const NOT_FOUND_HINT = 'Es posible que el enlace sea incorrecto o que el feedback no esté registrado en tu cuenta.';
export const FORBIDDEN_MESSAGE = 'No tienes permiso para acceder a esta sección.';
export const LOAD_ERROR = 'No fue posible cargar el feedback. Intenta nuevamente.';

// Database ids are positive BIGINTs; anything else never reaches the API path.
const ID_PATTERN = /^[1-9]\d{0,18}$/;

export function isFeedbackId(value: string | undefined): value is string {
  return value !== undefined && ID_PATTERN.test(value);
}

/** What the detail page shows. A feedback of another resident is "not found", exactly like a missing one. */
export type DetailPage =
  | { kind: 'feedback'; feedback: ResidentFeedbackDetail }
  | { kind: 'not-found' }
  /** The session ended between the page check and the API call: back to the login. */
  | { kind: 'unauthenticated' }
  | { kind: 'forbidden' }
  | { kind: 'error' };

export async function loadDetailPage(id: string | undefined, load: (id: string) => Promise<ResidentFeedbackDetail>): Promise<DetailPage> {
  if (!isFeedbackId(id)) return { kind: 'not-found' }; // the API is not called
  try {
    return { kind: 'feedback', feedback: await load(id) };
  } catch (error) {
    if (!(error instanceof ApiError)) return { kind: 'error' };
    if (error.status === 404 || error.status === 422) return { kind: 'not-found' };
    if (error.status === 401) return { kind: 'unauthenticated' };
    if (error.status === 403) return { kind: 'forbidden' };
    return { kind: 'error' };
  }
}

export interface Line {
  label: string;
  value: string;
  /** No value recorded (no subject, or sent before HU-009). */
  muted: boolean;
}

export function detailLines(feedback: ResidentFeedbackDetail): Line[] {
  const classification = classificationOf(feedback);
  return [
    { label: 'Fecha de envío', value: formatDateTime(feedback.created_at), muted: false },
    { label: 'Asunto', value: subjectText(feedback.subject), muted: !feedback.subject },
    { label: 'Categoría', value: classification.category, muted: !classification.categoryRecorded },
    { label: 'Urgencia', value: classification.urgency, muted: !classification.urgencyRecorded },
    { label: 'Estado', value: STATUS_LABELS[feedback.status], muted: false }
  ];
}
