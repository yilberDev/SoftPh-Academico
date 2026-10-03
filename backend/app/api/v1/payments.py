"""HU-004: manual payments (administrator only).

Every route requires an ADMINISTRADOR session (401 without a session, 403 for
other roles). POST also requires Origin (checked for the whole router) and the
CSRF token. The client never decides the apartment, the registration time,
the allocations, the status or the receipt number. Responses contain personal
and financial data: never cached.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.deps import DbDep, client_ip, require_csrf, require_roles, verify_origin
from app.models import UserRole
from app.schemas.payment import PaymentCreate, PaymentListQuery, PaymentPage, PaymentRegistered
from app.services import payments as payment_service
from app.services.mail import MailSender, get_mail_sender
from app.services.sessions import AuthContext
from app.services.structure import Actor

router = APIRouter(prefix="/admin/payments", tags=["payments"], dependencies=[Depends(verify_origin)])

# Order matters: 401 without a session, 403 for other roles, then 403 CSRF_FAILED.
AdminDep = Annotated[AuthContext, Depends(require_roles(UserRole.ADMINISTRADOR))]
CsrfDep = Annotated[AuthContext, Depends(require_csrf)]
MailerDep = Annotated[MailSender, Depends(get_mail_sender)]

NO_STORE = {"Cache-Control": "no-store"}


@router.post("", response_model=PaymentRegistered, status_code=status.HTTP_201_CREATED)
async def register_payment(
    data: PaymentCreate,
    request: Request,
    response: Response,
    auth: AdminDep,
    _csrf: CsrfDep,
    db: DbDep,
    mailer: MailerDep,
) -> PaymentRegistered:
    response.headers.update(NO_STORE)
    actor = Actor(user_id=auth.user.id, ip_address=client_ip(request))
    return await payment_service.register_payment(db, data, actor, mailer=mailer)


@router.get("", response_model=PaymentPage)
async def list_payments(
    _auth: AdminDep, query: Annotated[PaymentListQuery, Query()], db: DbDep, response: Response
) -> PaymentPage:
    response.headers.update(NO_STORE)
    return await payment_service.list_payments(db, query)
