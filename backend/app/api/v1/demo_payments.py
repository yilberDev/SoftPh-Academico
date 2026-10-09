"""HU-010: the signed-in resident's demo payment transactions (internal demo gateway, no real money).

Published only when DEMO_PAYMENTS_ENABLED is true (app.main.include_demo_payments; never in
production). Every route requires a RESIDENTE session (401 without a session, 403 for any other
role, decided before anything is read or validated). POST also requires Origin (checked for the
whole router) and the CSRF token. The resident is always the session's user (auth.user.id): no
path, query parameter or body field selects a resident, and unknown body fields are rejected.
Starting takes only the obligation and the amount; completing takes only the demo gateway's
outcome: the amount, the reference and the obligation always come from the stored transaction.
A transaction of another resident is 404 DEMO_TRANSACTION_NOT_FOUND, exactly like a missing one.

A thin layer over services/demo_payments.py, which owns the rules, the locks, the payment and its
single allocation, the idempotency and the audit. Reading may persist an expiry (INITIATED past
expires_at becomes EXPIRED). No email is sent and no receipt document exists yet. Financial data:
never cached, not even an error (NoStoreRoute).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status

from app.api.deps import DbDep, client_ip, require_csrf, require_roles, verify_origin
from app.api.v1.charges import NoStoreRoute
from app.models import UserRole
from app.schemas.demo_payment import (
    DemoPaymentComplete,
    DemoPaymentStart,
    DemoPaymentTransactionRead,
)
from app.schemas.invitation import ID_MAX
from app.services import demo_payments as demo_payment_service
from app.services.sessions import AuthContext

router = APIRouter(
    prefix="/resident/demo-payments", tags=["resident-demo-payments"], dependencies=[Depends(verify_origin)],
    route_class=NoStoreRoute,
)

# Order matters: 401 without a session, 403 for other roles, then 403 CSRF_FAILED.
ResidentDep = Annotated[AuthContext, Depends(require_roles(UserRole.RESIDENTE))]
CsrfDep = Annotated[AuthContext, Depends(require_csrf)]
TransactionId = Annotated[int, Path(ge=1, le=ID_MAX)]


@router.post("", response_model=DemoPaymentTransactionRead, status_code=status.HTTP_201_CREATED)
async def start_demo_payment(
    data: DemoPaymentStart, request: Request, auth: ResidentDep, _csrf: CsrfDep, db: DbDep
) -> DemoPaymentTransactionRead:
    started = await demo_payment_service.start_demo_payment(
        db, auth.user.id, data.charge_id, data.amount, ip_address=client_ip(request)  # the session's resident
    )
    return DemoPaymentTransactionRead.from_result(started)


@router.get("/{transaction_id}", response_model=DemoPaymentTransactionRead)
async def get_demo_payment(
    transaction_id: TransactionId, request: Request, auth: ResidentDep, db: DbDep
) -> DemoPaymentTransactionRead:
    # transaction_id AND the session's resident: another resident's transaction is "not found".
    return DemoPaymentTransactionRead.from_result(
        await demo_payment_service.get_demo_payment(db, auth.user.id, transaction_id, ip_address=client_ip(request))
    )


@router.post("/{transaction_id}/complete", response_model=DemoPaymentTransactionRead)
async def complete_demo_payment(
    transaction_id: TransactionId, data: DemoPaymentComplete, request: Request, auth: ResidentDep, _csrf: CsrfDep,
    db: DbDep,
) -> DemoPaymentTransactionRead:
    completed = await demo_payment_service.complete_demo_payment(
        db, auth.user.id, transaction_id, data.outcome, ip_address=client_ip(request)
    )
    return DemoPaymentTransactionRead.from_result(completed)


@router.post("/{transaction_id}/cancel", response_model=DemoPaymentTransactionRead)
async def cancel_demo_payment(
    transaction_id: TransactionId, request: Request, auth: ResidentDep, _csrf: CsrfDep, db: DbDep
) -> DemoPaymentTransactionRead:
    cancelled = await demo_payment_service.cancel_demo_payment(
        db, auth.user.id, transaction_id, ip_address=client_ip(request)
    )
    return DemoPaymentTransactionRead.from_result(cancelled)
