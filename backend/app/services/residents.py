"""Registered residents for the administrator (HU-006). Read-only.

A registered resident is a non-deleted RESIDENTE account that is ACTIVE or
DISABLED (shown as INACTIVE) and has a current residence (resident_units with
ended_at IS NULL, created by HU-019 on activation). PENDING_ACTIVATION
accounts (invited, not activated) are never listed.

Search, filter, sort and pagination run in MySQL (one COUNT and one page
query, only the needed columns); the client only chooses among whitelisted
options. The unit label is derived from the numbers (HU-013), never stored.
"""

import math
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.unit_labels import unit_label
from app.models import Block, ResidentUnit, Tower, Unit, User, UserRole, UserStatus
from app.schemas.resident import (
    ResidentListQuery,
    ResidentPage,
    ResidentRead,
    ResidentSortField,
    ResidentStatus,
    ResidentUnitRef,
    SortOrder,
)

# Account status in the database for each status shown to the administrator.
_ACCOUNT_STATUS = {ResidentStatus.ACTIVE: UserStatus.ACTIVE, ResidentStatus.INACTIVE: UserStatus.DISABLED}
_SHOWN_STATUS = {account: shown for shown, account in _ACCOUNT_STATUS.items()}

# sort_by → ordered expressions. Never built from the client's text.
_SORT_KEYS: dict[ResidentSortField, tuple[Any, ...]] = {
    ResidentSortField.NAME: (User.full_name,),
    # Same length first: numeric documents (cédulas) sort numerically.
    ResidentSortField.DOCUMENT: (func.char_length(User.document_number), User.document_number),
    # Numerically by block, tower and apartment; never the label as text.
    ResidentSortField.UNIT: (Block.number, Tower.number, Unit.number),
    ResidentSortField.PHONE: (User.phone,),
    ResidentSortField.EMAIL: (User.email,),
    # 'ACTIVE' < 'DISABLED': Activo before Inactivo.
    ResidentSortField.STATUS: (User.status,),
}
# Optional columns: residents without a value go last in both directions.
_NULLS_LAST = {ResidentSortField.DOCUMENT: User.document_number, ResidentSortField.PHONE: User.phone}


def _registered(statement: Select[Any], query: ResidentListQuery) -> Select[Any]:
    """Joins and conditions shared by the count and the page."""
    conditions: list[ColumnElement[bool]] = [
        User.role == UserRole.RESIDENTE,
        User.deleted_at.is_(None),
        User.status.in_(
            [_ACCOUNT_STATUS[query.status]] if query.status else list(_ACCOUNT_STATUS.values())
        ),
    ]
    if query.search:
        # autoescape: %, _ and the escape character are literal, not wildcards.
        conditions.append(
            User.full_name.contains(query.search, autoescape=True)
            | User.document_number.contains(query.search, autoescape=True)
        )
    return (
        statement.join(ResidentUnit, (ResidentUnit.user_id == User.id) & ResidentUnit.ended_at.is_(None))
        .join(Unit, Unit.id == ResidentUnit.unit_id)
        .join(Tower, Tower.id == Unit.tower_id)
        .join(Block, Block.id == Tower.block_id)
        .where(*conditions)
    )


def _order_by(query: ResidentListQuery) -> list[Any]:
    descending = query.sort_order == SortOrder.DESC
    keys: list[Any] = []
    if query.sort_by in _NULLS_LAST:
        keys.append(_NULLS_LAST[query.sort_by].is_(None))
    keys += [key.desc() if descending else key.asc() for key in _SORT_KEYS[query.sort_by]]
    # Stable pages: equal values always come in the same order.
    keys.append(User.id.desc() if descending else User.id.asc())
    return keys


async def list_residents(db: AsyncSession, query: ResidentListQuery) -> ResidentPage:
    total = int(await db.scalar(_registered(select(func.count(User.id)).select_from(User), query)) or 0)
    total_pages = math.ceil(total / query.page_size)
    items: list[ResidentRead] = []
    # A page beyond the last one is simply empty (total and total_pages still tell where the data is).
    if query.page <= total_pages:
        rows = (
            await db.execute(
                _registered(
                    select(
                        User.id,
                        User.full_name,
                        User.document_number,
                        User.phone,
                        User.email,
                        User.status,
                        Unit.id.label("unit_id"),
                        Unit.number.label("unit_number"),
                        Tower.number.label("tower_number"),
                        Block.number.label("block_number"),
                    ).select_from(User),
                    query,
                )
                .order_by(*_order_by(query))
                .limit(query.page_size)
                .offset((query.page - 1) * query.page_size)
            )
        ).all()
        items = [
            ResidentRead(
                id=row.id,
                full_name=row.full_name,
                document_number=row.document_number,
                phone=row.phone,
                email=row.email,
                unit=ResidentUnitRef(
                    id=row.unit_id, label=unit_label(row.block_number, row.tower_number, row.unit_number)
                ),
                status=_SHOWN_STATUS[UserStatus(row.status)],
            )
            for row in rows
        ]
    return ResidentPage(
        items=items, page=query.page, page_size=query.page_size, total=total, total_pages=total_pages
    )
