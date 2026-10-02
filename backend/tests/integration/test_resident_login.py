"""HU-007: inicio de sesión del residente (same login, sessions and throttle as HU-002)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.exceptions import InvalidCredentialsError, LoginTemporarilyLockedError
from app.core.passwords import hash_password
from app.core.sessions import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, hash_token
from app.models import AuditAction, AuditLog, User, UserRole, UserSession, UserStatus
from app.schemas.auth import LoginRequest
from app.services.auth import authenticate
from tests.integration.conftest import PASSWORD

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]
RESIDENT = "residente@example.com"
GENERIC_401 = {
    "error": {"code": "INVALID_CREDENTIALS", "message": "Correo electrónico o contraseña no válidos.", "details": []}
}


async def login(client: AsyncClient, headers: dict[str, str], email: str = RESIDENT, password: str = PASSWORD, **extra: Any) -> Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password, **extra}, headers=headers)


@pytest.fixture
async def resident(make_user: MakeUser) -> User:
    """An ACTIVE resident with a password, as HU-019 leaves it."""
    return await make_user(RESIDENT, role=UserRole.RESIDENTE)


async def audit(db: AsyncSession, action: AuditAction) -> list[AuditLog]:
    return list((await db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))).all())


async def sessions(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(UserSession)) or 0)


# --- Criterion 1: successful login (A) ------------------------------------------------------


async def test_resident_logs_in(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, db_session: AsyncSession
) -> None:
    response = await login(client, origin_headers, email="  Residente@Example.COM ")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["user"] == {"id": resident.id, "full_name": "Ana María Pérez", "email": RESIDENT, "role": "RESIDENTE"}
    token = client.cookies[SESSION_COOKIE_NAME]
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=28800" in cookie and "path=/" in cookie
    [stored] = (await db_session.scalars(select(UserSession).where(UserSession.user_id == resident.id))).all()
    assert stored.token_hash == hash_token(token) and stored.revoked_at is None
    assert stored.expires_at - stored.last_seen_at == timedelta(hours=8)
    [entry] = await audit(db_session, AuditAction.LOGIN_SUCCEEDED)
    assert (entry.actor_user_id, entry.entity_type, entry.entity_id, entry.context) == (resident.id, "user", resident.id, None)


async def test_the_client_cannot_choose_the_role(client: AsyncClient, origin_headers: dict[str, str], resident: User) -> None:
    response = await login(client, origin_headers, role="ADMINISTRADOR")
    assert response.json()["user"]["role"] == "RESIDENTE"
    assert (await client.get("/api/v1/blocks")).status_code == 403


async def test_administrator_login_is_unchanged(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser) -> None:
    await make_user("admin@example.com")
    response = await login(client, origin_headers, email="admin@example.com")
    assert response.status_code == 200 and response.json()["user"]["role"] == "ADMINISTRADOR"
    assert (await client.get("/api/v1/blocks")).status_code == 200


# --- Criterion 2: failed login, indistinguishable (B-F) ---------------------------------------


CASES: list[tuple[str, dict[str, Any], dict[str, Any], str]] = [
    ("wrong-password", {}, {"password": "otra contraseña mala"}, "invalid_password"),
    ("unknown-email", {}, {"email": "nadie@example.com"}, "unknown_email"),
    ("pending-activation", {"status": UserStatus.PENDING_ACTIVATION, "password": None}, {}, "inactive"),
    ("pending-with-hash", {"status": UserStatus.PENDING_ACTIVATION}, {}, "inactive"),
    ("disabled", {"status": UserStatus.DISABLED}, {}, "inactive"),
    ("deleted", {"deleted": True}, {}, "deleted"),
]


@pytest.mark.parametrize(("fields", "credentials", "reason"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
async def test_failures_are_indistinguishable(
    client: AsyncClient,
    origin_headers: dict[str, str],
    make_user: MakeUser,
    db_session: AsyncSession,
    fields: dict[str, Any],
    credentials: dict[str, Any],
    reason: str,
) -> None:
    await make_user(RESIDENT, role=UserRole.RESIDENTE, **fields)

    response = await login(client, origin_headers, **credentials)

    assert response.status_code == 401
    assert response.json() == GENERIC_401
    assert "set-cookie" not in response.headers
    assert await sessions(db_session) == 0
    [entry] = await audit(db_session, AuditAction.LOGIN_FAILED)
    assert entry.context == {"reason": reason} and entry.actor_user_id is None


async def test_wrong_password_and_unknown_email_look_the_same(
    client: AsyncClient, origin_headers: dict[str, str], resident: User
) -> None:
    wrong = await login(client, origin_headers, password="otra contraseña mala")
    unknown = await login(client, origin_headers, email="nadie@example.com")
    other_unknown = await login(client, origin_headers, email="admin@example.com")

    assert wrong.status_code == unknown.status_code == other_unknown.status_code == 401
    assert wrong.json() == unknown.json() == other_unknown.json() == GENERIC_401
    assert dict(wrong.headers).keys() == dict(unknown.headers).keys()


# --- G. Same throttle as HU-002 ----------------------------------------------------------------


async def test_five_failures_lock_the_resident(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, db_session: AsyncSession
) -> None:
    for _ in range(5):
        assert (await login(client, origin_headers, password="otra contraseña mala")).status_code == 401

    locked = await login(client, origin_headers)  # even with the right password

    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "LOGIN_TEMPORARILY_LOCKED"
    assert int(locked.headers["retry-after"]) > 0
    assert await sessions(db_session) == 0
    assert len(await audit(db_session, AuditAction.LOGIN_LOCKED)) == 1


async def test_lock_is_per_email_and_ignores_case_and_spaces(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, make_user: MakeUser
) -> None:
    await make_user("admin@example.com")
    for _ in range(5):
        await login(client, origin_headers, email=" RESIDENTE@example.com", password="otra contraseña mala")

    assert (await login(client, origin_headers, email="Residente@Example.com")).status_code == 429
    # The administrator (another email) is not affected.
    assert (await login(client, origin_headers, email="admin@example.com")).status_code == 200


async def test_accent_variant_is_another_identity(client: AsyncClient, origin_headers: dict[str, str], resident: User) -> None:
    # A-1: no bypass through Unicode variants of the email (they never match the account).
    response = await login(client, origin_headers, email="résidente@example.com")
    assert response.status_code == 401 and response.json() == GENERIC_401


async def test_successful_login_resets_the_counter(
    client: AsyncClient, origin_headers: dict[str, str], resident: User
) -> None:
    for _ in range(4):
        await login(client, origin_headers, password="otra contraseña mala")
    assert (await login(client, origin_headers)).status_code == 200
    for _ in range(4):
        await login(client, origin_headers, password="otra contraseña mala")
    assert (await login(client, origin_headers)).status_code == 200


async def test_concurrent_failures_against_a_real_resident(
    committed_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Eight simultaneous wrong attempts on separate connections: exactly five are
    evaluated, three are locked, and the right password cannot slip through."""
    async with committed_session_factory() as session:
        session.add(
            User(email=RESIDENT, full_name="Laura Gómez", role=UserRole.RESIDENTE, status=UserStatus.ACTIVE,
                 password_hash=hash_password(PASSWORD))
        )
        await session.commit()
    settings = get_settings()
    wrong = LoginRequest.model_validate({"email": RESIDENT, "password": "otra contraseña mala"})
    right = LoginRequest.model_validate({"email": RESIDENT, "password": PASSWORD})

    async def attempt(data: LoginRequest) -> type[Exception] | str:
        async with committed_session_factory() as session:
            try:
                await authenticate(session, data, settings=settings, ip_address=None, previous_token=None)
            except (InvalidCredentialsError, LoginTemporarilyLockedError) as exc:
                return type(exc)
            return "logged-in"

    results = await asyncio.wait_for(asyncio.gather(*(attempt(wrong) for _ in range(8))), timeout=60)
    after = await attempt(right)

    assert results.count(InvalidCredentialsError) == 5
    assert results.count(LoginTemporarilyLockedError) == 3
    assert after is LoginTemporarilyLockedError
    async with committed_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserSession)) == 0


