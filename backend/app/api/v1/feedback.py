"""HU-005: feedback of residents, reviewed and managed by the administrator.

Every route requires an ADMINISTRADOR session (401 without a session, 403 for
other roles). The two changes have their own endpoints (no generic PUT) and
also require Origin (checked for the whole router) and the CSRF token. The
original message, the resident and the status are never set by the client.
Responses contain personal data: never cached.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from app.api.deps import DbDep, client_ip, require_csrf, require_roles, verify_origin
from app.models import UserRole
from app.schemas.feedback import (
    FeedbackActionResult,
    FeedbackDetail,
    FeedbackListQuery,
    FeedbackPage,
    FeedbackResolution,
    FeedbackResponse,
)
from app.schemas.invitation import ID_MAX
from app.services import feedback as feedback_service
from app.services.mail import MailSender, get_mail_sender
from app.services.sessions import AuthContext
from app.services.structure import Actor

router = APIRouter(prefix="/admin/feedback", tags=["feedback"], dependencies=[Depends(verify_origin)])

# Order matters: 401 without a session, 403 for other roles, then 403 CSRF_FAILED.
AdminDep = Annotated[AuthContext, Depends(require_roles(UserRole.ADMINISTRADOR))]
CsrfDep = Annotated[AuthContext, Depends(require_csrf)]
MailerDep = Annotated[MailSender, Depends(get_mail_sender)]
FeedbackId = Annotated[int, Path(ge=1, le=ID_MAX)]

NO_STORE = {"Cache-Control": "no-store"}


def _actor(auth: AuthContext, request: Request) -> Actor:
    return Actor(user_id=auth.user.id, ip_address=client_ip(request))


@router.get("", response_model=FeedbackPage)
async def list_feedback(
    _auth: AdminDep, query: Annotated[FeedbackListQuery, Query()], db: DbDep, response: Response
) -> FeedbackPage:
    response.headers.update(NO_STORE)
    return await feedback_service.list_feedback(db, query)


@router.get("/{feedback_id}", response_model=FeedbackDetail)
async def get_feedback(feedback_id: FeedbackId, _auth: AdminDep, db: DbDep, response: Response) -> FeedbackDetail:
    response.headers.update(NO_STORE)
    return await feedback_service.get_feedback(db, feedback_id)


@router.post("/{feedback_id}/response", response_model=FeedbackActionResult)
async def respond(
    feedback_id: FeedbackId, data: FeedbackResponse, request: Request, response: Response,
    auth: AdminDep, _csrf: CsrfDep, db: DbDep, mailer: MailerDep,
) -> FeedbackActionResult:
    response.headers.update(NO_STORE)
    return await feedback_service.respond(db, feedback_id, data, _actor(auth, request), mailer=mailer)


@router.post("/{feedback_id}/resolve", response_model=FeedbackActionResult)
async def resolve(
    feedback_id: FeedbackId, data: FeedbackResolution, request: Request, response: Response,
    auth: AdminDep, _csrf: CsrfDep, db: DbDep, mailer: MailerDep,
) -> FeedbackActionResult:
    response.headers.update(NO_STORE)
    return await feedback_service.resolve(db, feedback_id, data, _actor(auth, request), mailer=mailer)
