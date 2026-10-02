"""HU-016: real concurrency (independent connections, real commits)."""

import asyncio
import time
from collections.abc import Awaitable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.exceptions import AppError, InvalidCredentialsError, PasswordResetRevokedError, PasswordResetUsedError
from app.core.passwords import hash_password, verify_password
from app.core.sessions import hash_token
from app.models import User, UserRole, UserStatus
from app.schemas.auth import LoginRequest
from app.schemas.password_reset import PasswordResetConfirm
from app.services import auth as auth_service
from app.services import password_reset as service
from app.services.auth import LoginResult
from app.services.sessions import resolve_session

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
HOLD_SECONDS = 0.5
RESIDENT = "residente@example.com"
OLD_PASSWORD = "la clave anterior 2025"


@pytest.fixture
async def resident_id(committed_session_factory: Factory) -> int:
    async with committed_session_factory() as session:
        user = User(email=RESIDENT, full_name="Laura Gómez", role=UserRole.RESIDENTE, status=UserStatus.ACTIVE,
                    password_hash=hash_password(OLD_PASSWORD))
        session.add(user)
        await session.commit()
        return user.id


async def attempt(factory: Factory, operation: Any, *args: Any, **kwargs: Any) -> Any:
    async with factory() as session:
        try:
            return await operation(session, *args, **kwargs)
        except AppError as exc:
            return exc


async def new_token(factory: Factory) -> str:
    delivery = await attempt(factory, service.request_reset, RESIDENT, ip_address=None)
    assert isinstance(delivery, service.ResetDelivery)
    return delivery.token


def confirmation(token: str, password: str) -> PasswordResetConfirm:
    return PasswordResetConfirm.model_validate({"token": token, "password": password, "password_confirmation": password})


async def confirm(factory: Factory, token: str, password: str) -> Any:
    return await attempt(factory, service.confirm_reset, confirmation(token, password), ip_address=None)


async def login(factory: Factory, password: str) -> Any:
    data = LoginRequest.model_validate({"email": RESIDENT, "password": password})
    return await attempt(factory, auth_service.authenticate, data, settings=get_settings(), ip_address=None, previous_token=None)


async def scalar(factory: Factory, sql: str, **params: Any) -> Any:
    async with factory() as session:
        return await session.scalar(text(sql), params)


async def gather(*coroutines: Awaitable[Any]) -> list[Any]:
    return list(await asyncio.wait_for(asyncio.gather(*coroutines), timeout=60))


def before_lock(monkeypatch: pytest.MonkeyPatch, barrier: asyncio.Barrier) -> None:
    real = service._lock_user

    async def wrapper(*args: Any) -> Any:
        await barrier.wait()
        return await real(*args)

    monkeypatch.setattr(service, "_lock_user", wrapper)


