"""Failed login control (HU-002, criterion 3).

Policy (configurable): N failures within the window lock the identifier for
the lockout period. The identifier is the SHA-256 of the normalized email, so
unknown emails are throttled exactly like registered ones.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.emails import normalize_email
from app.models import LoginThrottle


@dataclass(frozen=True)
class ThrottlePolicy:
    max_failed_attempts: int
    window: timedelta
    lockout: timedelta

    @classmethod
    def from_settings(cls, settings: Settings) -> "ThrottlePolicy":
        return cls(
            max_failed_attempts=settings.login_max_failed_attempts,
            window=timedelta(minutes=settings.login_failure_window_minutes),
            lockout=timedelta(minutes=settings.login_lockout_minutes),
        )


@dataclass(frozen=True)
class ThrottleState:
    failed_attempts: int
    window_started_at: datetime
    locked_until: datetime | None


def lock_remaining(state: ThrottleState | None, now: datetime) -> timedelta | None:
    if state is None or state.locked_until is None or state.locked_until <= now:
        return None
    return state.locked_until - now


def register_failure(state: ThrottleState | None, now: datetime, policy: ThrottlePolicy) -> ThrottleState:
    """Counts one failure. Reaching the limit locks the identifier."""
    lock_expired = state is not None and state.locked_until is not None and state.locked_until <= now
    if state is None or lock_expired or now - state.window_started_at >= policy.window:
        attempts, window_started_at = 1, now
    else:
        attempts, window_started_at = state.failed_attempts + 1, state.window_started_at
    locked_until = now + policy.lockout if attempts >= policy.max_failed_attempts else None
    return ThrottleState(attempts, window_started_at, locked_until)


def identifier_hash(email: str) -> str:
    """SHA-256 of the canonical form of the email the client sent.

    Never derived from the email stored in the database: equivalent variants
    must share a counter whether or not the account exists.
    """
    return hashlib.sha256(normalize_email(email).encode()).hexdigest()


async def lock_throttle_row(session: AsyncSession, identifier: str, now: datetime) -> LoginThrottle:
    """Ensures the row exists and locks it (SELECT ... FOR UPDATE).

    Every attempt for the same identifier is serialized until the caller's
    transaction ends, so concurrent failures are all counted and no attempt
    can slip past a lock that another attempt just set.
    """
    await session.execute(
        insert(LoginThrottle)
        .values(identifier_hash=identifier, failed_attempts=0, window_started_at=now, locked_until=None)
        .on_duplicate_key_update(identifier_hash=identifier)
    )
    row = await session.scalar(
        select(LoginThrottle)
        .where(LoginThrottle.identifier_hash == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


def state_of(row: LoginThrottle) -> ThrottleState:
    return ThrottleState(row.failed_attempts, row.window_started_at, row.locked_until)


def apply_state(row: LoginThrottle, state: ThrottleState) -> None:
    row.failed_attempts = state.failed_attempts
    row.window_started_at = state.window_started_at
    row.locked_until = state.locked_until


async def clear_throttle(session: AsyncSession, identifier: str) -> None:
    await session.execute(delete(LoginThrottle).where(LoginThrottle.identifier_hash == identifier))
