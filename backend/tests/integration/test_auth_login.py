"""HU-002: POST /api/v1/auth/login."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from argon2 import PasswordHasher
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import needs_rehash, verify_password
from app.core.sessions import SESSION_COOKIE_NAME, derive_csrf_token, hash_token
from app.models import AuditAction, AuditLog, User, UserRole, UserSession, UserStatus
from tests.integration.conftest import PASSWORD

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]
GENERIC_401 = {
    "error": {"code": "INVALID_CREDENTIALS", "message": "Correo electrónico o contraseña no válidos.", "details": []}
}


async def login(client: AsyncClient, headers: dict[str, str], email: str = "admin@example.com", password: str = PASSWORD) -> Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=headers)


def set_cookie_header(response: Response) -> str:
    cookies = [c for c in response.headers.get_list("set-cookie") if c.startswith(f"{SESSION_COOKIE_NAME}=")]
    assert len(cookies) == 1
    return cookies[0]


async def audit_entries(db: AsyncSession, action: AuditAction) -> list[AuditLog]:
    return list((await db.scalars(select(AuditLog).where(AuditLog.action == action))).all())


# --- criterion 1: successful login ----------------------------------------------


async def test_login_succeeds_and_returns_role_and_csrf(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser) -> None:
    user = await make_user(email="admin@example.com")

    response = await login(client, origin_headers, email="  Admin@Example.com ")

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {"id": user.id, "full_name": "Ana María Pérez", "email": "admin@example.com", "role": "ADMINISTRADOR"}
    token = client.cookies[SESSION_COOKIE_NAME]
    assert body["csrf_token"] == derive_csrf_token(token)
    assert response.headers["cache-control"] == "no-store"


async def test_session_cookie_attributes(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser) -> None:
    await make_user()

    cookie = set_cookie_header(await login(client, origin_headers))

    attributes = {part.strip().split("=")[0].lower() for part in cookie.split(";")[1:]}
    assert "httponly" in attributes
    assert "samesite=lax" in cookie.lower()
    assert "path=/" in cookie.lower()
    assert "max-age=28800" in cookie.lower()  # 8 hours
    assert "domain" not in attributes
    assert "secure" not in attributes  # SESSION_COOKIE_SECURE=false in local http


async def test_database_stores_only_the_token_hash(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession) -> None:
    user = await make_user()

    await login(client, origin_headers)

    token = client.cookies[SESSION_COOKIE_NAME]
    stored = (await db_session.scalars(select(UserSession).where(UserSession.user_id == user.id))).one()
    assert stored.token_hash == hash_token(token)
    assert stored.token_hash != token
    assert stored.revoked_at is None
    assert (stored.expires_at - stored.last_seen_at).total_seconds() == 8 * 3600


async def test_successful_login_is_audited(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession) -> None:
    user = await make_user()

    await login(client, origin_headers)

    [entry] = await audit_entries(db_session, AuditAction.LOGIN_SUCCEEDED)
    assert entry.actor_user_id == user.id
    assert entry.entity_id == user.id
    assert entry.ip_address == "127.0.0.1"


# --- criterion 2: failed login, indistinguishable ---------------------------------


FAILURE_CASES: list[tuple[str, dict[str, Any], dict[str, Any], str]] = [
    ("wrong-password", {}, {"password": "wrong password here"}, "invalid_password"),
    ("unknown-email", {}, {"email": "nobody@example.com"}, "unknown_email"),
    ("disabled", {"status": UserStatus.DISABLED}, {}, "inactive"),
    ("pending-activation", {"status": UserStatus.PENDING_ACTIVATION, "password": None}, {}, "inactive"),
    ("soft-deleted", {"deleted": True, "status": UserStatus.DISABLED}, {}, "deleted"),
    # Until HU-007 an ACTIVE resident with the right password was rejected here
    # ("role_not_allowed"). HU-007 allows residents: see test_resident_login.py.
    ("pending-resident", {"role": UserRole.RESIDENTE, "status": UserStatus.PENDING_ACTIVATION, "password": None}, {}, "inactive"),
]


@pytest.mark.parametrize(("user_fields", "credentials", "reason"), [c[1:] for c in FAILURE_CASES], ids=[c[0] for c in FAILURE_CASES])
async def test_failures_are_indistinguishable(
    client: AsyncClient,
    origin_headers: dict[str, str],
    make_user: MakeUser,
    db_session: AsyncSession,
    user_fields: dict[str, Any],
    credentials: dict[str, Any],
    reason: str,
) -> None:
    await make_user(**user_fields)

    response = await login(client, origin_headers, **credentials)

    assert response.status_code == 401
    assert response.json() == GENERIC_401
    assert "set-cookie" not in response.headers
    assert await db_session.scalar(select(func.count()).select_from(UserSession)) == 0
    [entry] = await audit_entries(db_session, AuditAction.LOGIN_FAILED)
    assert entry.context == {"reason": reason}
    assert entry.actor_user_id is None


async def test_wrong_password_with_valid_account_increments_nothing_visible(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser) -> None:
    await make_user()

    first = await login(client, origin_headers, password="wrong password 1")
    second = await login(client, origin_headers, email="nobody@example.com", password="wrong password 2")

    assert first.status_code == second.status_code == 401
    assert first.json() == second.json()
    assert dict(first.headers).keys() == dict(second.headers).keys()


# --- validation and Origin ---------------------------------------------------------


async def test_missing_fields_use_standard_validation_format(client: AsyncClient, origin_headers: dict[str, str]) -> None:
    response = await client.post("/api/v1/auth/login", json={}, headers=origin_headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert {d["field"] for d in response.json()["error"]["details"]} == {"email", "password"}


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": "https://evil.example"}, {"Origin": "http://127.0.0.1:4321"}, {"Referer": "https://evil.example/login"}],
    ids=["no-origin", "foreign-origin", "other-host", "foreign-referer"],
)
async def test_login_requires_the_public_origin(client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, headers: dict[str, str]) -> None:
    await make_user()

    response = await login(client, headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
    assert await db_session.scalar(select(func.count()).select_from(UserSession)) == 0


async def test_referer_is_accepted_when_origin_is_missing(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user()

    response = await login(client, {"Referer": "http://localhost:4321/login"})

    assert response.status_code == 200


# --- password rehash and session fixation -------------------------------------------


async def test_login_rehashes_password_with_old_parameters(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession) -> None:
    weak_hash = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSWORD)
    user = await make_user(password_hash=weak_hash)

    assert (await login(client, origin_headers)).status_code == 200

    await db_session.refresh(user)
    assert user.password_hash != weak_hash
    assert not needs_rehash(user.password_hash)
    assert verify_password(user.password_hash, PASSWORD)


async def test_new_login_revokes_previous_session_from_cookie(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession) -> None:
    await make_user()
    await login(client, origin_headers)
    first_token = client.cookies[SESSION_COOKIE_NAME]

    await login(client, origin_headers)
    second_token = client.cookies[SESSION_COOKIE_NAME]

    assert second_token != first_token
    first = await db_session.scalar(select(UserSession).where(UserSession.token_hash == hash_token(first_token)))
    second = await db_session.scalar(select(UserSession).where(UserSession.token_hash == hash_token(second_token)))
    assert first is not None and first.revoked_at is not None
    assert second is not None and second.revoked_at is None


async def test_attacker_planted_cookie_is_not_reused(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser) -> None:
    await make_user()
    planted = "attacker-chosen-session-id"
    client.cookies.set(SESSION_COOKIE_NAME, planted)

    response = await login(client, origin_headers)

    issued = set_cookie_header(response).split(";")[0].split("=", 1)[1]
    assert issued != planted
    assert len(issued) >= 43
