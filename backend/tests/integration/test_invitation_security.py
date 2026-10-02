"""HU-003: access control, CSRF and secrecy of the invitation token."""

import logging
import re
import smtplib
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sessions import CSRF_HEADER_NAME, derive_csrf_token, generate_session_token
from app.main import app
from app.models import User, UserRole
from tests.integration.conftest import FakeMailSender, sign_in

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]
API = "/api/v1"
TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]+)")

READS = ["/units/available", "/resident-invitations?status=PENDING"]
WRITES: list[tuple[str, dict[str, Any] | None]] = [
    ("/resident-invitations", {"email": "residente@example.com", "full_name": "Laura Gómez", "unit_id": 1, "validity_days": 7}),
    ("/resident-invitations/1/resend", None),
]


async def make_unit(client: AsyncClient, headers: dict[str, str], number: int = 104) -> int:
    block = (await client.post(f"{API}/blocks", json={"number": 1}, headers=headers)).json()
    if "id" not in block:
        block = (await client.get(f"{API}/blocks")).json()["items"][0]
    towers = (await client.get(f"{API}/blocks/{block['id']}/towers")).json()["items"]
    tower = towers[0] if towers else (
        await client.post(f"{API}/blocks/{block['id']}/towers", json={"number": 2}, headers=headers)
    ).json()
    unit = await client.post(f"{API}/towers/{tower['id']}/units", json={"number": number}, headers=headers)
    return unit.json()["id"]


async def invite(client: AsyncClient, headers: dict[str, str], unit_id: int, email: str = "residente@example.com") -> Any:
    return await client.post(
        f"{API}/resident-invitations",
        json={"email": email, "full_name": "Laura Gómez", "unit_id": unit_id, "validity_days": 7},
        headers=headers,
    )


# --- Authentication and roles ---------------------------------------------------------------


@pytest.mark.parametrize("path", READS)
async def test_reads_require_a_session(client: AsyncClient, path: str) -> None:
    response = await client.get(f"{API}{path}")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.parametrize(("path", "body"), WRITES)
async def test_writes_require_a_session(
    client: AsyncClient, origin_headers: dict[str, str], mailer: FakeMailSender, path: str, body: Any
) -> None:
    headers = {**origin_headers, CSRF_HEADER_NAME: derive_csrf_token(generate_session_token())}
    response = await client.post(f"{API}{path}", json=body, headers=headers)
    assert response.status_code == 401
    assert mailer.sent == []


@pytest.fixture
async def resident_headers(client: AsyncClient, db_session: AsyncSession, make_user: MakeUser) -> dict[str, str]:
    resident = await make_user("resident@example.com", role=UserRole.RESIDENTE, password_hash="not-a-real-hash")
    return await sign_in(client, db_session, resident)


