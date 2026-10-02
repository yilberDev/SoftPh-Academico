import type { AvailableUnitList, InvitationList } from './invitations';
import { serverApiGet } from './server';

/** Server-side only (SSR): read-only calls for the invitation page (HU-003). */

export function listAvailableUnits(request: Request): Promise<AvailableUnitList> {
  return serverApiGet<AvailableUnitList>('/units/available', request);
}

export function listPendingInvitations(request: Request): Promise<InvitationList> {
  return serverApiGet<InvitationList>('/resident-invitations?status=PENDING', request);
}
