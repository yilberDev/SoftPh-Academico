"""Physical structure (HU-013): Bloque → Torre → Apartamento.

Guarantees, with the database as the final authority:

- One active row per number in its context: UNIQUE indexes on generated
  columns. The checks here only produce clear messages; a concurrent insert or
  renumbering that passes them still fails on the UNIQUE index and gets the
  same 409.
- No active child under a deleted parent: every write locks the path from the
  top (block → tower → unit) with locking reads, which always see the latest
  committed data. A creation holds FOR SHARE on its parent; a deletion takes
  FOR UPDATE on the row and then looks for active children with FOR SHARE.
  Both need the same parent row, so they run one after the other.
- Concurrent edits: optimistic locking with `version`, compared under FOR UPDATE.

Lock order is always block → tower → unit (HU-003 continues with the unit),
and no transaction upgrades a FOR SHARE lock to FOR UPDATE on the same row.
"""

from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import Row, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.exceptions import (
    AppError,
    BlockHasActiveTowersError,
    BlockNotFoundError,
    BlockNumberExistsError,
    TowerHasActiveUnitsError,
    TowerNotFoundError,
    TowerNumberExistsError,
    UnitInUseError,
    UnitNotFoundError,
    UnitNumberExistsError,
    VersionConflictError,
)
from app.core.unit_labels import (
    BLOCK_LABEL_PREFIX,
    block_label,
    tower_label,
    tower_label_prefix,
    tower_name,
    unit_label,
    unit_label_prefix,
    unit_name,
)
from app.models import AuditAction, Block, InvitationStatus, ResidentInvitation, ResidentUnit, Tower, Unit
from app.schemas.structure import (
    BlockList,
    BlockRead,
    BlockRef,
    TowerList,
    TowerRead,
    TowerRef,
    UnitList,
    UnitRead,
)
from app.services.audit import record_audit

Entity = TypeVar("Entity", Block, Tower, Unit)


@dataclass(frozen=True)
class Actor:
    """Administrator performing the change, for the audit log."""

    user_id: int
    ip_address: str | None


# --- Reads -------------------------------------------------------------------

_active_towers_of_block = (
    select(func.count(Tower.id))
    .where(Tower.block_id == Block.id, Tower.deleted_at.is_(None))
    .correlate(Block)
    .scalar_subquery()
)
_active_units_of_block = (
    select(func.count(Unit.id))
    .join(Tower, Tower.id == Unit.tower_id)
    .where(Tower.block_id == Block.id, Unit.deleted_at.is_(None))
    .correlate(Block)
    .scalar_subquery()
)
_active_units_of_tower = (
    select(func.count(Unit.id))
    .where(Unit.tower_id == Tower.id, Unit.deleted_at.is_(None))
    .correlate(Tower)
    .scalar_subquery()
)

_BLOCKS = select(
    Block.id,
    Block.number,
    Block.version,
    _active_towers_of_block.label("active_towers"),
    _active_units_of_block.label("active_units"),
).where(Block.deleted_at.is_(None))

# An active tower always has an active block, and an active unit an active
# tower (see the module docstring), so only the row itself is filtered.
_TOWERS = (
    select(
        Tower.id,
        Tower.block_id,
        Tower.number,
        Tower.version,
        Block.number.label("block_number"),
        _active_units_of_tower.label("active_units"),
    )
    .join(Block, Block.id == Tower.block_id)
    .where(Tower.deleted_at.is_(None))
)

_UNITS = (
    select(
        Unit.id,
        Unit.tower_id,
        Unit.number,
        Unit.version,
        Tower.block_id,
        Tower.number.label("tower_number"),
        Block.number.label("block_number"),
    )
    .join(Tower, Tower.id == Unit.tower_id)
    .join(Block, Block.id == Tower.block_id)
    .where(Unit.deleted_at.is_(None))
)


def _block_ref(block_id: int, block_number: int) -> BlockRef:
    label = block_label(block_number)
    return BlockRef(id=block_id, number=block_number, name=label, label=label)


def _block_read(row: Row[Any]) -> BlockRead:
    return BlockRead(
        id=row.id,
        number=row.number,
        name=block_label(row.number),
        label=block_label(row.number),
        version=row.version,
        active_towers=row.active_towers,
        active_units=row.active_units,
    )


def _tower_read(row: Row[Any]) -> TowerRead:
    return TowerRead(
        id=row.id,
        block_id=row.block_id,
        number=row.number,
        name=tower_name(row.number),
        label=tower_label(row.block_number, row.number),
        version=row.version,
        active_units=row.active_units,
        block=_block_ref(row.block_id, row.block_number),
    )


