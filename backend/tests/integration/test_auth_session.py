"""HU-002: GET /api/v1/auth/session, expiration and invalidation."""

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sessions import SESSION_COOKIE_NAME, derive_csrf_token, hash_token
from app.models import User, UserSession, UserStatus
from tests.integration.conftest import PASSWORD

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]
NOT_AUTHENTICATED = {"error": {"code": "NOT_AUTHENTICATED", "message": "Debes iniciar sesión para continuar.", "details": []}}


@pytest.fixture
async def logged_in(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser) -> User:
    user = await make_user()
    response = await client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}, headers=origin_headers)
    assert response.status_code == 200
    return user


def current_token(client: AsyncClient) -> str:
    return client.cookies[SESSION_COOKIE_NAME]


async def set_session(db: AsyncSession, token: str, **values: object) -> None:
    await db.execute(update(UserSession).where(UserSession.token_hash == hash_token(token)).values(**values))


async def test_returns_current_user_and_csrf_token(client: AsyncClient, logged_in: User) -> None:
    response = await client.get("/api/v1/auth/session")

    assert response.status_code == 200
    assert response.json() == {
        "user": {"id": logged_in.id, "full_name": "Ana María Pérez", "email": "admin@example.com", "role": "ADMINISTRADOR"},
        "csrf_token": derive_csrf_token(current_token(client)),
    }
    assert response.headers["cache-control"] == "no-store"


async def test_without_cookie_is_not_authenticated(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/session")

    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED


async def test_unknown_token_is_not_authenticated(client: AsyncClient) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, "made-up-token")

    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_absolute_expiration(client: AsyncClient, logged_in: User, db_session: AsyncSession) -> None:
    await set_session(db_session, current_token(client), expires_at=text("UTC_TIMESTAMP(6) - INTERVAL 1 SECOND"))

    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_idle_expiration_after_30_minutes(client: AsyncClient, logged_in: User, db_session: AsyncSession) -> None:
    await set_session(db_session, current_token(client), last_seen_at=text("UTC_TIMESTAMP(6) - INTERVAL 30 MINUTE"))

    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_activity_before_idle_limit_keeps_session_and_refreshes_last_seen(
    client: AsyncClient, logged_in: User, db_session: AsyncSession
) -> None:
    token = current_token(client)
    await set_session(db_session, token, last_seen_at=text("UTC_TIMESTAMP(6) - INTERVAL 29 MINUTE"))

    assert (await client.get("/api/v1/auth/session")).status_code == 200

    idle_seconds = await db_session.scalar(
        text("SELECT TIMESTAMPDIFF(SECOND, last_seen_at, UTC_TIMESTAMP(6)) FROM sessions WHERE token_hash = :h"),
        {"h": hash_token(token)},
    )
    assert idle_seconds is not None and idle_seconds < 5


async def test_revoked_session_is_rejected(client: AsyncClient, logged_in: User, db_session: AsyncSession) -> None:
    await set_session(db_session, current_token(client), revoked_at=text("UTC_TIMESTAMP(6)"))

    assert (await client.get("/api/v1/auth/session")).status_code == 401


@pytest.mark.parametrize("change", ["disable", "soft-delete"])
async def test_disabling_or_deleting_the_user_ends_its_sessions(
    client: AsyncClient, logged_in: User, db_session: AsyncSession, change: str
) -> None:
    values = {"status": UserStatus.DISABLED} if change == "disable" else {"deleted_at": text("UTC_TIMESTAMP(6)")}
    await db_session.execute(update(User).where(User.id == logged_in.id).values(**values))

    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_session_row_is_kept_for_history_after_expiration(client: AsyncClient, logged_in: User, db_session: AsyncSession) -> None:
    token = current_token(client)
    await set_session(db_session, token, expires_at=text("UTC_TIMESTAMP(6) - INTERVAL 1 SECOND"))
    await client.get("/api/v1/auth/session")

    assert await db_session.scalar(select(UserSession.id).where(UserSession.token_hash == hash_token(token))) is not None
