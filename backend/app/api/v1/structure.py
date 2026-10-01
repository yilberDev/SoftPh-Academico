"""HU-013: physical structure Bloque → Torre → Apartamento (administrator only).

Collections are nested under their parent; items have flat routes because ids
are unique across the database. Every route requires an ADMINISTRADOR session;
state-changing routes also require Origin + CSRF token. Origin is checked for
every unsafe method of this router before anything else.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.api.deps import DbDep, client_ip, require_csrf, require_roles, verify_origin
from app.models import UserRole
from app.schemas.structure import (
    NUMBER_TECHNICAL_MAX,
    BlockList,
    BlockRead,
    BlockUpdate,
    NumberCreate,
    TowerList,
    TowerRead,
    TowerUpdate,
    UnitList,
    UnitRead,
    UnitUpdate,
)
from app.services import structure as structure_service
from app.services.sessions import AuthContext
from app.services.structure import Actor

router = APIRouter(tags=["structure"], dependencies=[Depends(verify_origin)])

# Order matters: 401 without a session, 403 for other roles, then 403 CSRF_FAILED.
AdminDep = Annotated[AuthContext, Depends(require_roles(UserRole.ADMINISTRADOR))]
CsrfDep = Annotated[AuthContext, Depends(require_csrf)]

# BIGINT ids; out-of-range values are rejected before reaching MySQL.
_ID_MAX = 2**63 - 1
BlockId = Annotated[int, Path(ge=1, le=_ID_MAX)]
TowerId = Annotated[int, Path(ge=1, le=_ID_MAX)]
UnitId = Annotated[int, Path(ge=1, le=_ID_MAX)]
# Optimistic locking for DELETE (no body): the version the client last read.
VersionQuery = Annotated[int, Query(ge=1, le=NUMBER_TECHNICAL_MAX)]


def _actor(auth: AuthContext, request: Request) -> Actor:
    return Actor(user_id=auth.user.id, ip_address=client_ip(request))


def _no_content() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Blocks ------------------------------------------------------------------


@router.get("/blocks", response_model=BlockList)
async def list_blocks(_auth: AdminDep, db: DbDep) -> BlockList:
    return await structure_service.list_blocks(db)


@router.post("/blocks", response_model=BlockRead, status_code=status.HTTP_201_CREATED)
async def create_block(
    data: NumberCreate, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep
) -> BlockRead:
    return await structure_service.create_block(db, data.number, _actor(auth, request))


@router.get("/blocks/{block_id}", response_model=BlockRead)
async def get_block(block_id: BlockId, _auth: AdminDep, db: DbDep) -> BlockRead:
    return await structure_service.get_block(db, block_id)


@router.patch("/blocks/{block_id}", response_model=BlockRead)
async def update_block(
    block_id: BlockId, data: BlockUpdate, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep
) -> BlockRead:
    return await structure_service.update_block(db, block_id, data.number, data.version, _actor(auth, request))


@router.delete("/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_block(
    block_id: BlockId, version: VersionQuery, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep
) -> Response:
    await structure_service.delete_block(db, block_id, version, _actor(auth, request))
    return _no_content()


# --- Towers ------------------------------------------------------------------


@router.get("/blocks/{block_id}/towers", response_model=TowerList)
async def list_towers(block_id: BlockId, _auth: AdminDep, db: DbDep) -> TowerList:
    return await structure_service.list_towers(db, block_id)


@router.post("/blocks/{block_id}/towers", response_model=TowerRead, status_code=status.HTTP_201_CREATED)
async def create_tower(
    block_id: BlockId, data: NumberCreate, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep
) -> TowerRead:
    return await structure_service.create_tower(db, block_id, data.number, _actor(auth, request))


@router.get("/towers/{tower_id}", response_model=TowerRead)
async def get_tower(tower_id: TowerId, _auth: AdminDep, db: DbDep) -> TowerRead:
    return await structure_service.get_tower(db, tower_id)


@router.patch("/towers/{tower_id}", response_model=TowerRead)
async def update_tower(
    tower_id: TowerId, data: TowerUpdate, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep
) -> TowerRead:
    return await structure_service.update_tower(db, tower_id, data.number, data.version, _actor(auth, request))


@router.delete("/towers/{tower_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tower(
    tower_id: TowerId, version: VersionQuery, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep
) -> Response:
    await structure_service.delete_tower(db, tower_id, version, _actor(auth, request))
    return _no_content()


# --- Units (apartments) -------------------------------------------------------


@router.get("/towers/{tower_id}/units", response_model=UnitList)
async def list_units(tower_id: TowerId, _auth: AdminDep, db: DbDep) -> UnitList:
    return await structure_service.list_units(db, tower_id)


@router.post("/towers/{tower_id}/units", response_model=UnitRead, status_code=status.HTTP_201_CREATED)
async def create_unit(
    tower_id: TowerId, data: NumberCreate, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep
) -> UnitRead:
    return await structure_service.create_unit(db, tower_id, data.number, _actor(auth, request))


@router.get("/units/{unit_id}", response_model=UnitRead)
async def get_unit(unit_id: UnitId, _auth: AdminDep, db: DbDep) -> UnitRead:
    return await structure_service.get_unit(db, unit_id)


@router.patch("/units/{unit_id}", response_model=UnitRead)
async def update_unit(
    unit_id: UnitId, data: UnitUpdate, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep
) -> UnitRead:
    return await structure_service.update_unit(db, unit_id, data.number, data.version, _actor(auth, request))


@router.delete("/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(
    unit_id: UnitId, version: VersionQuery, request: Request, auth: AdminDep, _csrf: CsrfDep, db: DbDep
) -> Response:
    await structure_service.delete_unit(db, unit_id, version, _actor(auth, request))
    return _no_content()