def _unit_read(row: Row[Any]) -> UnitRead:
    return UnitRead(
        id=row.id,
        block_id=row.block_id,
        tower_id=row.tower_id,
        number=row.number,
        name=unit_name(row.number),
        label=unit_label(row.block_number, row.tower_number, row.number),
        version=row.version,
        block=_block_ref(row.block_id, row.block_number),
        tower=TowerRef(
            id=row.tower_id,
            number=row.tower_number,
            name=tower_name(row.tower_number),
            label=tower_label(row.block_number, row.tower_number),
        ),
    )


async def list_blocks(db: AsyncSession) -> BlockList:
    rows = (await db.execute(_BLOCKS.order_by(Block.number))).all()
    return BlockList(items=[_block_read(row) for row in rows], child_label_prefix=BLOCK_LABEL_PREFIX)


async def get_block(db: AsyncSession, block_id: int) -> BlockRead:
    row = (await db.execute(_BLOCKS.where(Block.id == block_id))).first()
    if row is None:
        raise BlockNotFoundError()
    return _block_read(row)


async def list_towers(db: AsyncSession, block_id: int) -> TowerList:
    block = await get_block(db, block_id)
    rows = (await db.execute(_TOWERS.where(Tower.block_id == block_id).order_by(Tower.number))).all()
    return TowerList(
        block=block,
        items=[_tower_read(row) for row in rows],
        child_label_prefix=tower_label_prefix(block.number),
    )


async def get_tower(db: AsyncSession, tower_id: int) -> TowerRead:
    row = (await db.execute(_TOWERS.where(Tower.id == tower_id))).first()
    if row is None:
        raise TowerNotFoundError()
    return _tower_read(row)


async def list_units(db: AsyncSession, tower_id: int) -> UnitList:
    tower = await get_tower(db, tower_id)
    rows = (await db.execute(_UNITS.where(Unit.tower_id == tower_id).order_by(Unit.number))).all()
    return UnitList(
        block=tower.block,
        tower=tower,
        items=[_unit_read(row) for row in rows],
        child_label_prefix=unit_label_prefix(tower.block.number, tower.number),
    )


async def get_unit(db: AsyncSession, unit_id: int) -> UnitRead:
    row = (await db.execute(_UNITS.where(Unit.id == unit_id))).first()
    if row is None:
        raise UnitNotFoundError()
    return _unit_read(row)


# --- Locking -----------------------------------------------------------------


async def _lock_active(db: AsyncSession, model: type[Entity], entity_id: int, *, share: bool) -> Entity | None:
    """Locking read of an active row (FOR SHARE or FOR UPDATE), latest committed data."""
    return await db.scalar(
        select(model)
        .where(model.id == entity_id, model.deleted_at.is_(None))
        .with_for_update(read=share)
        .execution_options(populate_existing=True)
    )


async def _lock_block(db: AsyncSession, block_id: int, *, share: bool) -> Block:
    block = await _lock_active(db, Block, block_id, share=share)
    if block is None:
        raise BlockNotFoundError()
    return block


async def _lock_tower_path(db: AsyncSession, tower_id: int, *, share: bool) -> tuple[Block, Tower]:
    """Locks block (FOR SHARE) → tower (FOR SHARE or FOR UPDATE)."""
    # block_id never changes, so reading it without a lock is safe.
    block_id = await db.scalar(select(Tower.block_id).where(Tower.id == tower_id))
    if block_id is None:
        raise TowerNotFoundError()
    block = await _lock_active(db, Block, block_id, share=True)
    tower = await _lock_active(db, Tower, tower_id, share=share)
    if block is None or tower is None:
        raise TowerNotFoundError()
    return block, tower


async def _lock_unit_path(db: AsyncSession, unit_id: int) -> tuple[Block, Tower, Unit]:
    """Locks block (FOR SHARE) → tower (FOR SHARE) → unit (FOR UPDATE)."""
    # tower_id and block_id never change, so reading them without a lock is safe.
    parents = (
        await db.execute(
            select(Unit.tower_id, Tower.block_id).join(Tower, Tower.id == Unit.tower_id).where(Unit.id == unit_id)
        )
    ).first()
    if parents is None:
        raise UnitNotFoundError()
    block = await _lock_active(db, Block, parents.block_id, share=True)
    tower = await _lock_active(db, Tower, parents.tower_id, share=True)
    unit = await _lock_active(db, Unit, unit_id, share=False)
    if block is None or tower is None or unit is None:
        raise UnitNotFoundError()
    return block, tower, unit


async def _has_active_children(db: AsyncSession, model: type[Tower] | type[Unit], parent_filter: Any) -> bool:
    # Locking read: sees children committed after this transaction started.
    found = await db.scalar(
        select(model.id).where(parent_filter, model.deleted_at.is_(None)).limit(1).with_for_update(read=True)
    )
    return found is not None


def _check_version(entity: Block | Tower | Unit, version: int) -> None:
    if entity.version != version:
        raise VersionConflictError()


