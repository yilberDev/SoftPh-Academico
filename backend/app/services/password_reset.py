"""Password reset of residents (HU-016). Never for administrators.

    request   Always the same answer. Only an ACTIVE, non-deleted RESIDENTE gets
              a token: previous pending tokens are revoked, a new one (1 hour)
              is stored as SHA-256 and the email is sent AFTER the response
              (background task), so neither the body, the status code nor the
              response time reveals whether the email belongs to a resident.
              At most MAX_REQUESTS_PER_WINDOW tokens per resident per
              REQUEST_WINDOW: extra requests are silently ignored (no 429).
    verify    Reads the token (no writes) and returns its expiry.
    confirm   1. reads without locks to reject early (no Argon2 work for bad
                 links); 2. hashes the new password (Argon2id, off the event
                 loop) with nothing open; 3. one atomic transaction that locks
                 user → token (the same order as request), checks everything
                 again and then: new password, token used, every session of
                 the user revoked, PASSWORD_RESET_COMPLETED. No new session.

MySQL guarantees one pending reset per user (UNIQUE on a generated column).
The login locks the user row too (HU-002), so a login and a reset of the same
account are serialized: no session created with the old password survives.
The token, its hash and the password are never logged, audited or returned.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.exceptions import (
    AppError,
    FieldValidationError,
    PasswordResetExpiredError,
    PasswordResetInvalidError,
    PasswordResetRevokedError,
    PasswordResetUsedError,
)
from app.core.passwords import hash_password_async
from app.core.sessions import generate_session_token, hash_token, hash_well_formed_token
from app.db.session import SessionFactory
from app.models import AuditAction, PasswordResetToken, User, UserRole, UserSession, UserStatus
from app.schemas.password_reset import PasswordResetConfirm, PasswordResetPreview
from app.schemas.user import PASSWORD_EQUALS_EMAIL_MESSAGE, password_equals_email
from app.services.audit import record_audit
from app.services.mail import MailSender
from app.services.password_reset_email import build_password_reset_email

logger = logging.getLogger(__name__)

RESET_TTL = timedelta(hours=1)
MAX_REQUESTS_PER_WINDOW = 3
REQUEST_WINDOW = timedelta(hours=1)


def is_eligible(user: User | None) -> bool:
    """Only an ACTIVE, non-deleted resident can reset the password (HU-016)."""
    return (
        user is not None
        and user.deleted_at is None
        and user.role == UserRole.RESIDENTE
        and user.status == UserStatus.ACTIVE
    )


def _audit(
    db: AsyncSession,
    action: AuditAction,
    *,
    ip_address: str | None,
    context: dict[str, Any],
    user_id: int | None = None,
    reset_id: int | None = None,
) -> None:
    record_audit(
        db,
        action=action,
        entity_type="password_reset" if reset_id is not None else ("user" if user_id is not None else None),
        entity_id=reset_id if reset_id is not None else user_id,
        actor_user_id=user_id if action == AuditAction.PASSWORD_RESET_COMPLETED else None,
        context=context,
        ip_address=ip_address,
    )


async def _lock_user(db: AsyncSession, user_id: int) -> User | None:
    return await db.scalar(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    )


# --- request -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResetDelivery:
    """What the background task needs to send the email. The token only lives here, in memory."""

    reset_id: int
    to: str
    full_name: str
    token: str
    expires_at: datetime


async def request_reset(db: AsyncSession, email: str, *, ip_address: str | None) -> ResetDelivery | None:
    """Creates a reset for an eligible resident; None otherwise. The caller answers the same."""
    await db.commit()
    user_id = await db.scalar(select(User.id).where(User.email == email))
    if user_id is None:
        return None  # unknown email: nothing is written
    # New transaction whose first statement is a locking read: the reads that
    # follow see everything committed before the lock was granted.
    await db.commit()
    user = await _lock_user(db, user_id)
    if user is None or not is_eligible(user):
        _audit(db, AuditAction.PASSWORD_RESET_REJECTED, ip_address=ip_address, user_id=user_id,
               context={"reason": "not_eligible"})
        await db.commit()
        return None

    now = utc_now()
    recent = await db.scalar(
        select(func.count())
        .select_from(PasswordResetToken)
        .where(PasswordResetToken.user_id == user_id, PasswordResetToken.created_at > now - REQUEST_WINDOW)
    )
    if (recent or 0) >= MAX_REQUESTS_PER_WINDOW:
        _audit(db, AuditAction.PASSWORD_RESET_REJECTED, ip_address=ip_address, user_id=user_id,
               context={"reason": "rate_limited"})
        await db.commit()
        return None

    # Only the newest link works: every pending token of the user is revoked.
    await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    token = generate_session_token()
    reset = PasswordResetToken(user_id=user_id, token_hash=hash_token(token), created_at=now, expires_at=now + RESET_TTL)
    db.add(reset)
    await db.flush()
    reset_id = reset.id
    _audit(db, AuditAction.PASSWORD_RESET_REQUESTED, ip_address=ip_address, user_id=user_id,
           context={"reset_id": reset_id})
    delivery = ResetDelivery(reset_id=reset_id, to=user.email, full_name=user.full_name, token=token,
                             expires_at=reset.expires_at)
    await db.commit()
    return delivery


async def deliver_reset_email(
    delivery: ResetDelivery, *, mailer: MailSender, app_origin: str, session_factory: SessionFactory
) -> None:
    """Background task (after the response). A failure is audited by type only."""
    email = build_password_reset_email(
        to=delivery.to, full_name=delivery.full_name, app_origin=app_origin, token=delivery.token,
        expires_at=delivery.expires_at,
    )
    try:
        await mailer.send(email)
    except Exception as exc:  # any SMTP/network failure; the body (token) is never logged
        error = type(exc).__name__
        logger.warning("Password reset email not sent (reset_id=%s, error=%s)", delivery.reset_id, error)
        try:
            async with session_factory() as session:
                _audit(session, AuditAction.PASSWORD_RESET_EMAIL_FAILED, ip_address=None, reset_id=delivery.reset_id,
                       context={"reset_id": delivery.reset_id, "error": error})
                await session.commit()
        except Exception as audit_exc:
            logger.error("Password reset email failure not audited (reset_id=%s, error=%s)",
                         delivery.reset_id, type(audit_exc).__name__)


# --- verify / confirm ----------------------------------------------------------------------------


def _check_state(reset: PasswordResetToken | None, now: datetime) -> PasswordResetToken:
    if reset is None:
        raise PasswordResetInvalidError()
    if reset.used_at is not None:
        raise PasswordResetUsedError()
    if reset.revoked_at is not None:
        raise PasswordResetRevokedError()
    if reset.expires_at <= now:
        raise PasswordResetExpiredError()
    return reset


async def _find(db: AsyncSession, token_hash: str | None) -> PasswordResetToken | None:
    if token_hash is None:
        return None
    return await db.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == token_hash)
        .execution_options(populate_existing=True)
    )


async def verify_reset(db: AsyncSession, token: str) -> PasswordResetPreview:
    await db.commit()  # fresh transaction: see the latest committed state
    reset = _check_state(await _find(db, hash_well_formed_token(token)), utc_now())
    user = await db.get(User, reset.user_id, populate_existing=True)
    if not is_eligible(user):
        raise PasswordResetInvalidError()
    return PasswordResetPreview(expires_at=reset.expires_at.replace(tzinfo=UTC))


_REASONS: dict[type[AppError], str] = {
    PasswordResetInvalidError: "invalid",
    PasswordResetUsedError: "used",
    PasswordResetRevokedError: "revoked",
    PasswordResetExpiredError: "expired",
}


async def _reject(db: AsyncSession, error: AppError, reset_id: int | None, ip_address: str | None,
                  reason: str | None = None) -> NoReturn:
    """Undoes everything, records PASSWORD_RESET_REJECTED (safe reason only) and raises."""
    await db.rollback()
    _audit(db, AuditAction.PASSWORD_RESET_REJECTED, ip_address=ip_address, reset_id=reset_id,
           context={"reason": reason or _REASONS[type(error)]})
    await db.commit()
    raise error


async def confirm_reset(db: AsyncSession, data: PasswordResetConfirm, *, ip_address: str | None) -> None:
    token_hash = hash_well_formed_token(data.token)

    # 1. Early checks without locks (and without Argon2 work).
    await db.commit()
    reset = await _find(db, token_hash)
    reset_id = reset.id if reset is not None else None
    try:
        reset = _check_state(reset, utc_now())
    except AppError as exc:
        await _reject(db, exc, reset_id, ip_address)
    user_id = reset.user_id
    user = await db.get(User, user_id, populate_existing=True)
    if user is None or not is_eligible(user):
        await _reject(db, PasswordResetInvalidError(), reset_id, ip_address, "user_not_eligible")
    if password_equals_email(data.password, user.email):
        raise FieldValidationError("password", PASSWORD_EQUALS_EMAIL_MESSAGE)
    await db.commit()  # nothing stays open while hashing

    # 2. Argon2id off the event loop, with no transaction or lock open.
    password_hash = await hash_password_async(data.password.get_secret_value())

    # 3. One atomic transaction: user → token, then everything is checked again.
    user = await _lock_user(db, user_id)
    locked = await db.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.id == reset_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = utc_now()
    if locked is None or locked.token_hash != token_hash:
        await _reject(db, PasswordResetInvalidError(), reset_id, ip_address)
    try:
        _check_state(locked, now)
    except AppError as exc:
        await _reject(db, exc, reset_id, ip_address)
    if user is None or not is_eligible(user):
        await _reject(db, PasswordResetInvalidError(), reset_id, ip_address, "user_not_eligible")

    user.password_hash = password_hash
    locked.used_at = now
    await db.flush()
    # Any other pending link of the user stops working too (normally none: UNIQUE).
    await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    # Every open session ends: the resident logs in again with the new password.
    revoked = await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    _audit(db, AuditAction.PASSWORD_RESET_COMPLETED, ip_address=ip_address, user_id=user_id,
           context={"reset_id": reset_id, "sessions_revoked": revoked.rowcount})  # type: ignore[attr-defined]
    await db.commit()
