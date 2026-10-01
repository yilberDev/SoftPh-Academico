"""Login and logout (HU-002)."""

import math
from dataclasses import dataclass
from secrets import token_urlsafe

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.exceptions import InvalidCredentialsError, LoginTemporarilyLockedError
from app.core.passwords import hash_password_async, needs_rehash, verify_password_async
from app.models import AuditAction, User, UserRole, UserStatus
from app.schemas.auth import LoginRequest
from app.services.audit import record_audit
from app.services.login_throttle import (
    ThrottlePolicy,
    apply_state,
    clear_throttle,
    identifier_hash,
    lock_remaining,
    lock_throttle_row,
    register_failure,
    state_of,
)
from app.services.sessions import AuthContext, create_session, revoke_session_token

# Roles that may obtain a session. RESIDENTE is added with HU-007.
LOGIN_ALLOWED_ROLES = frozenset({UserRole.ADMINISTRADOR})


@dataclass(frozen=True)
class LoginResult:
    user: User
    token: str


_dummy_hash: str | None = None


async def dummy_password_hash() -> str:
    """Verified when the email is unknown, so every attempt costs the same time.

    Computed once, outside the event loop; the app lifespan warms it up at startup.
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = await hash_password_async(token_urlsafe(32))
    return _dummy_hash


def _failure_reason(user: User | None, password_ok: bool) -> str | None:
    if user is None:
        return "unknown_email"
    if user.deleted_at is not None:
        return "deleted"
    if user.status != UserStatus.ACTIVE:
        return "inactive"
    if user.role not in LOGIN_ALLOWED_ROLES:
        return "role_not_allowed"
    if not password_ok:
        return "invalid_password"
    return None


async def authenticate(
    db: AsyncSession,
    data: LoginRequest,
    *,
    settings: Settings,
    ip_address: str | None,
    previous_token: str | None,
) -> LoginResult:
    """Validates credentials and opens a session.

    Every failure cause produces the same InvalidCredentialsError. Attempts for
    the same identifier are serialized by a row lock on login_throttles.
    """
    now = utc_now()
    identifier = identifier_hash(data.email)
    throttle = await lock_throttle_row(db, identifier, now)

    remaining = lock_remaining(state_of(throttle), now)
    if remaining is not None:
        record_audit(db, action=AuditAction.LOGIN_FAILED, context={"reason": "locked"}, ip_address=ip_address)
        await db.commit()
        raise LoginTemporarilyLockedError(math.ceil(remaining.total_seconds()))

    user = await db.scalar(select(User).where(User.email == data.email))
    password = data.password.get_secret_value()
    stored_hash = user.password_hash if user is not None and user.password_hash else await dummy_password_hash()
    password_ok = await verify_password_async(stored_hash, password)

    reason = _failure_reason(user, password_ok)
    if reason is not None:
        state = register_failure(state_of(throttle), now, ThrottlePolicy.from_settings(settings))
        apply_state(throttle, state)
        user_id = user.id if user is not None else None
        record_audit(
            db,
            action=AuditAction.LOGIN_FAILED,
            entity_type="user" if user_id else None,
            entity_id=user_id,
            context={"reason": reason},
            ip_address=ip_address,
        )
        if state.locked_until is not None:
            record_audit(
                db,
                action=AuditAction.LOGIN_LOCKED,
                entity_type="user" if user_id else None,
                entity_id=user_id,
                context={"failed_attempts": state.failed_attempts},
                ip_address=ip_address,
            )
        await db.commit()
        raise InvalidCredentialsError()

    assert user is not None and user.password_hash is not None
    await clear_throttle(db, identifier)
    if needs_rehash(user.password_hash):
        user.password_hash = await hash_password_async(password)
    if previous_token:
        # A new login always gets a new session (prevents session fixation).
        await revoke_session_token(db, previous_token, now)
    token = await create_session(db, user, settings, now)
    record_audit(
        db,
        action=AuditAction.LOGIN_SUCCEEDED,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        ip_address=ip_address,
    )
    await db.commit()
    return LoginResult(user=user, token=token)


async def logout(db: AsyncSession, auth: AuthContext, *, ip_address: str | None) -> None:
    auth.session.revoked_at = utc_now()
    record_audit(
        db,
        action=AuditAction.LOGOUT,
        actor_user_id=auth.user.id,
        entity_type="user",
        entity_id=auth.user.id,
        ip_address=ip_address,
    )
    await db.commit()
