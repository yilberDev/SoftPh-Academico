"""Resident invitations (HU-003): the administrator invites a resident to an apartment.

Flow (design E-1): the data is committed BEFORE the email is sent, and no lock
or transaction is kept open during SMTP.

    T1  lock the unit → (lock the user) → create/reuse the PENDING_ACTIVATION
        user → invitation PENDING with delivery_status PENDING → RESIDENT_INVITED → COMMIT
    SMTP  send the link (the token only exists in memory)
    T2  delivery_status SENT/FAILED, send_attempts + 1 → INVITATION_EMAIL_SENT/FAILED → COMMIT

Guarantees, with MySQL as the final authority:
- One PENDING invitation per apartment and per user, one active residence per
  apartment and per user, one user per email: UNIQUE indexes. The checks here
  only produce clear messages; a concurrent winner still gets the same 409.
- Lock order unit → user → invitation (→ residence, HU-019). A resend only
  locks its invitation row.
- Only SHA-256(token) is stored. The token is never logged, audited or returned.
- Status transitions only go PENDING → EXPIRED here (PENDING → USED is HU-019);
  every UPDATE of the status is conditioned on status = 'PENDING'.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Row, exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.exceptions import (
    EmailAlreadyRegisteredError,
    InvitationAlreadyPendingError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationNotPendingError,
    UnitNotAvailableError,
)
from app.core.sessions import generate_session_token, hash_token
from app.core.unit_labels import unit_label
from app.models import (
    AuditAction,
    Block,
    DeliveryStatus,
    InvitationStatus,
    ResidentInvitation,
    ResidentUnit,
    Tower,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.schemas.invitation import (
    AvailableUnit,
    AvailableUnitList,
    InvitationCreate,
    InvitationList,
    InvitationRead,
    InvitationUnit,
)
from app.services.audit import record_audit
from app.services.invitation_email import build_invitation_email
from app.services.mail import MailSender
from app.services.structure import Actor

logger = logging.getLogger(__name__)


def is_expired(expires_at: datetime, now: datetime) -> bool:
    """An invitation is valid strictly before expires_at (both naive UTC)."""
    return expires_at <= now


def _utc(moment: datetime) -> datetime:
    # MySQL DATETIME columns hold naive UTC values; the API returns aware UTC.
    return moment.replace(tzinfo=UTC)


# --- Reads ---------------------------------------------------------------------------

_INVITATIONS = (
    select(
        ResidentInvitation.id,
        ResidentInvitation.status,
        ResidentInvitation.delivery_status,
        ResidentInvitation.send_attempts,
        ResidentInvitation.validity_days,
        ResidentInvitation.created_at,
        ResidentInvitation.expires_at,
        User.email,
        User.full_name,
        Unit.id.label("unit_id"),
        Unit.number.label("unit_number"),
        Tower.number.label("tower_number"),
        Block.number.label("block_number"),
    )
    .join(User, User.id == ResidentInvitation.user_id)
    .join(Unit, Unit.id == ResidentInvitation.unit_id)
    .join(Tower, Tower.id == Unit.tower_id)
    .join(Block, Block.id == Tower.block_id)
)


def _invitation_read(row: Row[Any]) -> InvitationRead:
    return InvitationRead(
        id=row.id,
        email=row.email,
        full_name=row.full_name,
        unit=InvitationUnit(id=row.unit_id, label=unit_label(row.block_number, row.tower_number, row.unit_number)),
        status=row.status,
        delivery_status=row.delivery_status,
        send_attempts=row.send_attempts,
        validity_days=row.validity_days,
        created_at=_utc(row.created_at),
        expires_at=_utc(row.expires_at),
    )


async def get_invitation(db: AsyncSession, invitation_id: int) -> InvitationRead:
    row = (await db.execute(_INVITATIONS.where(ResidentInvitation.id == invitation_id))).first()
    if row is None:
        raise InvitationNotFoundError()
    return _invitation_read(row)


async def list_pending_invitations(db: AsyncSession) -> InvitationList:
    """Valid pending invitations (E-1b), soonest to expire first. Read-only: no lazy expiry here."""
    now = utc_now()
    rows = (
        await db.execute(
            _INVITATIONS.where(
                ResidentInvitation.status == InvitationStatus.PENDING, ResidentInvitation.expires_at > now
            ).order_by(ResidentInvitation.expires_at, ResidentInvitation.id)
        )
    ).all()
    return InvitationList(items=[_invitation_read(row) for row in rows])


async def list_available_units(db: AsyncSession) -> AvailableUnitList:
    """Apartments that can be offered: active (with active tower and block), without an
    active residence and without a valid PENDING invitation. Read-only: an expired
    PENDING invitation simply does not reserve the apartment."""
    now = utc_now()
    rows = (
        await db.execute(
            select(Unit.id, Unit.number, Tower.number.label("tower_number"), Block.number.label("block_number"))
            .join(Tower, Tower.id == Unit.tower_id)
            .join(Block, Block.id == Tower.block_id)
            .where(
                Unit.deleted_at.is_(None),
                Tower.deleted_at.is_(None),
                Block.deleted_at.is_(None),
                ~exists().where(ResidentUnit.unit_id == Unit.id, ResidentUnit.ended_at.is_(None)),
                ~exists().where(
                    ResidentInvitation.unit_id == Unit.id,
                    ResidentInvitation.status == InvitationStatus.PENDING,
                    ResidentInvitation.expires_at > now,
                ),
            )
            .order_by(Block.number, Tower.number, Unit.number)
        )
    ).all()
    return AvailableUnitList(
        items=[
            AvailableUnit(
                unit_id=row.id,
                label=unit_label(row.block_number, row.tower_number, row.number),
                block_number=row.block_number,
                tower_number=row.tower_number,
                number=row.number,
            )
            for row in rows
        ]
    )


# --- T1: reserve the apartment and create the invitation ----------------------------------


class _ConcurrentNewUser(Exception):
    """Another transaction created a user with the same email first (uq_users_email)."""


async def _expire(db: AsyncSession, invitation_id: int, now: datetime) -> bool:
    """PENDING → EXPIRED, only if it is still pending and already expired."""
    result = await db.execute(
        update(ResidentInvitation)
        .where(
            ResidentInvitation.id == invitation_id,
            ResidentInvitation.status == InvitationStatus.PENDING,
            ResidentInvitation.expires_at <= now,
        )
        .values(status=InvitationStatus.EXPIRED)
    )
    return result.rowcount == 1  # type: ignore[attr-defined]


async def _pending_invitation(db: AsyncSession, **criteria: int) -> ResidentInvitation | None:
    return await db.scalar(
        select(ResidentInvitation)
        .filter_by(status=InvitationStatus.PENDING, **criteria)
        .execution_options(populate_existing=True)
    )


async def _lock_available_unit(db: AsyncSession, unit_id: int, now: datetime) -> list[int]:
    """Locks the apartment (FOR UPDATE) and checks that it can be reserved.

    Returns the ids of expired PENDING invitations it marked EXPIRED.
    """
    locked = await db.scalar(
        select(Unit.id).where(Unit.id == unit_id, Unit.deleted_at.is_(None)).with_for_update()
    )
    if locked is None:
        raise UnitNotAvailableError()
    if await db.scalar(
        select(exists().where(ResidentUnit.unit_id == unit_id, ResidentUnit.ended_at.is_(None)))
    ):
        raise UnitNotAvailableError()
    pending = await _pending_invitation(db, unit_id=unit_id)
    if pending is None:
        return []
    if not is_expired(pending.expires_at, now) or not await _expire(db, pending.id, now):
        raise UnitNotAvailableError()
    return [pending.id]


async def _lock_user_by_email(db: AsyncSession, email: str) -> User | None:
    # Look the id up first and lock the existing row by primary key: a locking
    # read on a missing email would take a gap lock that deadlocks concurrent
    # inserts. A missing user is created and uq_users_email decides any race.
    user_id = await db.scalar(select(User.id).where(User.email == email))
    if user_id is None:
        return None
    return await db.scalar(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    )


async def _reserve(db: AsyncSession, data: InvitationCreate, actor: Actor, token_hash: str) -> int:
    # End the read-only transaction opened by the session lookup: the first
    # statement below is a locking read, so the consistent reads that follow
    # see everything committed before the unit lock was granted.
    await db.commit()
    now = utc_now()
    expired_ids = await _lock_available_unit(db, data.unit_id, now)

    user = await _lock_user_by_email(db, data.email)
    reinvitation = user is not None
    name_updated = False
    if user is None:
        user = User(
            email=data.email,
            full_name=data.full_name,
            role=UserRole.RESIDENTE,
            status=UserStatus.PENDING_ACTIVATION,
            password_hash=None,
        )
        db.add(user)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            if "uq_users_email" in str(exc.orig):
                raise _ConcurrentNewUser() from exc
            raise
    else:
        # Only a resident that never activated the account can be invited again (R-3).
        if user.deleted_at is not None or user.role != UserRole.RESIDENTE or user.status != UserStatus.PENDING_ACTIVATION:
            raise EmailAlreadyRegisteredError()
        previous = await _pending_invitation(db, user_id=user.id)
        if previous is not None:
            if not is_expired(previous.expires_at, now) or not await _expire(db, previous.id, now):
                raise InvitationAlreadyPendingError()
            expired_ids.append(previous.id)
        if user.full_name != data.full_name:
            user.full_name = data.full_name  # approved: the not-yet-activated account takes the new name
            name_updated = True

    invitation = ResidentInvitation(
        token_hash=token_hash,
        user_id=user.id,
        unit_id=data.unit_id,
        invited_by_user_id=actor.user_id,
        validity_days=data.validity_days,
        status=InvitationStatus.PENDING,
        delivery_status=DeliveryStatus.PENDING,
        send_attempts=0,
        created_at=now,
        expires_at=now + timedelta(days=data.validity_days),
    )
    db.add(invitation)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_resident_invitations_pending_unit" in str(exc.orig):
            raise UnitNotAvailableError() from exc
        if "uq_resident_invitations_pending_user" in str(exc.orig):
            raise InvitationAlreadyPendingError() from exc
        raise
    invitation_id = invitation.id
    context: dict[str, Any] = {
        "invitation_id": invitation_id,
        "unit_id": data.unit_id,
        "validity_days": data.validity_days,
        "reinvitation": reinvitation,
    }
    if expired_ids:
        context["expired_invitation_ids"] = expired_ids
    if name_updated:
        context["full_name_updated"] = True
    record_audit(
        db,
        action=AuditAction.RESIDENT_INVITED,
        entity_type="user",
        entity_id=user.id,
        actor_user_id=actor.user_id,
        context=context,
        ip_address=actor.ip_address,
    )
    await db.commit()
    return invitation_id


# --- SMTP and T2: deliver and record the result -----------------------------------------


async def _lock_invitation(db: AsyncSession, invitation_id: int) -> ResidentInvitation | None:
    return await db.scalar(
        select(ResidentInvitation)
        .where(ResidentInvitation.id == invitation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _record_delivery(
    db: AsyncSession, invitation_id: int, token_hash: str, error: str | None, actor: Actor
) -> None:
    invitation = await _lock_invitation(db, invitation_id)
    if invitation is None:
        return
    attempt = invitation.send_attempts + 1
    invitation.send_attempts = attempt
    # A later resend may have rotated the token meanwhile: the result of an
    # email with an older link must not overwrite the current delivery status.
    current = invitation.token_hash == token_hash
    if current:
        if error is None:
            invitation.delivery_status = DeliveryStatus.SENT
            invitation.last_sent_at = utc_now()
        else:
            invitation.delivery_status = DeliveryStatus.FAILED
    context: dict[str, Any] = {"invitation_id": invitation_id, "attempt": attempt}
    if error is not None:
        context["error"] = error
    if not current:
        context["superseded"] = True
    await db.flush()
    record_audit(
        db,
        action=AuditAction.INVITATION_EMAIL_SENT if error is None else AuditAction.INVITATION_EMAIL_FAILED,
        entity_type="resident_invitation",
        entity_id=invitation_id,
        actor_user_id=actor.user_id,
        context=context,
        ip_address=actor.ip_address,
    )
    await db.commit()


async def _deliver(
    db: AsyncSession, invitation_id: int, token: str, actor: Actor, *, mailer: MailSender, app_origin: str
) -> InvitationRead:
    invitation = await get_invitation(db, invitation_id)
    # No transaction (and no lock) stays open while SMTP runs.
    await db.commit()
    email = build_invitation_email(
        to=invitation.email,
        full_name=invitation.full_name,
        unit_label=invitation.unit.label,
        app_origin=app_origin,
        token=token,
        expires_at=invitation.expires_at,
    )
    error: str | None = None
    try:
        await mailer.send(email)
    except Exception as exc:  # any SMTP/network failure; the body (token) is never logged
        error = type(exc).__name__
        logger.warning("Invitation email not sent (invitation_id=%s, error=%s)", invitation_id, error)
    try:
        await _record_delivery(db, invitation_id, hash_token(token), error, actor)
    except Exception as exc:
        # The invitation stays valid with delivery_status PENDING (unconfirmed); it can be resent.
        await db.rollback()
        logger.error(
            "Delivery result not recorded (invitation_id=%s, error=%s)", invitation_id, type(exc).__name__
        )
    return await get_invitation(db, invitation_id)


async def create_invitation(
    db: AsyncSession, data: InvitationCreate, actor: Actor, *, mailer: MailSender, app_origin: str
) -> InvitationRead:
    """Creates (or re-creates, R-3) the invitation, then sends it. Returns the final state."""
    token = generate_session_token()
    try:
        invitation_id = await _reserve(db, data, actor, hash_token(token))
    except _ConcurrentNewUser:
        # Retry once: now the user exists and the normal rules apply
        # (usually 409 INVITATION_ALREADY_PENDING).
        try:
            invitation_id = await _reserve(db, data, actor, hash_token(token))
        except _ConcurrentNewUser as exc:
            raise EmailAlreadyRegisteredError() from exc
    return await _deliver(db, invitation_id, token, actor, mailer=mailer, app_origin=app_origin)


async def resend_invitation(
    db: AsyncSession, invitation_id: int, actor: Actor, *, mailer: MailSender, app_origin: str
) -> InvitationRead:
    """Same invitation, new token (the previous link stops working). Does not extend expires_at (E-1c)."""
    await db.commit()
    now = utc_now()
    # Concurrent resends of the same invitation run one after the other here.
    invitation = await _lock_invitation(db, invitation_id)
    if invitation is None:
        raise InvitationNotFoundError()
    if invitation.status == InvitationStatus.EXPIRED:
        raise InvitationExpiredError()
    if invitation.status != InvitationStatus.PENDING:
        raise InvitationNotPendingError()
    if is_expired(invitation.expires_at, now):
        await _expire(db, invitation_id, now)
        await db.commit()
        raise InvitationExpiredError()

    token = generate_session_token()
    invitation.token_hash = hash_token(token)
    invitation.delivery_status = DeliveryStatus.PENDING
    await db.flush()
    record_audit(
        db,
        action=AuditAction.INVITATION_RESENT,
        entity_type="resident_invitation",
        entity_id=invitation_id,
        actor_user_id=actor.user_id,
        context={"invitation_id": invitation_id, "unit_id": invitation.unit_id},
        ip_address=actor.ip_address,
    )
    await db.commit()
    return await _deliver(db, invitation_id, token, actor, mailer=mailer, app_origin=app_origin)
