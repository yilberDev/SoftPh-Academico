"""HU-016: password reset of residents (public, no session).

Anonymous state-changing requests: valid Origin (checked for the whole router)
and, after the request step, the one-time token in the JSON body (never in the
URL). No session, so no CSRF token; no session is created either.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status

from app.api.deps import DbDep, SettingsDep, client_ip, verify_origin
from app.db.session import SessionFactory, get_session_factory
from app.schemas.password_reset import (
    PasswordResetAccepted,
    PasswordResetCompleted,
    PasswordResetConfirm,
    PasswordResetPreview,
    PasswordResetRequest,
    PasswordResetTokenRequest,
)
from app.services import password_reset as reset_service
from app.services.mail import MailSender, get_mail_sender

router = APIRouter(prefix="/auth/password-reset", tags=["password-reset"], dependencies=[Depends(verify_origin)])

NO_STORE = {"Cache-Control": "no-store"}
MailerDep = Annotated[MailSender, Depends(get_mail_sender)]
SessionFactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]


@router.post("/request", response_model=PasswordResetAccepted, status_code=status.HTTP_202_ACCEPTED)
async def request_reset(
    data: PasswordResetRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: DbDep,
    settings: SettingsDep,
    mailer: MailerDep,
    session_factory: SessionFactoryDep,
) -> PasswordResetAccepted:
    # The same answer (status, body, headers) for every email; the email is
    # sent after the response, so the response time does not depend on SMTP.
    response.headers.update(NO_STORE)
    delivery = await reset_service.request_reset(db, data.email, ip_address=client_ip(request))
    if delivery is not None:
        background.add_task(
            reset_service.deliver_reset_email,
            delivery,
            mailer=mailer,
            app_origin=settings.app_origin,
            session_factory=session_factory,
        )
    return PasswordResetAccepted()


@router.post("/verify", response_model=PasswordResetPreview)
async def verify_reset(data: PasswordResetTokenRequest, response: Response, db: DbDep) -> PasswordResetPreview:
    response.headers.update(NO_STORE)
    return await reset_service.verify_reset(db, data.token)


@router.post("/confirm", response_model=PasswordResetCompleted)
async def confirm_reset(
    data: PasswordResetConfirm, request: Request, response: Response, db: DbDep
) -> PasswordResetCompleted:
    response.headers.update(NO_STORE)
    await reset_service.confirm_reset(db, data, ip_address=client_ip(request))
    return PasswordResetCompleted()
