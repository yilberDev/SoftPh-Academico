"""HU-006: registered residents (administrator only, read-only).

Requires an ADMINISTRADOR session (401 without a session, 403 for other
roles). GET only: no CSRF token. The query string is validated against a
whitelist (unknown parameters are rejected). Personal data: never cached.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import DbDep, require_roles
from app.models import UserRole
from app.schemas.resident import ResidentListQuery, ResidentPage
from app.services import residents as resident_service
from app.services.sessions import AuthContext

router = APIRouter(prefix="/admin/residents", tags=["residents"])

AdminDep = Annotated[AuthContext, Depends(require_roles(UserRole.ADMINISTRADOR))]

NO_STORE = {"Cache-Control": "no-store"}


@router.get("", response_model=ResidentPage)
async def list_residents(
    _auth: AdminDep, query: Annotated[ResidentListQuery, Query()], db: DbDep, response: Response
) -> ResidentPage:
    response.headers.update(NO_STORE)
    return await resident_service.list_residents(db, query)
