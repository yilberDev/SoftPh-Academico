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

HU-010: GET /{payment_id}/receipt downloads the PDF receipt of the resident's
approved demo payment (DEMO_GATEWAY) only: a manual payment, another
resident's and a missing one are the same 404. It is composed from the
persisted payment, its demo transaction and its allocation (checked against
each other; inconsistent records are a generic 500 and no PDF), off the event
loop. A read: no audit, no email, receipt_email_status unchanged. Published
whatever DEMO_PAYMENTS_ENABLED says (that flag only governs the simulator).
"""

from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.deps import DbDep, require_roles
from app.api.v1.charges import NoStoreRoute
from app.models import UserRole
from app.schemas.invitation import ID_MAX
from app.schemas.resident_payment import (
    ResidentPaymentDetailRead,
    ResidentPaymentListQuery,
    ResidentPaymentPage,
    ResidentReceiptQuery,
)
from app.services import resident_payments as resident_payment_service
from app.services.receipts import build_demo_receipt_pdf, demo_receipt_filename
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


@router.get(
    "/{payment_id}/receipt",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "Comprobante de pago (demostración), PDF."}},
)
async def download_my_demo_receipt(
    payment_id: PaymentId, auth: ResidentDep, _query: Annotated[ResidentReceiptQuery, Query()], db: DbDep
) -> Response:
    # payment_id AND the session's resident AND a demo payment: anything else is "not found".
    data = await resident_payment_service.get_demo_receipt(db, auth.user.id, payment_id)
    pdf = await anyio.to_thread.run_sync(build_demo_receipt_pdf, data)  # reportlab is synchronous
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            # The receipt number is derived from the payment id (RC-00000012): nothing from the client.
            "Content-Disposition": f'attachment; filename="{demo_receipt_filename(data.receipt_number)}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
