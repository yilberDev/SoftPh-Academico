import type { DeliveryStatus, Invitation, InvitationPayload } from '../api/invitations.ts';

/**
 * Invitation form (HU-003), without DOM: format checks for a better
 * experience and the texts of each result. Business rules (availability,
 * duplicates, sending) are decided by the backend.
 */

export const VALIDITY_MIN_DAYS = 1;
export const VALIDITY_MAX_DAYS = 30;
export const DEFAULT_VALIDITY_DAYS = 7;
export const EMAIL_MAX_LENGTH = 254;
export const NAME_MIN_LENGTH = 3;
export const NAME_MAX_LENGTH = 150;

export type InvitationField = 'email' | 'full_name' | 'unit_id' | 'validity_days';
export type FieldErrors = Partial<Record<InvitationField, string>>;

export interface InvitationFormValues {
  email: string;
  fullName: string;
  unitId: string;
  validityDays: string;
}

const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const POSITIVE_INTEGER = /^\d+$/;

/** Format checks only. Returns the errors per field and, if there are none, the payload. */
export function validateInvitationForm(values: InvitationFormValues): {
  errors: FieldErrors;
  payload: InvitationPayload | null;
} {
  const errors: FieldErrors = {};
  const email = values.email.trim();
  const fullName = values.fullName.split(/\s+/).filter(Boolean).join(' ');

  if (!email) {
    errors.email = 'Ingresa el correo electrónico del residente.';
  } else if (email.length > EMAIL_MAX_LENGTH || !EMAIL_SHAPE.test(email)) {
    errors.email = 'Ingresa un correo electrónico válido.';
  }

  if (!fullName) {
    errors.full_name = 'Ingresa el nombre completo del residente.';
  } else if (fullName.length < NAME_MIN_LENGTH || fullName.length > NAME_MAX_LENGTH) {
    errors.full_name = `El nombre completo debe tener entre ${NAME_MIN_LENGTH} y ${NAME_MAX_LENGTH} caracteres.`;
  }

  const unitId = values.unitId.trim();
  if (!POSITIVE_INTEGER.test(unitId) || Number(unitId) < 1) {
    errors.unit_id = 'Selecciona un apartamento disponible.';
  }

  const days = values.validityDays.trim();
  const validityDays = Number(days);
  if (!POSITIVE_INTEGER.test(days) || validityDays < VALIDITY_MIN_DAYS || validityDays > VALIDITY_MAX_DAYS) {
    errors.validity_days = `La vigencia debe estar entre ${VALIDITY_MIN_DAYS} y ${VALIDITY_MAX_DAYS} días.`;
  }

  if (Object.keys(errors).length > 0) {
    return { errors, payload: null };
  }
  return {
    errors,
    payload: { email, full_name: fullName, unit_id: Number(unitId), validity_days: validityDays }
  };
}

const BOGOTA_PARTS = new Intl.DateTimeFormat('es-CO', {
  timeZone: 'America/Bogota',
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  hour12: true
});

/**
 * "8 de octubre de 2026, 3:05 p. m. (hora de Colombia)" from an ISO UTC date:
 * the same text as the invitation email sent by the backend.
 */
export function formatBogota(isoUtc: string): string {
  const parts = Object.fromEntries(
    BOGOTA_PARTS.formatToParts(new Date(isoUtc)).map(({ type, value }) => [type, value])
  );
  // ICU uses non-breaking spaces inside "p. m.".
  const period = (parts.dayPeriod ?? '').replace(/[\u00a0\u202f]/g, ' ');
  return `${parts.day} de ${parts.month} de ${parts.year}, ${parts.hour}:${parts.minute} ${period} (hora de Colombia)`;
}

export type OutcomeTone = 'success' | 'warning';

export interface InvitationOutcome {
  tone: OutcomeTone;
  title: string;
  detail: string;
  /** The email was not confirmed: offer "Reenviar". */
  canResend: boolean;
}

/** Confirmation shown after creating (CA-7) or resending an invitation. */
export function invitationOutcome(invitation: Invitation, resent = false): InvitationOutcome {
  const expiry = `Vence el ${formatBogota(invitation.expires_at)}.`;
  if (invitation.delivery_status === 'SENT') {
    return {
      tone: 'success',
      title: `${resent ? 'Invitación reenviada' : 'Invitación enviada'} a ${invitation.email}`,
      detail: `${invitation.unit.label}. ${expiry}`,
      canResend: false
    };
  }
  const subject = resent ? 'La invitación sigue vigente' : 'La invitación se creó';
  const problem =
    invitation.delivery_status === 'FAILED'
      ? 'pero el correo no pudo enviarse.'
      : 'pero no se pudo confirmar el envío del correo.';
  return {
    tone: 'warning',
    title: `${subject}, ${problem}`,
    detail: `${invitation.email} · ${invitation.unit.label}. ${expiry} Puedes reenviarla.`,
    canResend: true
  };
}

export const DELIVERY_LABELS: Record<DeliveryStatus, string> = {
  SENT: 'Enviado',
  FAILED: 'No enviado',
  PENDING: 'Sin confirmar'
};

/** Page shown after an action: the invitation id (never personal data) goes in the URL. */
export function resultPath(invitationId: number, resent = false): string {
  return `/admin/residents/invite?invitation=${invitationId}${resent ? '&resent=1' : ''}`;
}

/** Reads ?invitation= safely: a positive integer or null. */
export function parseInvitationParam(value: string | null): number | null {
  if (value === null || !/^[1-9]\d{0,15}$/.test(value)) {
    return null;
  }
  return Number(value);
}
