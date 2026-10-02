import { apiFetch } from './client';

/**
 * Password reset of residents (HU-016). Public: no session and no CSRF token;
 * the browser sends Origin. The token only travels in POST bodies.
 */

export interface ResetPreview {
  /** ISO 8601, UTC. */
  expires_at: string;
}

export interface ResetConfirmPayload {
  token: string;
  password: string;
  password_confirmation: string;
}

/** Always answers the same (202), whether or not the email belongs to a resident. */
export function requestPasswordReset(email: string): Promise<{ status: 'ACCEPTED' }> {
  return apiFetch<{ status: 'ACCEPTED' }>('/auth/password-reset/request', {
    method: 'POST',
    body: JSON.stringify({ email })
  });
}

export function verifyPasswordReset(token: string): Promise<ResetPreview> {
  return apiFetch<ResetPreview>('/auth/password-reset/verify', { method: 'POST', body: JSON.stringify({ token }) });
}

export function confirmPasswordReset(payload: ResetConfirmPayload): Promise<{ status: 'PASSWORD_UPDATED' }> {
  return apiFetch<{ status: 'PASSWORD_UPDATED' }>('/auth/password-reset/confirm', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}