# --- I. Session and logout -------------------------------------------------------------------


async def test_session_and_logout(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, db_session: AsyncSession
) -> None:
    csrf = (await login(client, origin_headers)).json()["csrf_token"]

    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["user"]["role"] == "RESIDENTE" and session.json()["csrf_token"] == csrf

    logout = await client.post("/api/v1/auth/logout", headers={**origin_headers, CSRF_HEADER_NAME: csrf})

    assert logout.status_code == 204
    assert (await client.get("/api/v1/auth/session")).status_code == 401
    [stored] = (await db_session.scalars(select(UserSession))).all()
    assert stored.revoked_at is not None
    [entry] = await audit(db_session, AuditAction.LOGOUT)
    assert entry.actor_user_id == resident.id


async def test_idle_resident_session_expires(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, db_session: AsyncSession
) -> None:
    await login(client, origin_headers)
    await db_session.execute(text("UPDATE sessions SET last_seen_at = UTC_TIMESTAMP(6) - INTERVAL 31 MINUTE"))
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_disabling_the_resident_ends_the_session(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, db_session: AsyncSession
) -> None:
    await login(client, origin_headers)
    # ORM update (as in HU-002's tests): the test shares one session with the app.
    await db_session.execute(update(User).where(User.id == resident.id).values(status=UserStatus.DISABLED))
    assert (await client.get("/api/v1/auth/session")).status_code == 401


