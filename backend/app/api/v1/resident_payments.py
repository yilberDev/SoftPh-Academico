"""HU-008: the signed-in resident's own payments (read only).

Both routes require a RESIDENTE session (401 without a session, 403 for any
other role, decided before anything is read or validated). GET only: no CSRF
token. The resident is always the session's user (auth.user.id): no path,
query parameter, header or body selects a resident, and unknown query
parameters (resident_id, user_id, ...) are rejected. A payment of another
resident is 404 PAYMENT_NOT_FOUND, exactly like a missing one. A thin layer
over services/resident_payments.py, which owns the ownership, the filters, the
search and the derived status. Financial data: never cached, not even an
error (NoStoreRoute).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import DbDep, require_roles
from app.api.v1.charges import NoStoreRoute
from app.models import UserRole
from app.schemas.invitation import ID_MAX
from app.schemas.resident_payment import ResidentPaymentDetailRead, ResidentPaymentListQuery, ResidentPaymentPage
from app.services import resident_payments as resident_payment_service
from app.services.sessions import AuthContext

router = APIRouter(prefix="/resident/payments", tags=["resident-payments"], route_class=NoStoreRoute)

ResidentDep = Annotated[AuthContext, Depends(require_roles(UserRole.RESIDENTE))]
PaymentId = Annotated[int, Path(ge=1, le=ID_MAX)]


@router.get("", response_model=ResidentPaymentPage)
async def list_my_payments(
    auth: ResidentDep, query: Annotated[ResidentPaymentListQuery, Query()], db: DbDep
) -> ResidentPaymentPage:
    page = await resident_payment_service.list_resident_payments(
        db,
        auth.user.id,  # the session's resident, never a value from the client
        resident_payment_service.ResidentPaymentQuery(
            page=query.page, page_size=query.page_size, status=query.status, date_from=query.date_from,
            date_to=query.date_to, search=query.search,
        ),
    )
    return ResidentPaymentPage.from_page(page)


@router.get("/{payment_id}", response_model=ResidentPaymentDetailRead)
async def get_my_payment(payment_id: PaymentId, auth: ResidentDep, db: DbDep) -> ResidentPaymentDetailRead:
    # payment_id AND the session's resident: another resident's payment is "not found".
    return ResidentPaymentDetailRead.from_detail(
        await resident_payment_service.get_resident_payment(db, auth.user.id, payment_id)
    )
