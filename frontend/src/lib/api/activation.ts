import { apiFetch } from './client';

/**
 * Account activation by the resident (HU-019). Public: no session and no CSRF
 * token; the browser sends Origin and the one-time token goes only in the body.
 */

export interface InvitationPreview {
  email: string;
  full_name: string;
  unit_code: string;
  /** ISO 8601, UTC. */
  expires_at: string;
}

export interface ActivationPayload {
  token: string;
  document_number: string;
  phone: string | null;
  password: string;
  password_confirmation: string;
}

export function verifyInvitation(token: string): Promise<InvitationPreview> {
  return apiFetch<InvitationPreview>('/resident-invitations/verify', {
    method: 'POST',
    body: JSON.stringify({ token })
  });
}

export function activateAccount(payload: ActivationPayload): Promise<{ status: 'ACTIVATED' }> {
  return apiFetch<{ status: 'ACTIVATED' }>('/resident-invitations/activate', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}