# --- J. Role separation ------------------------------------------------------------------------


ADMIN_ONLY: list[tuple[str, str, Any]] = [
    ("GET", "/api/v1/blocks", None),
    ("POST", "/api/v1/blocks", {"number": 1}),
    ("GET", "/api/v1/towers/1/units", None),
    ("DELETE", "/api/v1/units/1?version=1", None),
    ("GET", "/api/v1/units/available", None),
    ("GET", "/api/v1/resident-invitations?status=PENDING", None),
    ("POST", "/api/v1/resident-invitations", {"email": "x@example.com", "full_name": "X Y Z", "unit_id": 1, "validity_days": 7}),
    ("POST", "/api/v1/resident-invitations/1/resend", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ONLY, ids=[f"{m} {p}" for m, p, _ in ADMIN_ONLY])
async def test_resident_cannot_use_administrator_endpoints(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, method: str, path: str, body: Any
) -> None:
    csrf = (await login(client, origin_headers)).json()["csrf_token"]

    response = await client.request(method, path, json=body, headers={**origin_headers, CSRF_HEADER_NAME: csrf})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- K. No secrets in responses, logs or audit --------------------------------------------------


async def test_no_secrets_leak(
    client: AsyncClient,
    origin_headers: dict[str, str],
    resident: User,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    failed = await login(client, origin_headers, password="otra contraseña mala")
    ok = await login(client, origin_headers)
    token = client.cookies[SESSION_COOKIE_NAME]
    csrf = ok.json()["csrf_token"]
    session = await client.get("/api/v1/auth/session")
    await client.post("/api/v1/auth/logout", headers={**origin_headers, CSRF_HEADER_NAME: csrf})

    audit_dump = str((await db_session.execute(text("SELECT action, context, entity_type FROM audit_logs"))).all())
    responses = failed.text + ok.text + session.text
    for secret in (PASSWORD, "otra contraseña mala", token, hash_token(token), resident.password_hash or ""):
        assert secret not in caplog.text
        assert secret not in audit_dump
    for secret in (PASSWORD, "otra contraseña mala", token, hash_token(token), resident.password_hash or "", "password_hash"):
        assert secret not in responses
