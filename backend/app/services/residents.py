"""Registered residents for the administrator: the list (HU-006, read-only) and the detail,
update and logical deletion of one resident (HU-015).

A registered resident is a non-deleted RESIDENTE account that is ACTIVE or
DISABLED (shown as INACTIVE) and has a current residence (resident_units with
ended_at IS NULL, created by HU-019 on activation). PENDING_ACTIVATION
accounts (invited, not activated) are never listed.

Search, filter, sort and pagination run in MySQL (one COUNT and one page
query, only the needed columns); the client only chooses among whitelisted
options. The unit label is derived from the numbers (HU-013), never stored.

HU-015 acts only on a registered resident (the same definition, through _registered):

    detail   one resident, with updated_at as the optimistic-locking token.
    update   full name, document and phone (the data of the registration; never the email,
             role, status or apartment). One transaction: the resident's row is locked (FOR
             UPDATE) and checked again (still registered, same updated_at), the document must not
             belong to another account (uq_users_document_number is the final guarantee), then
             RESIDENT_UPDATED with the names of the changed fields only.
    delete   logical: users.deleted_at (the row, its payments, feedback and history stay). The
             same lock order as HU-004 payments and HU-010: block (S) -> tower (S) -> apartment
             (X) -> current residence (X) -> resident (X). The residence ends (the apartment can
             be invited again, HU-003), every open session is revoked (as HU-016 does) and
             RESIDENT_DELETED is recorded, all in one transaction. Charges belong to the
             apartment, never to the resident: no balance moves.
             Refused (409 RESIDENT_DELETION_BLOCKED, CU-015 7b, approved decision) while the
             apartment has a pending balance or a credit (saldo a favor) other than zero
             (payments.unit_balance, as UNIT_HAS_BALANCE does for HU-013), or a live demo payment
             (INITIATED and not expired, demo_payments.is_expired) on one of its obligations. Every
             financial write of an apartment (HU-004 payments, charges, monthly generation and
             every HU-010 demo transition) takes this same apartment lock first, so the
             consistent reads made under it cannot change before the COMMIT. The email of a
             deleted account stays reserved (uq_users_email; HU-003 refuses to invite it again).
"""

import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.exceptions import (
    DocumentAlreadyRegisteredError,
    ResidentDeletionBlockedError,
    ResidentNotFoundError,
    UnitNotFoundError,
    VersionConflictError,
)
from app.core.unit_labels import unit_label
from app.models import (
    AuditAction,
    Block,
    Charge,
    DemoPaymentTransaction,
    DemoPaymentTransactionStatus,
    ResidentUnit,
    Tower,
    Unit,
    User,
    UserRole,
    UserSession,
    UserStatus,
)
from app.schemas.resident import (
    ResidentDetail,
    ResidentListQuery,
    ResidentPage,
    ResidentRead,
    ResidentSortField,
    ResidentStatus,
    ResidentUnitRef,
    ResidentUpdate,
    SortOrder,
    utc_naive,
)
from app.services.audit import record_audit
from app.services.demo_payments import is_expired as demo_payment_expired
from app.services.payments import unit_balance
from app.services.structure import Actor, lock_unit_path

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


# --- HU-015: detail, update and logical deletion of one registered resident -------------------------------

ENTITY = "user"
# No search and no status filter: exactly the HU-006 definition of a registered resident.
_ANY_REGISTERED = ResidentListQuery()
# The fields of ResidentUpdate, in the order they are compared and audited.
_EDITABLE = ("full_name", "document_number", "phone")


def _is_registered_account(user: User) -> bool:
    """The account part of _registered (the current residence is checked by the caller)."""
    return user.role == UserRole.RESIDENTE and user.deleted_at is None and user.status in _ACCOUNT_STATUS.values()


def _document_taken() -> DocumentAlreadyRegisteredError:
    # The same 409 as the activation (HU-019), plus the field, so the form shows it next to it.
    error = DocumentAlreadyRegisteredError()
    error.details = [{"field": "document_number", "message": error.message}]
    return error


async def get_resident(db: AsyncSession, resident_id: int) -> ResidentDetail:
    row = (
        await db.execute(
            _registered(
                select(
                    User.id,
                    User.full_name,
                    User.document_number,
                    User.phone,
                    User.email,
                    User.status,
                    User.updated_at,
                    Unit.id.label("unit_id"),
                    Unit.number.label("unit_number"),
                    Tower.number.label("tower_number"),
                    Block.number.label("block_number"),
                ).select_from(User),
                _ANY_REGISTERED,
            ).where(User.id == resident_id)
        )
    ).first()
    if row is None:
        raise ResidentNotFoundError()
    return ResidentDetail(
        id=row.id,
        full_name=row.full_name,
        document_number=row.document_number,
        phone=row.phone,
        email=row.email,
        unit=ResidentUnitRef(id=row.unit_id, label=unit_label(row.block_number, row.tower_number, row.unit_number)),
        status=_SHOWN_STATUS[UserStatus(row.status)],
        updated_at=row.updated_at.replace(tzinfo=UTC),
    )


async def _lock_resident(db: AsyncSession, resident_id: int) -> User | None:
    return await db.scalar(
        select(User).where(User.id == resident_id).with_for_update().execution_options(populate_existing=True)
    )


def _check_token(user: User, updated_at: datetime) -> None:
    if utc_naive(user.updated_at) != utc_naive(updated_at):
        raise VersionConflictError()


