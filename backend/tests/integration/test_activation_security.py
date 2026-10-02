"""HU-019: public activation routes (Origin, no session) and secrecy of token and password."""

import logging
import re
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from tests.integration.conftest import FakeMailSender

pytestmark = pytest.mark.anyio

API = "/api/v1"
TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]+)")
PASSWORD = "una clave segura 2026"
ROUTES = [f"{API}/resident-invitations/verify", f"{API}/resident-invitations/activate"]


@pytest.fixture
async def token(client: AsyncClient, admin_headers: dict[str, str], mailer: FakeMailSender) -> str:
    block = (await client.post(f"{API}/blocks", json={"number": 1}, headers=admin_headers)).json()
    tower = (await client.post(f"{API}/blocks/{block['id']}/towers", json={"number": 1}, headers=admin_headers)).json()
    unit = (await client.post(f"{API}/towers/{tower['id']}/units", json={"number": 101}, headers=admin_headers)).json()
    response = await client.post(
        f"{API}/resident-invitations",
        json={"email": "residente@example.com", "full_name": "Laura Gómez", "unit_id": unit["id"], "validity_days": 7},
        headers=admin_headers,
    )
    assert response.status_code == 201
    client.cookies.clear()
    return TOKEN_RE.search(mailer.sent[-1].body).group(1)  # type: ignore[union-attr]


def tampered(token: str) -> str:
    """The same token with its last character changed (never equal to the original)."""
    return token[:-1] + ("A" if token[-1] != "A" else "B")


def activation(token: str, **overrides: Any) -> dict[str, Any]:
    return {
        "token": token,
        "document_number": "CC1234567",
        "phone": None,
        "password": PASSWORD,
        "password_confirmation": PASSWORD,
        **overrides,
    }


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://127.0.0.1:9999"])
@pytest.mark.parametrize("route", ROUTES)
async def test_public_routes_require_the_public_origin(
    client: AsyncClient, token: str, db_session: AsyncSession, route: str, origin: str | None
) -> None:
    headers = {"Origin": origin} if origin else {}

    response = await client.post(route, json=activation(token), headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
    status = await db_session.scalar(text("SELECT status FROM users WHERE email = 'residente@example.com'"))
    assert status == "PENDING_ACTIVATION"


async def test_no_session_is_needed_nor_created(client: AsyncClient, token: str, origin_headers: dict[str, str]) -> None:
    verify = await client.post(ROUTES[0], json={"token": token}, headers=origin_headers)
    activate = await client.post(ROUTES[1], json=activation(token), headers=origin_headers)

    assert (verify.status_code, activate.status_code) == (200, 200)
    assert "set-cookie" not in verify.headers and "set-cookie" not in activate.headers
    assert (await client.get(f"{API}/auth/session")).status_code == 401


async def test_token_only_travels_in_the_body() -> None:
    # The routes have no path or query parameters: the token cannot end up in a URL.
    for route in ROUTES:
        operation = app.openapi()["paths"][route]["post"]
        assert "parameters" not in operation
        assert operation["requestBody"]["required"] is True


async def test_responses_expose_no_secrets(client: AsyncClient, token: str, origin_headers: dict[str, str]) -> None:
    verify = await client.post(ROUTES[0], json={"token": token}, headers=origin_headers)
    activate = await client.post(ROUTES[1], json=activation(token), headers=origin_headers)
    used = await client.post(ROUTES[1], json=activation(token), headers=origin_headers)

    assert set(verify.json()) == {"email", "full_name", "unit_code", "expires_at"}
    for response in (verify, activate, used):
        text_ = response.text
        assert token not in text_ and PASSWORD not in text_
        for forbidden in ("token", "token_hash", "password_hash", "user_id", "invitation_id", "unit_id", "$argon2"):
            assert forbidden not in text_


async def test_token_and_password_never_leak(
    client: AsyncClient,
    token: str,
    origin_headers: dict[str, str],
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    responses = [
        await client.post(ROUTES[0], json={"token": token}, headers=origin_headers),
        await client.post(ROUTES[1], json=activation(token, password_confirmation="no coincide 12345"), headers=origin_headers),
        await client.post(ROUTES[1], json=activation(token, document_number="!!"), headers=origin_headers),
        await client.post(ROUTES[1], json=activation(tampered(token)), headers=origin_headers),
        await client.post(ROUTES[1], json=activation(token), headers=origin_headers),
        await client.post(ROUTES[1], json=activation(token), headers=origin_headers),
        await client.post(ROUTES[0], json={"token": token}, headers=origin_headers),
    ]
    assert [r.status_code for r in responses] == [200, 422, 422, 404, 200, 410, 410]

    dump = ""
    for table in ("users", "resident_invitations", "resident_units", "audit_logs", "sessions"):
        dump += str((await db_session.execute(text(f"SELECT * FROM {table}"))).all())
    audit = str((await db_session.execute(text("SELECT action, context FROM audit_logs"))).all())
    password_hash = await db_session.scalar(text("SELECT password_hash FROM users WHERE email = 'residente@example.com'"))
    token_hash = await db_session.scalar(text("SELECT token_hash FROM resident_invitations"))

    for secret in (token, tampered(token), PASSWORD, "no coincide 12345"):
        assert secret not in dump
        assert secret not in caplog.text
        assert all(secret not in r.text for r in responses)
    assert password_hash not in audit and token_hash not in audit
    assert all(password_hash not in r.text and token_hash not in r.text for r in responses)
