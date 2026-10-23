"""HU-011 / HU-014: the account statement of a registered resident's current apartment (read only).

    GET /admin/residents/{resident_id}/account-statement   ADMINISTRADOR (HU-011): any registered
        resident; one that is not (missing, another role, deleted, never activated, without a current
        residence) is 404 RESIDENT_NOT_FOUND, as in HU-015.
    GET /resident/account-statement                        RESIDENTE (HU-014): always the session's
        own resident (auth.user.id); no path, query parameter, header or body selects anyone. Without
        a current apartment: 404 ACCOUNT_STATEMENT_UNAVAILABLE.

401 without a session and 403 for the other role, decided before anything is read. GET only: no
CSRF token. Neither takes a query parameter (unknown ones: 422). Financial and personal data:
never cached, not even an error (NoStoreRoute). A thin layer over services/account_statement.py.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import DbDep, require_roles
from app.api.v1.charges import NoStoreRoute
from app.core.exceptions import AccountStatementUnavailableError, ResidentNotFoundError
from app.models import UserRole
from app.schemas.account_statement import AccountStatementQuery, AccountStatementRead
from app.schemas.invitation import ID_MAX
from app.services import account_statement as statement_service
from app.services.sessions import AuthContext

admin_router = APIRouter(prefix="/admin/residents", tags=["account-statements"], route_class=NoStoreRoute)
resident_router = APIRouter(prefix="/resident", tags=["account-statements"], route_class=NoStoreRoute)

AdminDep = Annotated[AuthContext, Depends(require_roles(UserRole.ADMINISTRADOR))]
ResidentDep = Annotated[AuthContext, Depends(require_roles(UserRole.RESIDENTE))]
ResidentId = Annotated[int, Path(ge=1, le=ID_MAX)]


@admin_router.get("/{resident_id}/account-statement", response_model=AccountStatementRead)
async def get_resident_account_statement(
    resident_id: ResidentId, _auth: AdminDep, _query: Annotated[AccountStatementQuery, Query()], db: DbDep
) -> AccountStatementRead:
    statement = await statement_service.build_account_statement(db, resident_id, all_payers=True)
    return AccountStatementRead.from_statement(statement)


@resident_router.get("/account-statement", response_model=AccountStatementRead)
async def get_my_account_statement(
    auth: ResidentDep, _query: Annotated[AccountStatementQuery, Query()], db: DbDep
) -> AccountStatementRead:
    try:
        # The session's resident, never a value from the client; only their own payments.
        statement = await statement_service.build_account_statement(db, auth.user.id, all_payers=False)
    except ResidentNotFoundError:
        raise AccountStatementUnavailableError() from None
    return AccountStatementRead.from_statement(statement)