async def update_resident(db: AsyncSession, resident_id: int, data: ResidentUpdate, actor: Actor) -> ResidentDetail:
    # The first statement of the transaction is the locking read: its view starts after the lock.
    await db.commit()
    try:
        user = await _lock_resident(db, resident_id)
        if user is None or not _is_registered_account(user) or not await db.scalar(
            select(exists().where(ResidentUnit.user_id == resident_id, ResidentUnit.ended_at.is_(None)))
        ):
            raise ResidentNotFoundError()
        _check_token(user, data.updated_at)
        if await db.scalar(
            select(exists().where(User.document_number == data.document_number, User.id != resident_id))
        ):
            raise _document_taken()
        changed = [field for field in _EDITABLE if getattr(user, field) != getattr(data, field)]
        if changed:
            for field in changed:
                setattr(user, field, getattr(data, field))
            try:
                await db.flush()
            except IntegrityError as exc:
                # The UNIQUE index decides a race with another writer of the same document.
                if "uq_users_document_number" in str(exc.orig):
                    raise _document_taken() from exc
                raise
            # Only the names of the fields: the audit never copies personal data.
            record_audit(db, action=AuditAction.RESIDENT_UPDATED, entity_type=ENTITY, entity_id=resident_id,
                         actor_user_id=actor.user_id, context={"changed_fields": changed},
                         ip_address=actor.ip_address)
        await db.commit()
    except Exception:
        # Nothing is half saved: the previous information stays (CU-015 9a).
        await db.rollback()
        raise
    return await get_resident(db, resident_id)


async def _current_unit(db: AsyncSession, resident_id: int) -> tuple[int, int, int] | None:
    """(unit_id, tower_id, block_id) of the resident's current residence (no locks)."""
    row = (
        await db.execute(
            select(ResidentUnit.unit_id, Unit.tower_id, Tower.block_id)
            .join(Unit, Unit.id == ResidentUnit.unit_id)
            .join(Tower, Tower.id == Unit.tower_id)
            .where(ResidentUnit.user_id == resident_id, ResidentUnit.ended_at.is_(None))
        )
    ).first()
    return None if row is None else (row.unit_id, row.tower_id, row.block_id)


async def _live_demo_payment(db: AsyncSession, unit_id: int, now: datetime) -> bool:
    """A demo transaction still in progress on an obligation of the apartment (consistent read under its lock).

    INITIATED past its expiry is not live: it can no longer be completed (HU-010 marks it EXPIRED on
    its next read), so it never blocks the deletion.
    """
    initiated = (
        await db.scalars(
            select(DemoPaymentTransaction)
            .join(Charge, Charge.id == DemoPaymentTransaction.charge_id)
            .where(Charge.unit_id == unit_id, DemoPaymentTransaction.status == DemoPaymentTransactionStatus.INITIATED)
        )
    ).all()
    return any(not demo_payment_expired(transaction, now) for transaction in initiated)


async def _deletion_blockers(db: AsyncSession, unit_id: int, label: str, now: datetime) -> list[tuple[str, str]]:
    """CU-015 7b: the conditions of the apartment that must be resolved first (none: it can be deleted)."""
    balance = await unit_balance(db, unit_id)  # the derived balance of HU-004, never recomputed here
    reasons: list[tuple[str, str]] = []
    if balance.pending_balance != 0:
        reasons.append(("pending_balance", f"su unidad residencial {label} tiene saldo pendiente por pagar"))
    if balance.credit_balance != 0:
        reasons.append(("credit_balance", f"su unidad residencial {label} tiene saldo a favor sin aplicar"))
    if await _live_demo_payment(db, unit_id, now):
        reasons.append(("demo_payment",
                        f"hay un pago de demostración en curso para una obligación de su unidad residencial {label}"))
    return reasons


async def delete_resident(db: AsyncSession, resident_id: int, updated_at: datetime, actor: Actor) -> None:
    # 1. Which apartment (no locks), in its own transaction.
    await db.commit()
    current = await _current_unit(db, resident_id)
    await db.commit()
    if current is None:
        raise ResidentNotFoundError()
    unit_id, tower_id, block_id = current

    # 2. One transaction whose first statement is a locking read, in the financial lock order.
    try:
        try:
            block, tower, unit = await lock_unit_path(db, unit_id, tower_id=tower_id, block_id=block_id)
        except UnitNotFoundError:
            raise VersionConflictError() from None  # the residence changed meanwhile
        residence = await db.scalar(
            select(ResidentUnit)
            .where(ResidentUnit.active_unit_marker == unit_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        user = await _lock_resident(db, resident_id)
        if user is None or not _is_registered_account(user):
            raise ResidentNotFoundError()
        if residence is None or residence.user_id != resident_id:
            raise VersionConflictError()
        _check_token(user, updated_at)
        now = utc_now()
        label = unit_label(block.number, tower.number, unit.number)
        blockers = await _deletion_blockers(db, unit_id, label, now)
        if blockers:
            raise ResidentDeletionBlockedError(blockers)
        user.deleted_at = now  # logical: the account and its history stay; login and sessions refuse it
        residence.ended_at = now  # the apartment is free again (HU-003 can invite another resident)
        revoked = await db.execute(
            update(UserSession)
            .where(UserSession.user_id == resident_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        record_audit(
            db,
            action=AuditAction.RESIDENT_DELETED,
            entity_type=ENTITY,
            entity_id=resident_id,
            actor_user_id=actor.user_id,
            context={
                "unit_id": unit_id,
                "unit_label": label,
                "residence_id": residence.id,
                "sessions_revoked": revoked.rowcount,  # type: ignore[attr-defined]
            },
            ip_address=actor.ip_address,
        )
        await db.commit()
    except Exception:
        # Nothing is half deleted: the resident, the residence and the sessions stay as they were.
        await db.rollback()
        raise
