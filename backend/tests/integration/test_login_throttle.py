"""HU-002, criterion 3: temporary lockout after repeated failures."""

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.exceptions import InvalidCredentialsError, LoginTemporarilyLockedError
from app.models import AuditAction, AuditLog, LoginThrottle, User
from app.schemas.auth import LoginRequest
from app.services.auth import authenticate
from app.services.login_throttle import identifier_hash
from tests.integration.conftest import PASSWORD

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]


async def login(client: AsyncClient, headers: dict[str, str], email: str = "admin@example.com", password: str = PASSWORD) -> Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=headers)


async def fail_times(client: AsyncClient, headers: dict[str, str], times: int, email: str = "admin@example.com") -> list[Response]:
    return [await login(client, headers, email=email, password="wrong password") for _ in range(times)]


async def test_five_failures_lock_the_sixth_attempt_even_with_correct_password(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user()

    failures = await fail_times(client, origin_headers, 5)
    sixth = await login(client, origin_headers)  # correct password

    assert [r.status_code for r in failures] == [401] * 5
    assert sixth.status_code == 429
    assert sixth.json()["error"]["code"] == "LOGIN_TEMPORARILY_LOCKED"
    assert sixth.json()["error"]["message"] == (
        "Demasiados intentos fallidos. El acceso está bloqueado temporalmente; intenta de nuevo en 15 minutos."
    )
    assert 14 * 60 < int(sixth.headers["retry-after"]) <= 15 * 60
    assert "set-cookie" not in sixth.headers
    locked = await db_session.scalars(select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_LOCKED))
    assert [entry.context for entry in locked] == [{"failed_attempts": 5}]


async def test_four_failures_do_not_lock(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser) -> None:
    await make_user()
    await fail_times(client, origin_headers, 4)

    assert (await login(client, origin_headers)).status_code == 200


async def test_unknown_email_is_locked_exactly_like_a_real_account(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser
) -> None:
    await make_user()
    await fail_times(client, origin_headers, 5, email="admin@example.com")
    await fail_times(client, origin_headers, 5, email="nobody@example.com")

    real = await login(client, origin_headers, email="admin@example.com")
    unknown = await login(client, origin_headers, email="nobody@example.com")

    assert real.status_code == unknown.status_code == 429
    assert real.json() == unknown.json()


async def test_lock_applies_to_the_normalized_email(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser) -> None:
    await make_user()
    await fail_times(client, origin_headers, 5, email="ADMIN@example.com ")

    assert (await login(client, origin_headers, email="admin@example.com")).status_code == 429


async def test_other_emails_are_not_affected(client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser) -> None:
    await make_user(email="admin@example.com")
    await fail_times(client, origin_headers, 5, email="other@example.com")

    assert (await login(client, origin_headers, email="admin@example.com")).status_code == 200


async def test_login_works_again_after_the_lock_expires(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user()
    await fail_times(client, origin_headers, 5)
    await db_session.execute(
        update(LoginThrottle)
        .where(LoginThrottle.identifier_hash == identifier_hash("admin@example.com"))
        .values(locked_until=text("UTC_TIMESTAMP(6) - INTERVAL 1 SECOND"))
    )

    assert (await login(client, origin_headers)).status_code == 200


async def test_successful_login_resets_the_counter(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user()
    await fail_times(client, origin_headers, 4)

    assert (await login(client, origin_headers)).status_code == 200
    assert await db_session.get(LoginThrottle, identifier_hash("admin@example.com")) is None
    # A fresh window: four new failures still do not lock.
    await fail_times(client, origin_headers, 4)
    assert (await login(client, origin_headers)).status_code == 200


async def test_locked_attempts_are_audited(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user()
    await fail_times(client, origin_headers, 6)

    reasons = [e.context["reason"] for e in await db_session.scalars(select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_FAILED).order_by(AuditLog.id))]
    assert reasons == ["invalid_password"] * 5 + ["locked"]


async def test_concurrent_failures_are_all_counted_and_never_exceed_the_limit(
    committed_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Eight simultaneous wrong attempts on separate connections.

    The row lock serializes them: exactly five are evaluated (the fifth sets the
    lock) and the other three are rejected as locked. No attempt slips through.
    """
    settings = get_settings()
    data = LoginRequest.model_validate({"email": "nobody@example.com", "password": "wrong password"})

    async def attempt() -> type[Exception]:
        async with committed_session_factory() as session:
            try:
                await authenticate(session, data, settings=settings, ip_address=None, previous_token=None)
            except (InvalidCredentialsError, LoginTemporarilyLockedError) as exc:
                return type(exc)
        raise AssertionError("a wrong password must never log in")

    results = await asyncio.gather(*(attempt() for _ in range(8)))

    assert results.count(InvalidCredentialsError) == 5
    assert results.count(LoginTemporarilyLockedError) == 3
    async with committed_session_factory() as session:
        throttle = await session.get(LoginThrottle, identifier_hash("nobody@example.com"))
    assert throttle is not None
    assert throttle.failed_attempts == 5
    assert throttle.locked_until is not None
