"""HU-003: real concurrency with independent connections and real commits."""

import asyncio
import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.exceptions import AppError, InvitationAlreadyPendingError, UnitInUseError, UnitNotAvailableError
from app.models import User, UserRole, UserStatus
from app.schemas.invitation import InvitationCreate, InvitationRead
from app.services import invitations as service
from app.services import structure as structure_service
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
HOLD_SECONDS = 0.5
TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]+)")
ORIGIN = get_settings().app_origin


@pytest.fixture
async def actor(committed_session_factory: Factory) -> Actor:
    async with committed_session_factory() as session:
        admin = User(
            email="admin@example.com",
            full_name="Ana María Pérez",
            role=UserRole.ADMINISTRADOR,
            status=UserStatus.ACTIVE,
            password_hash="not-a-real-hash",
        )
        session.add(admin)
        await session.commit()
        return Actor(user_id=admin.id, ip_address="127.0.0.1")


async def make_units(factory: Factory, actor: Actor, *numbers: int) -> list[int]:
    async with factory() as session:
        block = await structure_service.create_block(session, 1, actor)
        tower = await structure_service.create_tower(session, block.id, 2, actor)
        return [(await structure_service.create_unit(session, tower.id, n, actor)).id for n in numbers]


def data(unit_id: int, email: str = "residente@example.com") -> InvitationCreate:
    return InvitationCreate.model_validate(
        {"email": email, "full_name": "Laura Gómez", "unit_id": unit_id, "validity_days": 7}
    )