async def _flush_or_conflict(db: AsyncSession, constraint: str, error: AppError) -> None:
    """Flushes; a concurrent winner on the UNIQUE index becomes the same 409."""
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if constraint in str(exc.orig):
            raise error from exc
        raise


# --- Duplicate checks (clear messages only; UNIQUE is the guarantee) ----------


async def _block_number_taken(db: AsyncSession, number: int, *, exclude_id: int | None = None) -> bool:
    query = exists().where(Block.number == number, Block.deleted_at.is_(None))
    if exclude_id is not None:
        query = query.where(Block.id != exclude_id)
    return bool(await db.scalar(select(query)))


async def _tower_number_taken(db: AsyncSession, block_id: int, number: int, *, exclude_id: int | None = None) -> bool:
    query = exists().where(Tower.block_id == block_id, Tower.number == number, Tower.deleted_at.is_(None))
    if exclude_id is not None:
        query = query.where(Tower.id != exclude_id)
    return bool(await db.scalar(select(query)))


async def _unit_number_taken(db: AsyncSession, tower_id: int, number: int, *, exclude_id: int | None = None) -> bool:
    query = exists().where(Unit.tower_id == tower_id, Unit.number == number, Unit.deleted_at.is_(None))
    if exclude_id is not None:
        query = query.where(Unit.id != exclude_id)
    return bool(await db.scalar(select(query)))


def _audit(
    db: AsyncSession, actor: Actor, action: AuditAction, entity_type: str, entity_id: int, context: dict[str, Any]
) -> None:
    record_audit(
        db,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor.user_id,
        context=context,
        ip_address=actor.ip_address,
    )


def _changes(previous_number: int, number: int, previous_label: str, label: str) -> dict[str, Any]:
    return {"number": {"from": previous_number, "to": number}, "label": {"from": previous_label, "to": label}}


# --- Blocks ------------------------------------------------------------------


async def create_block(db: AsyncSession, number: int, actor: Actor) -> BlockRead:
    label = block_label(number)
    if await _block_number_taken(db, number):
        raise BlockNumberExistsError(label)
    block = Block(number=number)
    db.add(block)
    await _flush_or_conflict(db, "uq_blocks_active_number", BlockNumberExistsError(label))
    block_id = block.id
    _audit(db, actor, AuditAction.BLOCK_CREATED, "block", block_id, {"number": number, "label": label})
    await db.commit()
    return await get_block(db, block_id)


async def update_block(db: AsyncSession, block_id: int, number: int, version: int, actor: Actor) -> BlockRead:
    block = await _lock_block(db, block_id, share=False)
    _check_version(block, version)
    previous = block.number
    if number != previous:
        label = block_label(number)
        if await _block_number_taken(db, number, exclude_id=block_id):
            raise BlockNumberExistsError(label)
        # Only this row changes: towers and units keep their block_id, and
        # their derived identifiers follow the new number.
        block.number = number
        block.version = version + 1
        await _flush_or_conflict(db, "uq_blocks_active_number", BlockNumberExistsError(label))
        _audit(
            db, actor, AuditAction.BLOCK_UPDATED, "block", block_id,
            _changes(previous, number, block_label(previous), label),
        )
    await db.commit()
    return await get_block(db, block_id)


async def delete_block(db: AsyncSession, block_id: int, version: int, actor: Actor) -> None:
    block = await _lock_block(db, block_id, share=False)
    _check_version(block, version)
    label = block_label(block.number)
    if await _has_active_children(db, Tower, Tower.block_id == block_id):
        raise BlockHasActiveTowersError(label)
    block.deleted_at = utc_now()
    block.version = version + 1
    await db.flush()
    _audit(db, actor, AuditAction.BLOCK_DELETED, "block", block_id, {"number": block.number, "label": label})
    await db.commit()


# --- Towers ------------------------------------------------------------------


async def create_tower(db: AsyncSession, block_id: int, number: int, actor: Actor) -> TowerRead:
    # FOR SHARE: the block cannot be deleted (or renumbered) until this commits.
    block = await _lock_block(db, block_id, share=True)
    label = tower_label(block.number, number)
    error = TowerNumberExistsError(tower_name(number), block_label(block.number))
    if await _tower_number_taken(db, block_id, number):
        raise error
    tower = Tower(block_id=block_id, number=number)
    db.add(tower)
    await _flush_or_conflict(db, "uq_towers_block_active_number", error)
    tower_id = tower.id
    _audit(
        db, actor, AuditAction.TOWER_CREATED, "tower", tower_id,
        {"block_id": block_id, "number": number, "label": label},
    )
    await db.commit()
    return await get_tower(db, tower_id)


