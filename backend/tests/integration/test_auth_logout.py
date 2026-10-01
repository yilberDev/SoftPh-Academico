"""HU-002: POST /api/v1/auth/logout and CSRF protection."""

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sessions import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, derive_csrf_token, generate_session_token, hash_token
from app.models import AuditAction, AuditLog, User, UserSession
from tests.integration.conftest import PASSWORD

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]


@pytest.fixture
async def session_token(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser) -> str:
    await make_user()
    response = await client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}, headers=origin_headers)
    assert response.status_code == 200
    return client.cookies[SESSION_COOKIE_NAME]


async def stored_session(db: AsyncSession, token: str) -> UserSession:
    row = await db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    assert row is not None
    await db.refresh(row)
    return row


async def test_logout_revokes_session_and_clears_cookie(
    client: AsyncClient, origin_headers: dict[str, str], session_token: str, db_session: AsyncSession
) -> None:
    response = await client.post(
        "/api/v1/auth/logout", headers={**origin_headers, CSRF_HEADER_NAME: derive_csrf_token(session_token)}
    )

    assert response.status_code == 204
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith(f"{SESSION_COOKIE_NAME}=")
    assert "max-age=0" in cookie
    assert (await stored_session(db_session, session_token)).revoked_at is not None
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_revoked_token_cannot_be_reused(
    client: AsyncClient, origin_headers: dict[str, str], session_token: str
) -> None:
    await client.post("/api/v1/auth/logout", headers={**origin_headers, CSRF_HEADER_NAME: derive_csrf_token(session_token)})

    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, session_token)
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_logout_is_audited(
    client: AsyncClient, origin_headers: dict[str, str], session_token: str, db_session: AsyncSession
) -> None:
    await client.post("/api/v1/auth/logout", headers={**origin_headers, CSRF_HEADER_NAME: derive_csrf_token(session_token)})

    [entry] = (await db_session.scalars(select(AuditLog).where(AuditLog.action == AuditAction.LOGOUT))).all()
    assert entry.actor_user_id is not None
    assert entry.ip_address == "127.0.0.1"


@pytest.mark.parametrize(
    "csrf",
    [None, "", "wrong-token", "other-session"],
    ids=["missing", "empty", "wrong", "token-of-another-session"],
)
async def test_logout_without_valid_csrf_token_is_rejected(
    client: AsyncClient, origin_headers: dict[str, str], session_token: str, db_session: AsyncSession, csrf: str | None
) -> None:
    headers = dict(origin_headers)
    if csrf == "other-session":
        headers[CSRF_HEADER_NAME] = derive_csrf_token(generate_session_token())
    elif csrf is not None:
        headers[CSRF_HEADER_NAME] = csrf

    response = await client.post("/api/v1/auth/logout", headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
    assert (await stored_session(db_session, session_token)).revoked_at is None


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": "https://evil.example"}, {"Referer": "https://evil.example/admin"}],
    ids=["no-origin-no-referer", "foreign-origin", "foreign-referer"],
)
async def test_logout_from_another_origin_is_rejected_even_with_csrf_token(
    client: AsyncClient, session_token: str, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/auth/logout", headers={**headers, CSRF_HEADER_NAME: derive_csrf_token(session_token)}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
    assert (await stored_session(db_session, session_token)).revoked_at is None


async def test_logout_without_session_is_idempotent(client: AsyncClient, origin_headers: dict[str, str]) -> None:
    response = await client.post("/api/v1/auth/logout", headers=origin_headers)

    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()
