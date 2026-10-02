"""HU-003: resident invitations (administrator only).

Every route requires an ADMINISTRADOR session; state-changing routes also
require Origin + CSRF token (Origin is checked for the whole router first).
Responses contain personal data and are never cached.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.api.deps import DbDep, SettingsDep, client_ip, require_csrf, require_roles, verify_origin
from app.models import UserRole
from app.schemas.invitation import ID_MAX, AvailableUnitList, InvitationCreate, InvitationList, InvitationRead
from app.services import invitations as invitation_service
from app.services.mail import MailSender, get_mail_sender
from app.services.sessions import AuthContext
from app.services.structure import Actor

router = APIRouter(tags=["resident-invitations"], dependencies=[Depends(verify_origin)])

# Order matters: 401 without a session, 403 for other roles, then 403 CSRF_FAILED.
AdminDep = Annotated[AuthContext, Depends(require_roles(UserRole.ADMINISTRADOR))]
CsrfDep = Annotated[AuthContext, Depends(require_csrf)]
MailerDep = Annotated[MailSender, Depends(get_mail_sender)]
InvitationId = Annotated[int, Path(ge=1, le=ID_MAX)]

NO_STORE = {"Cache-Control": "no-store"}


def _actor(auth: AuthContext, request: Request) -> Actor:
    return Actor(user_id=auth.user.id, ip_address=client_ip(request))


@router.get("/units/available", response_model=AvailableUnitList)
async def list_available_units(_auth: AdminDep, db: DbDep, response: Response) -> AvailableUnitList:
    response.headers.update(NO_STORE)
    return await invitation_service.list_available_units(db)


@router.get("/resident-invitations", response_model=InvitationList)
async def list_invitations(
    status_filter: Annotated[Literal["PENDING"], Query(alias="status")],
    _auth: AdminDep,
    db: DbDep,
    response: Response,
) -> InvitationList:
    # Only valid pending invitations are listed (E-1b: recover failed deliveries).
    response.headers.update(NO_STORE)
    return await invitation_service.list_pending_invitations(db)


@router.post("/resident-invitations", response_model=InvitationRead, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    data: InvitationCreate,
    request: Request,
    response: Response,
    auth: AdminDep,
    _csrf: CsrfDep,
    db: DbDep,
    settings: SettingsDep,
    mailer: MailerDep,
) -> InvitationRead:
    response.headers.update(NO_STORE)
    actor = _actor(auth, request)
    return await invitation_service.create_invitation(
        db, data, actor, mailer=mailer, app_origin=settings.app_origin
    )


@router.post("/resident-invitations/{invitation_id}/resend", response_model=InvitationRead)
async def resend_invitation(
    invitation_id: InvitationId,
    request: Request,
    response: Response,
    auth: AdminDep,
    _csrf: CsrfDep,
    db: DbDep,
    settings: SettingsDep,
    mailer: MailerDep,
) -> InvitationRead:
    response.headers.update(NO_STORE)
    actor = _actor(auth, request)
    return await invitation_service.resend_invitation(
        db, invitation_id, actor, mailer=mailer, app_origin=settings.app_origin
    )