async def test_same_token_twice_at_the_same_time(
    committed_session_factory: Factory, resident_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await new_token(committed_session_factory)
    before_lock(monkeypatch, asyncio.Barrier(2))  # both passed the early checks and hashed

    results = await gather(
        confirm(committed_session_factory, token, "primera frase nueva 1"),
        confirm(committed_session_factory, token, "segunda frase nueva 2"),
    )

    assert sorted(type(r).__name__ for r in results) == ["NoneType", "PasswordResetUsedError"], results
    winner = "primera frase nueva 1" if results[0] is None else "segunda frase nueva 2"
    loser = "segunda frase nueva 2" if results[0] is None else "primera frase nueva 1"
    stored = await scalar(committed_session_factory, "SELECT password_hash FROM users WHERE id = :u", u=resident_id)
    assert verify_password(stored, winner) and not verify_password(stored, loser)
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM password_reset_tokens WHERE used_at IS NOT NULL") == 1


async def test_two_requests_at_the_same_time(
    committed_session_factory: Factory, resident_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    before_lock(monkeypatch, asyncio.Barrier(2))

    deliveries = await gather(
        attempt(committed_session_factory, service.request_reset, RESIDENT, ip_address=None),
        attempt(committed_session_factory, service.request_reset, RESIDENT, ip_address=None),
    )

    assert all(isinstance(d, service.ResetDelivery) for d in deliveries)
    pending = await scalar(
        committed_session_factory,
        "SELECT token_hash FROM password_reset_tokens WHERE used_at IS NULL AND revoked_at IS NULL",
    )
    hashes = {hash_token(d.token) for d in deliveries}
    assert pending in hashes  # exactly one pending link: the last committed one
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM password_reset_tokens WHERE revoked_at IS NOT NULL") == 1


async def test_login_first_then_reset_revokes_that_session(
    committed_session_factory: Factory, resident_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await new_token(committed_session_factory)
    locked = asyncio.Event()
    real_verify = auth_service.verify_password_async

    async def slow_verify(*args: Any) -> bool:
        locked.set()  # the login already holds the user row
        await asyncio.sleep(HOLD_SECONDS)
        return await real_verify(*args)

    monkeypatch.setattr(auth_service, "verify_password_async", slow_verify)

    async def reset_after_login_locks() -> tuple[Any, float]:
        await locked.wait()
        began = time.monotonic()
        return await confirm(committed_session_factory, token, "una frase nueva 2026"), time.monotonic() - began

    logged_in, (reset, waited) = await gather(login(committed_session_factory, OLD_PASSWORD), reset_after_login_locks())

    assert isinstance(logged_in, LoginResult) and reset is None
    assert waited >= HOLD_SECONDS * 0.8  # the reset waited for the login (serialized)
    # The session created with the old password did not survive the reset.
    revoked = await scalar(committed_session_factory, "SELECT revoked_at FROM sessions WHERE token_hash = :h", h=hash_token(logged_in.token))
    assert revoked is not None
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM sessions WHERE user_id = :u AND revoked_at IS NULL", u=resident_id) == 0


async def test_reset_first_then_login_with_the_old_password_fails(
    committed_session_factory: Factory, resident_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await new_token(committed_session_factory)
    locked = asyncio.Event()
    real_lock = service._lock_user

    async def hold(*args: Any) -> Any:
        result = await real_lock(*args)
        locked.set()  # the reset holds the user row
        await asyncio.sleep(HOLD_SECONDS)
        return result

    monkeypatch.setattr(service, "_lock_user", hold)

    async def login_after_reset_locks() -> tuple[Any, float]:
        await locked.wait()
        began = time.monotonic()
        return await login(committed_session_factory, OLD_PASSWORD), time.monotonic() - began

    reset, (logged_in, waited) = await gather(confirm(committed_session_factory, token, "una frase nueva 2026"), login_after_reset_locks())

    assert reset is None
    assert isinstance(logged_in, InvalidCredentialsError)  # it saw the new password
    assert waited >= HOLD_SECONDS * 0.8
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM sessions WHERE user_id = :u", u=resident_id) == 0


async def test_reset_and_logout_at_the_same_time(committed_session_factory: Factory, resident_id: int) -> None:
    logged_in = await login(committed_session_factory, OLD_PASSWORD)
    assert isinstance(logged_in, LoginResult)
    token = await new_token(committed_session_factory)

    async def logout() -> None:
        async with committed_session_factory() as session:
            auth = await resolve_session(session, logged_in.token, get_settings())
            assert auth is not None
            await auth_service.logout(session, auth, ip_address=None)

    results = await gather(logout(), confirm(committed_session_factory, token, "una frase nueva 2026"))

    assert results == [None, None]
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM sessions WHERE revoked_at IS NULL") == 0


async def test_request_and_confirm_never_deadlock(
    committed_session_factory: Factory, resident_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both paths lock in the same order (user → token): whatever wins, no deadlock (1213).
    for round_ in range(5):
        token = await new_token(committed_session_factory)
        before_lock(monkeypatch, asyncio.Barrier(2))
        try:
            reset, delivery = await gather(
                confirm(committed_session_factory, token, f"frase nueva numero {round_}"),
                attempt(committed_session_factory, service.request_reset, RESIDENT, ip_address=None),
            )
        except DBAPIError as exc:
            pytest.fail(f"deadlock or database error: {exc.orig}")
        monkeypatch.undo()
        # Either the reset won (and the request created a new link), or the request
        # won first and revoked the link the reset was using.
        assert reset is None or isinstance(reset, PasswordResetRevokedError), reset
        assert isinstance(delivery, service.ResetDelivery), delivery
        # Keep the rate limit (3 per hour) out of the way for the next round.
        async with committed_session_factory() as session:
            await session.execute(text(
                "UPDATE password_reset_tokens SET created_at = created_at - INTERVAL 2 HOUR, "
                "expires_at = expires_at - INTERVAL 2 HOUR"
            ))
            await session.commit()


async def test_old_link_after_a_new_request(committed_session_factory: Factory, resident_id: int) -> None:
    first = await new_token(committed_session_factory)
    second = await new_token(committed_session_factory)

    assert isinstance(await confirm(committed_session_factory, first, "una frase nueva 2026"), PasswordResetRevokedError)
    assert await confirm(committed_session_factory, second, "una frase nueva 2026") is None
    assert isinstance(await confirm(committed_session_factory, second, "otra frase nueva 2026"), PasswordResetUsedError)