@pytest.mark.parametrize("path", READS)
async def test_residents_cannot_read(client: AsyncClient, resident_headers: dict[str, str], path: str) -> None:
    response = await client.get(f"{API}{path}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize(("path", "body"), WRITES)
async def test_residents_cannot_invite_or_resend(
    client: AsyncClient, resident_headers: dict[str, str], mailer: FakeMailSender, path: str, body: Any
) -> None:
    response = await client.post(f"{API}{path}", json=body, headers=resident_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert mailer.sent == []


async def test_invalid_session_cookie_is_rejected(client: AsyncClient) -> None:
    client.cookies.set("softph_session", generate_session_token())
    assert (await client.get(f"{API}/units/available")).status_code == 401


# --- CSRF and Origin --------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "body"), WRITES)
@pytest.mark.parametrize("csrf", ["missing", "wrong", "other-session"])
async def test_writes_require_the_csrf_token(
    client: AsyncClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    mailer: FakeMailSender,
    path: str,
    body: Any,
    csrf: str,
) -> None:
    unit_id = await make_unit(client, admin_headers)
    if body is not None:
        body = {**body, "unit_id": unit_id}
    headers = {"Origin": admin_headers["Origin"]}
    if csrf == "wrong":
        headers[CSRF_HEADER_NAME] = "wrong-token"
    elif csrf == "other-session":
        headers[CSRF_HEADER_NAME] = derive_csrf_token(generate_session_token())

    response = await client.post(f"{API}{path}", json=body, headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
    assert await db_session.scalar(text("SELECT COUNT(*) FROM resident_invitations")) == 0
    assert mailer.sent == []


@pytest.mark.parametrize(("path", "body"), WRITES)
@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://127.0.0.1:9999"])
async def test_writes_require_the_public_origin(
    client: AsyncClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    mailer: FakeMailSender,
    path: str,
    body: Any,
    origin: str | None,
) -> None:
    unit_id = await make_unit(client, admin_headers)
    if body is not None:
        body = {**body, "unit_id": unit_id}
    headers = {CSRF_HEADER_NAME: admin_headers[CSRF_HEADER_NAME]}
    if origin is not None:
        headers["Origin"] = origin

    response = await client.post(f"{API}{path}", json=body, headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
    assert await db_session.scalar(text("SELECT COUNT(*) FROM resident_invitations")) == 0
    assert mailer.sent == []


def test_every_invitation_route_is_covered() -> None:
    published = {
        (method.upper(), path.removeprefix(API))
        for path, operations in app.openapi()["paths"].items()
        if path.startswith((f"{API}/resident-invitations", f"{API}/units/available"))
        # HU-019 public activation routes: tested in test_activation_security.py.
        and path not in (f"{API}/resident-invitations/verify", f"{API}/resident-invitations/activate")
        for method in operations
    }
    assert published == {
        ("GET", "/units/available"),
        ("GET", "/resident-invitations"),
        ("POST", "/resident-invitations"),
        ("POST", "/resident-invitations/{invitation_id}/resend"),
    }


async def test_no_anonymous_endpoint_creates_or_changes_data(client: AsyncClient, origin_headers: dict[str, str]) -> None:
    # Every state-changing route needs a session except login/logout and the HU-019
    # activation routes (Origin + one-time token; they never create users): none can
    # create residents anonymously.
    anonymous_ok = {
        ("POST", f"{API}/auth/login"),
        ("POST", f"{API}/auth/logout"),
        ("POST", f"{API}/resident-invitations/verify"),
        ("POST", f"{API}/resident-invitations/activate"),
        # HU-016 password reset (Origin + one-time token; never creates users).
        ("POST", f"{API}/auth/password-reset/request"),
        ("POST", f"{API}/auth/password-reset/verify"),
        ("POST", f"{API}/auth/password-reset/confirm"),
    }
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if method.upper() in {"GET", "HEAD", "OPTIONS"} or (method.upper(), path) in anonymous_ok:
                continue
            concrete = re.sub(r"\{[^}]+\}", "1", path)
            response = await client.request(method.upper(), concrete, json={}, headers=origin_headers)
            assert response.status_code == 401, (method, path, response.status_code)


# --- Manipulated unit_id ------------------------------------------------------------------


async def test_occupied_unit_cannot_be_forced_through_unit_id(
    client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    unit_id = await make_unit(client, admin_headers)
    assert (await invite(client, admin_headers, unit_id, "uno@example.com")).status_code == 201

    # The unit is not offered, but the client sends its id anyway.
    available = (await client.get(f"{API}/units/available")).json()["items"]
    assert unit_id not in [u["unit_id"] for u in available]
    response = await invite(client, admin_headers, unit_id, "dos@example.com")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "UNIT_NOT_AVAILABLE"
    assert await db_session.scalar(text("SELECT COUNT(*) FROM resident_invitations")) == 1


# --- The token never leaves the email ---------------------------------------------------------


async def test_token_never_leaks(
    client: AsyncClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    mailer: FakeMailSender,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    unit_id = await make_unit(client, admin_headers)
    created = await invite(client, admin_headers, unit_id)
    first_token = TOKEN_RE.search(mailer.sent[-1].body).group(1)  # type: ignore[union-attr]

    # An SMTP error whose text echoes the message (and so the token) must not leak either.
    class EchoingSMTPError(smtplib.SMTPDataError):
        pass

    mailer.fail_with = EchoingSMTPError(554, f"rejected: {first_token}".encode())
    resent = await client.post(f"{API}/resident-invitations/{created.json()['id']}/resend", headers=admin_headers)
    listed = await client.get(f"{API}/resident-invitations", params={"status": "PENDING"})
    mailer.fail_with = None
    await client.post(f"{API}/resident-invitations/{created.json()['id']}/resend", headers=admin_headers)
    tokens = [TOKEN_RE.search(e.body).group(1) for e in mailer.sent]  # type: ignore[union-attr]
    tokens.append(first_token)

    responses = created.text + resent.text + listed.text
    tables = ""
    for table in ("resident_invitations", "audit_logs", "users", "resident_units"):
        tables += str((await db_session.execute(text(f"SELECT * FROM {table}"))).all())
    for token in tokens:
        assert token not in responses
        assert token not in tables
        assert token not in caplog.text
    for forbidden in ("token", "token_hash", "password", "password_hash"):
        assert f'"{forbidden}"' not in responses
    assert "EchoingSMTPError" in caplog.text  # the failure is logged, by type only
