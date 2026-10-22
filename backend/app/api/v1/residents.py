"""Registered residents (administrator only): the list (HU-006) and one resident's detail,
update and logical deletion (HU-015).

Requires an ADMINISTRADOR session (401 without a session, 403 for other
roles). Reads need no CSRF token; PATCH and DELETE also require Origin
(checked for every unsafe method of this router) and the CSRF token. The query
string is validated against a whitelist (unknown parameters are rejected).
Personal data: never cached.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.api.deps import DbDep, client_ip, require_csrf, require_roles, verify_origin
from app.models import UserRole
from app.schemas.invitation import ID_MAX
from app.schemas.resident import (
    ResidentDetail,
    ResidentListQuery,
    ResidentPage,
    ResidentUpdate,
    utc_naive,
)
from app.services import residents as resident_service
from app.services.sessions import AuthContext
from app.services.structure import Actor

router = APIRouter(prefix="/admin/residents", tags=["residents"], dependencies=[Depends(verify_origin)])

AdminDep = Annotated[AuthContext, Depends(require_roles(UserRole.ADMINISTRADOR))]
CsrfDep = Annotated[AuthContext, Depends(require_csrf)]
ResidentId = Annotated[int, Path(ge=1, le=ID_MAX)]
# Optimistic locking for DELETE (no body): the updated_at the client last read.
UpdatedAtQuery = Annotated[datetime, Query()]

NO_STORE = {"Cache-Control": "no-store"}


def _actor(auth: AuthContext, request: Request) -> Actor:
    return Actor(user_id=auth.user.id, ip_address=client_ip(request))


@router.get("", response_model=ResidentPage)
async def list_residents(
    _auth: AdminDep, query: Annotated[ResidentListQuery, Query()], db: DbDep, response: Response
) -> ResidentPage:
    response.headers.update(NO_STORE)
    return await resident_service.list_residents(db, query)


@router.get("/{resident_id}", response_model=ResidentDetail)
async def get_resident(resident_id: ResidentId, _auth: AdminDep, db: DbDep, response: Response) -> ResidentDetail:
    response.headers.update(NO_STORE)
    return await resident_service.get_resident(db, resident_id)


@router.patch("/{resident_id}", response_model=ResidentDetail)
async def update_resident(
    resident_id: ResidentId, data: ResidentUpdate, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep,
    response: Response,
) -> ResidentDetail:
    response.headers.update(NO_STORE)
    return await resident_service.update_resident(db, resident_id, data, _actor(auth, request))


@router.delete("/{resident_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resident(
    resident_id: ResidentId, updated_at: UpdatedAtQuery, request: Request, auth: AdminDep, _csrf: CsrfDep,
    db: DbDep,
) -> Response:
    await resident_service.delete_resident(db, resident_id, utc_naive(updated_at), _actor(auth, request))
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
