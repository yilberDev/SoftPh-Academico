"""HU-002: passwords, session tokens and hashes never leave the server."""

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sessions import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, derive_csrf_token
from app.models import User
from tests.integration.conftest import PASSWORD

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]


async def dump_database(db: AsyncSession) -> str:
    rows = []
    for table in ("users", "sessions", "login_throttles", "audit_logs"):
        rows += [repr(tuple(r)) for r in (await db.execute(text(f"SELECT * FROM {table}"))).all()]
    return "\n".join(rows)


async def test_token_and_password_never_leave_the_server(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession
) -> None:
    user = await make_user()
    await client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong password"}, headers=origin_headers)
    login = await client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}, headers=origin_headers)
    token = client.cookies[SESSION_COOKIE_NAME]
    session = await client.get("/api/v1/auth/session")
    await client.post("/api/v1/auth/logout", headers={**origin_headers, CSRF_HEADER_NAME: derive_csrf_token(token)})

    assert user.password_hash is not None
    for response in (login, session):
        assert token not in response.text
        assert PASSWORD not in response.text
        assert user.password_hash not in response.text
        assert "password" not in response.text
        assert "token_hash" not in response.text

    database = await dump_database(db_session)
    assert token not in database
    assert PASSWORD not in database
    assert "wrong password" not in database
    assert derive_csrf_token(token) not in database
