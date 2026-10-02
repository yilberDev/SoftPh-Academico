"""HU-016: public routes (Origin, no session) and secrecy of token and password."""

import hashlib
import logging
import re
import smtplib
from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import User, UserRole
from tests.integration.conftest import PASSWORD, FakeMailSender

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]
API = "/api/v1/auth/password-reset"
ROUTES = [f"{API}/request", f"{API}/verify", f"{API}/confirm"]
RESIDENT = "residente@example.com"
NEW_PASSWORD = "una frase nueva y segura 2026"
TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]+)")


@pytest.fixture
async def resident(make_user: MakeUser) -> User:
    return await make_user(RESIDENT, role=UserRole.RESIDENTE)


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://127.0.0.1:9999"])
@pytest.mark.parametrize("route", ROUTES)
async def test_public_routes_require_the_public_origin(
    client: AsyncClient, resident: User, mailer: FakeMailSender, db_session: AsyncSession, route: str, origin: str | None
) -> None:
    body = {"email": RESIDENT} if route.endswith("request") else {"token": "x" * 43, "password": NEW_PASSWORD, "password_confirmation": NEW_PASSWORD}
    response = await client.post(route, json=body, headers={"Origin": origin} if origin else {})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
    assert mailer.sent == []
    assert await db_session.scalar(text("SELECT COUNT(*) FROM password_reset_tokens")) == 0


def test_tokens_never_travel_in_the_url() -> None:
    for route in ROUTES:
        operation = app.openapi()["paths"][route]["post"]
        assert "parameters" not in operation and operation["requestBody"]["required"] is True


async def test_nothing_secret_leaks(
    client: AsyncClient,
    origin_headers: dict[str, str],
    resident: User,
    mailer: FakeMailSender,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    responses = [await client.post(f"{API}/request", json={"email": RESIDENT}, headers=origin_headers)]
    token = TOKEN_RE.search(mailer.sent[-1].body).group(1)  # type: ignore[union-attr]
    # An SMTP error that echoes the message (and so a token) must not leak either.
    mailer.fail_with = smtplib.SMTPDataError(554, b"rejected: " + token.encode())
    responses.append(await client.post(f"{API}/request", json={"email": RESIDENT}, headers=origin_headers))
    mailer.fail_with = None
    responses.append(await client.post(f"{API}/request", json={"email": RESIDENT}, headers=origin_headers))
    current = TOKEN_RE.search(mailer.sent[-1].body).group(1)  # type: ignore[union-attr]
    responses += [
        await client.post(f"{API}/verify", json={"token": current}, headers=origin_headers),
        await client.post(f"{API}/confirm", json={"token": current, "password": "corta", "password_confirmation": "corta"}, headers=origin_headers),
        await client.post(f"{API}/confirm", json={"token": token, "password": NEW_PASSWORD, "password_confirmation": NEW_PASSWORD}, headers=origin_headers),
        await client.post(f"{API}/confirm", json={"token": current, "password": NEW_PASSWORD, "password_confirmation": NEW_PASSWORD}, headers=origin_headers),
    ]
    assert [r.status_code for r in responses] == [202, 202, 202, 200, 422, 410, 200]

    dump = ""
    for table in ("password_reset_tokens", "audit_logs", "users", "sessions"):
        dump += str((await db_session.execute(text(f"SELECT * FROM {table}"))).all())
    audit = str((await db_session.execute(text("SELECT action, context FROM audit_logs"))).all())
    password_hash = await db_session.scalar(text("SELECT password_hash FROM users WHERE email = :e"), {"e": RESIDENT})
    hashes = [r[0] for r in (await db_session.execute(text("SELECT token_hash FROM password_reset_tokens"))).all()]
    texts = "".join(r.text for r in responses)

    for secret in (token, current, NEW_PASSWORD, PASSWORD, "corta"):
        assert secret not in dump and secret not in caplog.text and secret not in texts
    for secret in (password_hash, *hashes):
        assert secret not in audit and secret not in texts
    # Stored only as SHA-256 (three requests: two known tokens and the one whose email failed).
    assert len(hashes) == 3
    assert {hashlib.sha256(t.encode()).hexdigest() for t in (token, current)} <= set(hashes)
    for forbidden in ("token", "password_hash", "user_id", "email"):
        assert f'"{forbidden}"' not in texts
    assert "SMTPDataError" in caplog.text  # the failure is logged by type only


async def test_no_session_is_created(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender
) -> None:
    await client.post(f"{API}/request", json={"email": RESIDENT}, headers=origin_headers)
    token = TOKEN_RE.search(mailer.sent[-1].body).group(1)  # type: ignore[union-attr]
    for response in (
        await client.post(f"{API}/verify", json={"token": token}, headers=origin_headers),
        await client.post(f"{API}/confirm", json={"token": token, "password": NEW_PASSWORD, "password_confirmation": NEW_PASSWORD}, headers=origin_headers),
    ):
        assert "set-cookie" not in response.headers
    assert (await client.get("/api/v1/auth/session")).status_code == 401