async def update_tower(db: AsyncSession, tower_id: int, number: int, version: int, actor: Actor) -> TowerRead:
    block, tower = await _lock_tower_path(db, tower_id, share=False)
    _check_version(tower, version)
    previous = tower.number
    if number != previous:
        label = tower_label(block.number, number)
        error = TowerNumberExistsError(tower_name(number), block_label(block.number))
        if await _tower_number_taken(db, block.id, number, exclude_id=tower_id):
            raise error
        tower.number = number
        tower.version = version + 1
        await _flush_or_conflict(db, "uq_towers_block_active_number", error)
        _audit(
            db, actor, AuditAction.TOWER_UPDATED, "tower", tower_id,
            {"block_id": block.id, **_changes(previous, number, tower_label(block.number, previous), label)},
        )
    await db.commit()
    return await get_tower(db, tower_id)


async def delete_tower(db: AsyncSession, tower_id: int, version: int, actor: Actor) -> None:
    block, tower = await _lock_tower_path(db, tower_id, share=False)
    _check_version(tower, version)
    label = tower_label(block.number, tower.number)
    if await _has_active_children(db, Unit, Unit.tower_id == tower_id):
        raise TowerHasActiveUnitsError(label)
    tower.deleted_at = utc_now()
    tower.version = version + 1
    await db.flush()
    _audit(
        db, actor, AuditAction.TOWER_DELETED, "tower", tower_id,
        {"block_id": block.id, "number": tower.number, "label": label},
    )
    await db.commit()


# --- Units (apartments) -------------------------------------------------------


async def create_unit(db: AsyncSession, tower_id: int, number: int, actor: Actor) -> UnitRead:
    # FOR SHARE on block and tower: neither can be deleted until this commits.
    block, tower = await _lock_tower_path(db, tower_id, share=True)
    label = unit_label(block.number, tower.number, number)
    error = UnitNumberExistsError(unit_name(number), tower_label(block.number, tower.number))
    if await _unit_number_taken(db, tower_id, number):
        raise error
    unit = Unit(tower_id=tower_id, number=number)
    db.add(unit)
    await _flush_or_conflict(db, "uq_units_tower_active_number", error)
    unit_id = unit.id
    _audit(
        db, actor, AuditAction.UNIT_CREATED, "unit", unit_id,
        {"block_id": block.id, "tower_id": tower_id, "number": number, "label": label},
    )
    await db.commit()
    return await get_unit(db, unit_id)


async def update_unit(db: AsyncSession, unit_id: int, number: int, version: int, actor: Actor) -> UnitRead:
    block, tower, unit = await _lock_unit_path(db, unit_id)
    _check_version(unit, version)
    previous = unit.number
    if number != previous:
        label = unit_label(block.number, tower.number, number)
        error = UnitNumberExistsError(unit_name(number), tower_label(block.number, tower.number))
        if await _unit_number_taken(db, tower.id, number, exclude_id=unit_id):
            raise error
        unit.number = number
        unit.version = version + 1
        await _flush_or_conflict(db, "uq_units_tower_active_number", error)
        _audit(
            db, actor, AuditAction.UNIT_UPDATED, "unit", unit_id,
            {
                "block_id": block.id,
                "tower_id": tower.id,
                **_changes(previous, number, unit_label(block.number, tower.number, previous), label),
            },
        )
    await db.commit()
    return await get_unit(db, unit_id)


async def _unit_in_use(db: AsyncSession, unit_id: int) -> bool:
    """Active residence or valid PENDING invitation (HU-003).

    Locking reads on the UNIQUE marker indexes, under the unit lock: they see
    the latest committed data. An expired PENDING invitation does not reserve.
    """
    if await db.scalar(
        select(ResidentUnit.id).where(ResidentUnit.active_unit_marker == unit_id).with_for_update(read=True)
    ) is not None:
        return True
    expires_at = await db.scalar(
        select(ResidentInvitation.expires_at)
        .where(
            ResidentInvitation.pending_unit_marker == unit_id,
            ResidentInvitation.status == InvitationStatus.PENDING,
        )
        .with_for_update(read=True)
    )
    return expires_at is not None and expires_at > utc_now()


async def delete_unit(db: AsyncSession, unit_id: int, version: int, actor: Actor) -> None:
    block, tower, unit = await _lock_unit_path(db, unit_id)
    _check_version(unit, version)
    label = unit_label(block.number, tower.number, unit.number)
    # HU-003: an apartment with a resident or a pending invitation is in use.
    if await _unit_in_use(db, unit_id):
        raise UnitInUseError(label)
    unit.deleted_at = utc_now()
    unit.version = version + 1
    await db.flush()
    _audit(
        db, actor, AuditAction.UNIT_DELETED, "unit", unit_id,
        {"block_id": block.id, "tower_id": tower.id, "number": unit.number, "label": label},
    )
    await db.commit()