async def attempt(factory: Factory, operation: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
    async with factory() as session:
        try:
            return await operation(session, *args, **kwargs)
        except AppError as exc:
            return exc


async def scalar(factory: Factory, sql: str, **params: Any) -> Any:
    async with factory() as session:
        return await session.scalar(text(sql), params)


async def gather(*coroutines: Awaitable[Any]) -> list[Any]:
    return list(await asyncio.wait_for(asyncio.gather(*coroutines), timeout=30))


def wrap(monkeypatch: pytest.MonkeyPatch, module: Any, name: str, before: Any = None, after: Any = None) -> None:
    real = getattr(module, name)

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if before:
            await before()
        result = await real(*args, **kwargs)
        if after:
            await after()
        return result

    monkeypatch.setattr(module, name, wrapper)


# --- A. Two invitations for the same apartment ------------------------------------------------


async def test_two_invitations_for_the_same_unit(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    [unit_id] = await make_units(committed_session_factory, actor, 104)
    barrier = asyncio.Barrier(2)
    real_lock = service._lock_available_unit
    lock_errors: list[str] = []

    async def lock_together(db: AsyncSession, *args: Any) -> Any:
        await barrier.wait()  # both start the unit lock at the same time
        try:
            return await real_lock(db, *args)
        except AppError as exc:
            lock_errors.append(type(exc).__name__)
            raise

    monkeypatch.setattr(service, "_lock_available_unit", lock_together)
    mailer = FakeMailSender()

    results = await gather(
        attempt(committed_session_factory, service.create_invitation, data(unit_id, "uno@example.com"), actor, mailer=mailer, app_origin=ORIGIN),
        attempt(committed_session_factory, service.create_invitation, data(unit_id, "dos@example.com"), actor, mailer=mailer, app_origin=ORIGIN),
    )

    created = [r for r in results if isinstance(r, InvitationRead)]
    rejected = [r for r in results if isinstance(r, UnitNotAvailableError)]
    assert (len(created), len(rejected)) == (1, 1), results
    # The loser saw the winner's invitation right after getting the lock (fresh read).
    assert lock_errors == ["UnitNotAvailableError"]
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM resident_invitations") == 1
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM users WHERE role = 'RESIDENTE'") == 1
    assert len(mailer.sent) == 1


# --- B. Two invitations for the same email --------------------------------------------------------


async def test_two_invitations_for_the_same_email(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_unit, second_unit = await make_units(committed_session_factory, actor, 104, 105)
    barrier = asyncio.Barrier(2)
    calls = 0

    async def after_lock() -> None:
        nonlocal calls
        calls += 1
        if calls <= 2:
            await barrier.wait()  # both hold their unit and look for the user at the same time

    wrap(monkeypatch, service, "_lock_available_unit", after=after_lock)
    mailer = FakeMailSender()

    results = await gather(
        attempt(committed_session_factory, service.create_invitation, data(first_unit), actor, mailer=mailer, app_origin=ORIGIN),
        attempt(committed_session_factory, service.create_invitation, data(second_unit), actor, mailer=mailer, app_origin=ORIGIN),
    )

    created = [r for r in results if isinstance(r, InvitationRead)]
    rejected = [r for r in results if isinstance(r, InvitationAlreadyPendingError)]
    assert (len(created), len(rejected)) == (1, 1), results
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM users WHERE email = 'residente@example.com'") == 1
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM resident_invitations") == 1
    # The loser retried after the UNIQUE email conflict and its unit is free again.
    assert calls == 3
    async with committed_session_factory() as session:
        assert len((await service.list_available_units(session)).items) == 1


# --- C. Two resends of the same invitation ---------------------------------------------------------


async def test_two_resends_are_serialized_and_only_the_last_token_is_valid(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    [unit_id] = await make_units(committed_session_factory, actor, 104)
    mailer = FakeMailSender()
    created = await attempt(
        committed_session_factory, service.create_invitation, data(unit_id), actor, mailer=mailer, app_origin=ORIGIN
    )
    locked = asyncio.Event()

    async def hold_first_lock() -> None:
        if not locked.is_set():
            locked.set()
            await asyncio.sleep(HOLD_SECONDS)  # the first resend keeps the row locked

    wrap(monkeypatch, service, "_lock_invitation", after=hold_first_lock)

    async def second() -> tuple[Any, float]:
        await locked.wait()
        began = time.monotonic()
        result = await attempt(
            committed_session_factory, service.resend_invitation, created.id, actor, mailer=mailer, app_origin=ORIGIN
        )
        return result, time.monotonic() - began

    one, (two, waited) = await gather(
        attempt(committed_session_factory, service.resend_invitation, created.id, actor, mailer=mailer, app_origin=ORIGIN),
        second(),
    )

    assert isinstance(one, InvitationRead) and isinstance(two, InvitationRead)
    assert waited >= HOLD_SECONDS * 0.8  # serialized: the second waited for the first
    tokens = [TOKEN_RE.search(e.body).group(1) for e in mailer.sent]  # type: ignore[union-attr]
    assert len(tokens) == 3 and len(set(tokens)) == 3
    # Only the last committed rotation (the second resend, sent last) is valid.
    final_hash = await scalar(committed_session_factory, "SELECT token_hash FROM resident_invitations")
    assert final_hash == hashlib.sha256(tokens[-1].encode()).hexdigest()
    for old in tokens[:-1]:
        count = await scalar(
            committed_session_factory,
            "SELECT COUNT(*) FROM resident_invitations WHERE token_hash = :h",
            h=hashlib.sha256(old.encode()).hexdigest(),
        )
        assert count == 0
    assert await scalar(committed_session_factory, "SELECT send_attempts FROM resident_invitations") == 3
    assert await scalar(committed_session_factory, "SELECT delivery_status FROM resident_invitations") == "SENT"
    expires = await scalar(committed_session_factory, "SELECT expires_at FROM resident_invitations")
    assert expires.replace(tzinfo=created.expires_at.tzinfo) == created.expires_at  # not extended


# --- D. SMTP blocked does not block other apartments -------------------------------------------------


async def test_blocked_smtp_holds_no_lock(committed_session_factory: Factory, actor: Actor) -> None:
    first_unit, second_unit = await make_units(committed_session_factory, actor, 104, 105)
    slow = FakeMailSender()
    slow.gate = asyncio.Event()  # SMTP "hangs" until released
    fast = FakeMailSender()

    blocked = asyncio.create_task(
        attempt(committed_session_factory, service.create_invitation, data(first_unit, "uno@example.com"), actor, mailer=slow, app_origin=ORIGIN)
    )
    try:
        # Wait until the first invitation is committed and its email is "being sent".
        for _ in range(100):
            if await scalar(committed_session_factory, "SELECT COUNT(*) FROM resident_invitations") == 1:
                break
            await asyncio.sleep(0.02)

        started = time.monotonic()
        other = await asyncio.wait_for(
            attempt(committed_session_factory, service.create_invitation, data(second_unit, "dos@example.com"), actor, mailer=fast, app_origin=ORIGIN),
            timeout=5,
        )
        same_unit = await asyncio.wait_for(
            attempt(committed_session_factory, service.create_invitation, data(first_unit, "tres@example.com"), actor, mailer=fast, app_origin=ORIGIN),
            timeout=5,
        )
        elapsed = time.monotonic() - started

        assert isinstance(other, InvitationRead) and other.delivery_status == "SENT"
        # Even the same apartment answers at once (409): no lock is held during SMTP.
        assert isinstance(same_unit, UnitNotAvailableError)
        assert elapsed < 2
        assert not blocked.done()
        assert await scalar(
            committed_session_factory, "SELECT delivery_status FROM resident_invitations WHERE unit_id = :u", u=first_unit
        ) == "PENDING"
    finally:
        slow.gate.set()
        result = await asyncio.wait_for(blocked, timeout=10)
    assert isinstance(result, InvitationRead) and result.delivery_status == "SENT"


# --- HU-013 interaction: delete the apartment while it is being invited ----------------------------


async def test_delete_unit_while_inviting(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    [unit_id] = await make_units(committed_session_factory, actor, 104)
    locked = asyncio.Event()

    async def hold() -> None:
        locked.set()
        await asyncio.sleep(HOLD_SECONDS)  # invitation transaction keeps the unit locked

    wrap(monkeypatch, service, "_lock_available_unit", after=hold)

    async def delete_after_lock() -> tuple[Any, float]:
        await locked.wait()
        began = time.monotonic()
        result = await attempt(committed_session_factory, structure_service.delete_unit, unit_id, 1, actor)
        return result, time.monotonic() - began

    invited, (deleted, waited) = await gather(
        attempt(committed_session_factory, service.create_invitation, data(unit_id), actor, mailer=FakeMailSender(), app_origin=ORIGIN),
        delete_after_lock(),
    )

    assert isinstance(invited, InvitationRead)
    assert isinstance(deleted, UnitInUseError)
    assert waited >= HOLD_SECONDS * 0.8
    assert await scalar(committed_session_factory, "SELECT deleted_at FROM units WHERE id = :u", u=unit_id) is None
