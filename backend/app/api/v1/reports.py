"""HU-012: financial reports (administrator only, read only).

    GET /admin/reports/payments?date_from&date_to     the payments registered in a range of payment
        dates (both required: 422 next to the missing one, CU-012 5a/6a).
    GET /admin/reports/pending-balances?scope=ALL|OVERDUE   the apartments that owe, with their
        current resident (HU-011 criterion 3.2: who has pending obligations).

The statement of one resident (criterion 4) is GET /admin/residents/{id}/account-statement (HU-011).
401 without a session, 403 for a resident, decided before anything is read. GET only: no CSRF token.
Unknown query parameters: 422. Financial and personal data: never cached, not even an error.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbDep, require_roles
from app.api.v1.charges import NoStoreRoute
from app.models import UserRole
from app.schemas.report import (
    PaymentsReportQuery,
    PaymentsReportRead,
    PendingBalancesQuery,
    PendingBalancesReportRead,
    PendingScope,
)
from app.services import financial_reports as report_service
from app.services.sessions import AuthContext

router = APIRouter(prefix="/admin/reports", tags=["reports"], route_class=NoStoreRoute)

AdminDep = Annotated[AuthContext, Depends(require_roles(UserRole.ADMINISTRADOR))]


@router.get("/payments", response_model=PaymentsReportRead)
async def payments_report(
    _auth: AdminDep, query: Annotated[PaymentsReportQuery, Query()], db: DbDep
) -> PaymentsReportRead:
    report = await report_service.payments_report(db, *query.dates())
    return PaymentsReportRead.from_report(report)


@router.get("/pending-balances", response_model=PendingBalancesReportRead)
async def pending_balances_report(
    _auth: AdminDep, query: Annotated[PendingBalancesQuery, Query()], db: DbDep
) -> PendingBalancesReportRead:
    report = await report_service.pending_balances_report(db, only_overdue=query.scope == PendingScope.OVERDUE)
    return PendingBalancesReportRead.from_report(report)
