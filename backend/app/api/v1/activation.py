"""HU-019: account activation by the resident (public, no session).

Both routes are anonymous state-changing requests: they require a valid
Origin (checked for the whole router) and the one-time invitation token in
the JSON body (never in the URL). There is no session, so there is no CSRF
token. No session is created on activation (login is HU-007).
"""

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import DbDep, client_ip, verify_origin
from app.schemas.activation import ActivationResult, InvitationPreview, InvitationTokenRequest, ResidentActivation
from app.services import activation as activation_service

router = APIRouter(
    prefix="/resident-invitations", tags=["resident-activation"], dependencies=[Depends(verify_origin)]
)

NO_STORE = {"Cache-Control": "no-store"}


@router.post("/verify", response_model=InvitationPreview)
async def verify_invitation(data: InvitationTokenRequest, request: Request, response: Response, db: DbDep) -> InvitationPreview:
    response.headers.update(NO_STORE)
    return await activation_service.verify_invitation(db, data.token, ip_address=client_ip(request))


@router.post("/activate", response_model=ActivationResult)
async def activate_account(data: ResidentActivation, request: Request, response: Response, db: DbDep) -> ActivationResult:
    response.headers.update(NO_STORE)
    await activation_service.activate_account(db, data, ip_address=client_ip(request))
    return ActivationResult()
