import { apiFetch } from './client';

/** Resident invitations (HU-003). The token never reaches the frontend. */

export type InvitationStatus = 'PENDING' | 'USED' | 'EXPIRED';
export type DeliveryStatus = 'PENDING' | 'SENT' | 'FAILED';

/** An apartment that can be invited to. Send unit_id; label is only shown. */
export interface AvailableUnit {
  unit_id: number;
  label: string;
  block_number: number;
  tower_number: number;
  number: number;
}

export interface AvailableUnitList {
  items: AvailableUnit[];
}

export interface Invitation {
  id: number;
  email: string;
  full_name: string;
  unit: { id: number; label: string };
  status: InvitationStatus;
  delivery_status: DeliveryStatus;
  send_attempts: number;
  validity_days: number;
  /** ISO 8601, UTC. */
  created_at: string;
  /** ISO 8601, UTC. */
  expires_at: string;
}

export interface InvitationList {
  items: Invitation[];
}

export interface InvitationPayload {
  email: string;
  full_name: string;
  unit_id: number;
  validity_days: number;
}

/** Browser only. The backend decides availability, duplicates and sending. */
export function createInvitation(payload: InvitationPayload, csrfToken: string): Promise<Invitation> {
  return apiFetch<Invitation>('/resident-invitations', {
    method: 'POST',
    body: JSON.stringify(payload),
    csrfToken
  });
}

/** Browser only. New link (the previous one stops working); the expiry does not change. */
export function resendInvitation(invitationId: number, csrfToken: string): Promise<Invitation> {
  return apiFetch<Invitation>(`/resident-invitations/${encodeURIComponent(invitationId)}/resend`, {
    method: 'POST',
    csrfToken
  });
}
