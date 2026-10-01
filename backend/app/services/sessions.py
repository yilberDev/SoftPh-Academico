from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.sessions import derive_csrf_token, generate_session_token, hash_token, is_session_expired, should_touch
from app.models import User, UserSession, UserStatus


@dataclass(frozen=True)
class AuthContext:
    """The authenticated user of the current request and its session."""

    user: User
    session: UserSession
    token: str

    @property
    def csrf_token(self) -> str:
        return derive_csrf_token(self.token)


async def create_session(db: AsyncSession, user: User, settings: Settings, now: datetime) -> str:
    """Adds a new session to the caller's transaction and returns its token."""
    token = generate_session_token()
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            last_seen_at=now,
            expires_at=now + timedelta(minutes=settings.session_absolute_ttl_minutes),
        )
    )
    await db.flush()
    return token


async def resolve_session(db: AsyncSession, token: str | None, settings: Settings) -> AuthContext | None:
    """Returns the context for a valid session, or None.

    Valid means: not revoked, within the absolute and idle limits, and owned by
    an active, non-deleted user (disabling an account ends its sessions).
    """
    if not token:
        return None
    row = (
        await db.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == hash_token(token))
        )
    ).first()
    if row is None:
        return None
    user_session, user = row
    now = utc_now()
    if user_session.revoked_at is not None or is_session_expired(
        expires_at=user_session.expires_at,
        last_seen_at=user_session.last_seen_at,
        now=now,
        idle_ttl=timedelta(minutes=settings.session_idle_ttl_minutes),
    ):
        return None
    if user.deleted_at is not None or user.status != UserStatus.ACTIVE:
        return None
    if should_touch(user_session.last_seen_at, now):
        user_session.last_seen_at = now
        await db.commit()
    return AuthContext(user=user, session=user_session, token=token)


async def revoke_session_token(db: AsyncSession, token: str, now: datetime) -> None:
    """Revokes the session for this token, if any (adds to the caller's transaction)."""
    await db.execute(
        update(UserSession)
        .where(UserSession.token_hash == hash_token(token), UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
