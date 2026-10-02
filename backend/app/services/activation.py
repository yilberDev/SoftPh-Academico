"""Account activation by the resident (HU-019), continuation of HU-003.

The token of the invitation (sent by HU-003) is the only proof of identity:
the user, the apartment and the invitation always come from the invitation
found by SHA-256(token), never from the request.

    verify    reads the invitation and returns what the form shows (marks an
              expired PENDING invitation EXPIRED).
    activate  1. reads without locks to reject early (no Argon2 work for
                 invalid, used or expired links);
              2. hashes the password (Argon2id, off the event loop) with no
                 transaction or lock open;
              3. one atomic transaction: locks unit → user → invitation (the
                 approved order), checks everything again, then user ACTIVE +
                 resident_units row + invitation USED + RESIDENT_ACTIVATED.
                 Any failure rolls back everything.

No session is created: the resident then logs in with the single login (HU-007). Rejections are
audited as INVITATION_REJECTED with a safe reason only; the token, its hash
and the password are never logged, audited or returned.
"""

import re
from datetime import UTC
from typing import Any, NoReturn

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.exceptions import (
    AppError,
    DocumentAlreadyRegisteredError,
    FieldValidationError,
    InvitationInvalidError,
    InvitationLinkExpiredError,
    InvitationUsedError,
    UnitNotAvailableError,
)
from app.core.passwords import hash_password_async
from app.core.sessions import hash_token
from app.core.unit_labels import unit_label
from app.models import (
    AuditAction,
    Block,
    InvitationStatus,
    ResidentInvitation,
    ResidentUnit,
    Tower,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.schemas.activation import InvitationPreview, ResidentActivation
from app.schemas.user import PASSWORD_EQUALS_EMAIL_MESSAGE, password_equals_email
from app.services.audit import record_audit
from app.services.invitations import expire_invitation, is_expired, lock_invitation

# generate_session_token(): 32 random bytes as URL-safe base64, 43 characters.
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


def _token_hash(token: str) -> str | None:
    """SHA-256 of a well-formed token; None for anything else (answered like an unknown token)."""
    return hash_token(token) if TOKEN_PATTERN.fullmatch(token) else None


def is_pending_resident(user: User | None) -> bool:
    return (
        user is not None
        and user.deleted_at is None
        and user.role == UserRole.RESIDENTE
        and user.status == UserStatus.PENDING_ACTIVATION
    )


async def _find_invitation(db: AsyncSession, token_hash: str | None) -> ResidentInvitation | None:
    if token_hash is None:
        return None
    return await db.scalar(
        select(ResidentInvitation)
        .where(ResidentInvitation.token_hash == token_hash)
        .execution_options(populate_existing=True)
    )


async def _expire_and_audit(db: AsyncSession, invitation_id: int, ip_address: str | None) -> bool:
    """Locks the invitation and marks it EXPIRED if it is still PENDING and expired.

    Returns False when it was already used meanwhile (a concurrent activation won).
    """
    invitation = await lock_invitation(db, invitation_id)
    if invitation is None or invitation.status == InvitationStatus.USED:
        await db.rollback()
        return False
    if await expire_invitation(db, invitation_id, utc_now()):
        record_audit(
            db,
            action=AuditAction.INVITATION_EXPIRED,
            entity_type="resident_invitation",
            entity_id=invitation_id,
            context={"invitation_id": invitation_id},
            ip_address=ip_address,
        )
    await db.commit()
    return True


async def _check_usable(
    db: AsyncSession, invitation: ResidentInvitation | None, ip_address: str | None
) -> ResidentInvitation:
    """Status and expiry of an invitation, with the errors of verify/activate (no audit)."""
    if invitation is None:
        raise InvitationInvalidError()
    if invitation.status == InvitationStatus.USED:
        raise InvitationUsedError()
    if invitation.status == InvitationStatus.EXPIRED:
        raise InvitationLinkExpiredError()
    if is_expired(invitation.expires_at, utc_now()):
        invitation_id = invitation.id
        if await _expire_and_audit(db, invitation_id, ip_address):
            raise InvitationLinkExpiredError()
        raise InvitationUsedError()
    return invitation


# --- verify ----------------------------------------------------------------------------------


async def verify_invitation(db: AsyncSession, token: str, *, ip_address: str | None) -> InvitationPreview:
    await db.commit()  # fresh transaction: see the latest committed state
    invitation = await _check_usable(db, await _find_invitation(db, _token_hash(token)), ip_address)
    row = (
        await db.execute(
            select(User, Unit.number, Tower.number, Block.number)
            .join(ResidentInvitation, ResidentInvitation.user_id == User.id)
            .join(Unit, Unit.id == ResidentInvitation.unit_id)
            .join(Tower, Tower.id == Unit.tower_id)
            .join(Block, Block.id == Tower.block_id)
            .where(ResidentInvitation.id == invitation.id)
        )
    ).one()
    user, unit_number, tower_number, block_number = row
    if not is_pending_resident(user):
        raise InvitationInvalidError()
    return InvitationPreview(
        email=user.email,
        full_name=user.full_name,
        unit_code=unit_label(block_number, tower_number, unit_number),
        expires_at=invitation.expires_at.replace(tzinfo=UTC),
    )


# --- activate ---------------------------------------------------------------------------------


async def _reject(
    db: AsyncSession, reason: str, invitation_id: int | None, ip_address: str | None, error: AppError
) -> NoReturn:
    """Undoes everything, records INVITATION_REJECTED (safe reason only) and raises."""
    await db.rollback()
    record_audit(
        db,
        action=AuditAction.INVITATION_REJECTED,
        entity_type="resident_invitation" if invitation_id is not None else None,
        entity_id=invitation_id,
        context={"reason": reason},
        ip_address=ip_address,
    )
    await db.commit()
    raise error


async def _lock_activation_rows(
    db: AsyncSession, unit_id: int, user_id: int, invitation_id: int
) -> tuple[bool, User | None, ResidentInvitation | None]:
    """Locks in the approved order unit → user → invitation (FOR UPDATE).

    The first statement is a locking read, so the consistent reads that follow
    in this transaction see everything committed before the unit lock.
    """
    unit_active = await db.scalar(
        select(Unit.id).where(Unit.id == unit_id, Unit.deleted_at.is_(None)).with_for_update()
    ) is not None
    user = await db.scalar(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    )
    invitation = await lock_invitation(db, invitation_id)
    return unit_active, user, invitation


async def activate_account(db: AsyncSession, data: ResidentActivation, *, ip_address: str | None) -> None:
    token_hash = _token_hash(data.token)

    # 1. Early checks without locks (and without Argon2 work).
    await db.commit()
    invitation = await _find_invitation(db, token_hash)
    if invitation is None:
        await _reject(db, "invalid", None, ip_address, InvitationInvalidError())
    invitation_id, unit_id, user_id = invitation.id, invitation.unit_id, invitation.user_id
    try:
        await _check_usable(db, invitation, ip_address)
    except InvitationLinkExpiredError as exc:
        await _reject(db, "expired", invitation_id, ip_address, exc)
    except InvitationUsedError as exc:
        await _reject(db, "used", invitation_id, ip_address, exc)
    user = await db.get(User, user_id, populate_existing=True)
    if user is None or not is_pending_resident(user):
        await _reject(db, "user_not_pending", invitation_id, ip_address, InvitationInvalidError())
    if password_equals_email(data.password, user.email):
        raise FieldValidationError("password", PASSWORD_EQUALS_EMAIL_MESSAGE)
    await db.commit()  # nothing stays open while hashing

    # 2. Argon2id off the event loop, with no transaction or lock open.
    password_hash = await hash_password_async(data.password.get_secret_value())

    # 3. One atomic transaction; everything is checked again under the locks.
    unit_active, user, locked = await _lock_activation_rows(db, unit_id, user_id, invitation_id)
    now = utc_now()
    if locked is None or locked.token_hash != token_hash:
        # The link was rotated by a resend meanwhile: this token is no longer valid.
        await _reject(db, "invalid", invitation_id, ip_address, InvitationInvalidError())
    if locked.status == InvitationStatus.USED:
        await _reject(db, "used", invitation_id, ip_address, InvitationUsedError())
    if locked.status != InvitationStatus.PENDING:
        await _reject(db, "expired", invitation_id, ip_address, InvitationLinkExpiredError())
    if is_expired(locked.expires_at, now):
        await expire_invitation(db, invitation_id, now)
        record_audit(
            db,
            action=AuditAction.INVITATION_EXPIRED,
            entity_type="resident_invitation",
            entity_id=invitation_id,
            context={"invitation_id": invitation_id},
            ip_address=ip_address,
        )
        await db.commit()
        await _reject(db, "expired", invitation_id, ip_address, InvitationLinkExpiredError())
    if user is None or not is_pending_resident(user):
        await _reject(db, "user_not_pending", invitation_id, ip_address, InvitationInvalidError())
    if not unit_active or await db.scalar(
        select(exists().where(ResidentUnit.unit_id == unit_id, ResidentUnit.ended_at.is_(None)))
    ):
        await _reject(db, "unit_not_available", invitation_id, ip_address, UnitNotAvailableError())
    if await db.scalar(
        select(exists().where(User.document_number == data.document_number, User.id != user_id))
    ):
        await db.rollback()
        raise DocumentAlreadyRegisteredError()

    user.status = UserStatus.ACTIVE
    user.password_hash = password_hash
    user.document_number = data.document_number
    user.phone = data.phone
    db.add(ResidentUnit(user_id=user_id, unit_id=unit_id, invitation_id=invitation_id, started_at=now))
    locked.status = InvitationStatus.USED
    locked.used_at = now  # expires_at is kept as history
    try:
        await db.flush()
    except IntegrityError as exc:
        # The UNIQUE indexes are the final guarantee if a writer skipped the locks.
        await db.rollback()
        message = str(exc.orig)
        if "uq_users_document_number" in message:
            raise DocumentAlreadyRegisteredError() from exc
        if "uq_resident_units_active_unit" in message:
            await _reject(db, "unit_not_available", invitation_id, ip_address, UnitNotAvailableError())
        if "uq_resident_units_active_user" in message or "uq_resident_units_invitation_id" in message:
            await _reject(db, "user_not_pending", invitation_id, ip_address, InvitationInvalidError())
        raise
    context: dict[str, Any] = {"invitation_id": invitation_id, "unit_id": unit_id}
    record_audit(
        db,
        action=AuditAction.RESIDENT_ACTIVATED,
        entity_type="user",
        entity_id=user_id,
        actor_user_id=user_id,
        context=context,
        ip_address=ip_address,
    )
    await db.commit()
