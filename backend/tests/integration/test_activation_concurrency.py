"""HU-019: real concurrency of the activation (independent connections, real commits)."""

import asyncio
import re
import time
from collections.abc import Awaitable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.exceptions import (
    AppError,
    InvitationInvalidError,
    InvitationNotPendingError,
    InvitationUsedError,
    UnitInUseError,
    UnitNotAvailableError,
)
from app.core.passwords import verify_password
from app.models import User, UserRole, UserStatus
from app.schemas.activation import ResidentActivation
from app.schemas.invitation import InvitationCreate
from app.services import activation as service
from app.services import invitations as invitation_service
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
            email="admin@example.com", full_name="Ana María Pérez", role=UserRole.ADMINISTRADOR,
            status=UserStatus.ACTIVE, password_hash="not-a-real-hash",
        )
        session.add(admin)
        await session.commit()
        return Actor(user_id=admin.id, ip_address="127.0.0.1")


async def make_unit(factory: Factory, actor: Actor, number: int) -> int:
    async with factory() as session:
        blocks = (await structure_service.list_blocks(session)).items
        block = blocks[0] if blocks else await structure_service.create_block(session, 1, actor)
        towers = (await structure_service.list_towers(session, block.id)).items
        tower = towers[0] if towers else await structure_service.create_tower(session, block.id, 1, actor)
        return (await structure_service.create_unit(session, tower.id, number, actor)).id


async def invite(factory: Factory, actor: Actor, unit_id: int, email: str = "residente@example.com") -> tuple[int, str]:
    mailer = FakeMailSender()
    data = InvitationCreate.model_validate({"email": email, "full_name": "Laura Gómez", "unit_id": unit_id, "validity_days": 7})
    async with factory() as session:
        created = await invitation_service.create_invitation(session, data, actor, mailer=mailer, app_origin=ORIGIN)
    return created.id, TOKEN_RE.search(mailer.sent[-1].body).group(1)  # type: ignore[union-attr]


def activation(token: str, document: str = "CC1234567", password: str = "una clave segura 2026") -> ResidentActivation:
    return ResidentActivation.model_validate(
        {"token": token, "document_number": document, "password": password, "password_confirmation": password}
    )


async def attempt(factory: Factory, operation: Any, *args: Any, **kwargs: Any) -> Any:
    async with factory() as session:
        try:
            return await operation(session, *args, **kwargs)
        except AppError as exc:
            return exc


async def activate(factory: Factory, data: ResidentActivation) -> Any:
    return await attempt(factory, service.activate_account, data, ip_address="127.0.0.1")


async def scalar(factory: Factory, sql: str, **params: Any) -> Any:
    async with factory() as session:
        return await session.scalar(text(sql), params)


async def gather(*coroutines: Awaitable[Any]) -> list[Any]:
    return list(await asyncio.wait_for(asyncio.gather(*coroutines), timeout=30))


def wrap_lock(monkeypatch: pytest.MonkeyPatch, *, before: Any = None, after: Any = None) -> None:
    real = service._lock_activation_rows

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if before:
            await before()
        result = await real(*args, **kwargs)
        if after:
            await after()
        return result

    monkeypatch.setattr(service, "_lock_activation_rows", wrapper)


# --- A. Two activations of the same token at the same time ------------------------------------


async def test_same_token_twice_at_the_same_time(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit_id = await make_unit(committed_session_factory, actor, 101)
    invitation_id, token = await invite(committed_session_factory, actor, unit_id)
    barrier = asyncio.Barrier(2)
    wrap_lock(monkeypatch, before=barrier.wait)  # both passed the early checks and hashed

    results = await gather(
        activate(committed_session_factory, activation(token, "CC1111111", "primera clave 2026")),
        activate(committed_session_factory, activation(token, "CC2222222", "segunda clave 2026")),
    )

    assert sorted(type(r).__name__ for r in results) == ["InvitationUsedError", "NoneType"], results
    winner = 0 if results[0] is None else 1
    password, document = [("primera clave 2026", "CC1111111"), ("segunda clave 2026", "CC2222222")][winner]
    stored = await scalar(committed_session_factory, "SELECT password_hash FROM users WHERE email = 'residente@example.com'")
    assert verify_password(stored, password)
    assert not verify_password(stored, ["segunda clave 2026", "primera clave 2026"][winner])  # one password only
    assert await scalar(committed_session_factory, "SELECT document_number FROM users WHERE email = 'residente@example.com'") == document
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM resident_units") == 1
    assert await scalar(committed_session_factory, "SELECT status FROM resident_invitations WHERE id = :i", i=invitation_id) == "USED"
    actions = await scalar(
        committed_session_factory,
        "SELECT GROUP_CONCAT(action ORDER BY id) FROM audit_logs "
        "WHERE action IN ('RESIDENT_ACTIVATED', 'INVITATION_REJECTED')",
    )
    assert sorted(actions.split(",")) == ["INVITATION_REJECTED", "RESIDENT_ACTIVATED"]


# --- C. A second activation after the first one committed -------------------------------------------


async def test_second_activation_after_commit(committed_session_factory: Factory, actor: Actor) -> None:
    unit_id = await make_unit(committed_session_factory, actor, 101)
    _, token = await invite(committed_session_factory, actor, unit_id)

    first = await activate(committed_session_factory, activation(token))
    second = await activate(committed_session_factory, activation(token, "CC9999999", "otra clave segura 9"))

    assert first is None
    assert isinstance(second, InvitationUsedError)
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM resident_units") == 1
    stored = await scalar(committed_session_factory, "SELECT password_hash FROM users WHERE email = 'residente@example.com'")
    assert verify_password(stored, "una clave segura 2026")


# --- B. Different residents for the same apartment ----------------------------------------------------


async def insert_other_residence(session: AsyncSession, unit_id: int, *, lock_unit: bool) -> None:
    """Another process integrating a different resident into the same apartment (simulated)."""
    if lock_unit:  # follows the protocol: unit first, then check
        await session.execute(text("SELECT id FROM units WHERE id = :u FOR UPDATE"), {"u": unit_id})
        occupied = await session.scalar(
            text("SELECT COUNT(*) FROM resident_units WHERE unit_id = :u AND ended_at IS NULL"), {"u": unit_id}
        )
        if occupied:
            raise UnitNotAvailableError()
    await session.execute(
        text(
            "INSERT INTO users (email, full_name, role, status, password_hash) "
            "VALUES ('vecino@example.com', 'Otro Vecino', 'RESIDENTE', 'ACTIVE', 'not-a-real-hash')"
        )
    )
    await session.execute(
        text(
            "INSERT INTO resident_invitations (token_hash, user_id, unit_id, invited_by_user_id, validity_days, "
            "status, created_at, expires_at, used_at) SELECT REPEAT('e', 64), id, :u, id, 7, 'USED', "
            "UTC_TIMESTAMP(6), UTC_TIMESTAMP(6) + INTERVAL 1 DAY, UTC_TIMESTAMP(6) FROM users WHERE email = 'vecino@example.com'"
        ),
        {"u": unit_id},
    )
    await session.execute(
        text(
            "INSERT INTO resident_units (user_id, unit_id, invitation_id, started_at) "
            "SELECT user_id, unit_id, id, UTC_TIMESTAMP(6) FROM resident_invitations WHERE token_hash = REPEAT('e', 64)"
        )
    )


async def test_other_resident_takes_the_unit_first(committed_session_factory: Factory, actor: Actor) -> None:
    unit_id = await make_unit(committed_session_factory, actor, 101)
    invitation_id, token = await invite(committed_session_factory, actor, unit_id)
    locked = asyncio.Event()

    async def other_process() -> None:
        async with committed_session_factory() as session:
            await insert_other_residence(session, unit_id, lock_unit=True)
            locked.set()
            await asyncio.sleep(HOLD_SECONDS)  # keeps the unit locked
            await session.commit()

    async def activation_after_lock() -> tuple[Any, float]:
        await locked.wait()
        began = time.monotonic()
        return await activate(committed_session_factory, activation(token)), time.monotonic() - began

    _, (result, waited) = await gather(other_process(), activation_after_lock())

    assert isinstance(result, UnitNotAvailableError)
    assert waited >= HOLD_SECONDS * 0.8  # the activation waited for the unit lock
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM resident_units WHERE unit_id = :u AND ended_at IS NULL", u=unit_id) == 1
    # Nothing partial: the invited resident stays pending and the invitation PENDING.
    assert await scalar(committed_session_factory, "SELECT status FROM users WHERE email = 'residente@example.com'") == "PENDING_ACTIVATION"
    assert await scalar(committed_session_factory, "SELECT status FROM resident_invitations WHERE id = :i", i=invitation_id) == "PENDING"


async def test_unsynchronized_writer_is_stopped_by_the_database(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The activation holds the unit; a writer that ignores the protocol (no unit lock)
    # waits on the foreign key and is then rejected by the UNIQUE marker.
    unit_id = await make_unit(committed_session_factory, actor, 101)
    _, token = await invite(committed_session_factory, actor, unit_id)
    locked = asyncio.Event()

    async def hold() -> None:
        locked.set()
        await asyncio.sleep(HOLD_SECONDS)

    wrap_lock(monkeypatch, after=hold)

    async def unsynchronized() -> Any:
        await locked.wait()
        async with committed_session_factory() as session:
            try:
                await insert_other_residence(session, unit_id, lock_unit=False)
                await session.commit()
            except IntegrityError as exc:
                return exc

    activated, other = await gather(activate(committed_session_factory, activation(token)), unsynchronized())

    assert activated is None
    assert isinstance(other, IntegrityError) and "uq_resident_units_active_unit" in str(other.orig)
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM resident_units WHERE unit_id = :u AND ended_at IS NULL", u=unit_id) == 1
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM users WHERE email = 'vecino@example.com'") == 0


# --- D. Activation while an administrative process touches the same unit ---------------------------------


@pytest.mark.parametrize("operation", ["delete-unit", "invite-same-unit", "resend"])
async def test_activation_with_concurrent_admin_operation(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    unit_id = await make_unit(committed_session_factory, actor, 101)
    invitation_id, token = await invite(committed_session_factory, actor, unit_id)
    locked = asyncio.Event()

    async def hold() -> None:
        locked.set()
        await asyncio.sleep(HOLD_SECONDS)  # the activation keeps unit, user and invitation locked

    wrap_lock(monkeypatch, after=hold)

    async def admin_after_lock() -> tuple[Any, float]:
        await locked.wait()
        began = time.monotonic()
        if operation == "delete-unit":
            result = await attempt(committed_session_factory, structure_service.delete_unit, unit_id, 1, actor)
        elif operation == "invite-same-unit":
            data = InvitationCreate.model_validate(
                {"email": "otro@example.com", "full_name": "Otro Residente", "unit_id": unit_id, "validity_days": 7}
            )
            result = await attempt(
                committed_session_factory, invitation_service.create_invitation, data, actor,
                mailer=FakeMailSender(), app_origin=ORIGIN,
            )
        else:
            result = await attempt(
                committed_session_factory, invitation_service.resend_invitation, invitation_id, actor,
                mailer=FakeMailSender(), app_origin=ORIGIN,
            )
        return result, time.monotonic() - began

    activated, (admin_result, waited) = await gather(activate(committed_session_factory, activation(token)), admin_after_lock())

    assert activated is None
    expected = {"delete-unit": UnitInUseError, "invite-same-unit": UnitNotAvailableError, "resend": InvitationNotPendingError}
    assert isinstance(admin_result, expected[operation]), admin_result
    assert waited >= HOLD_SECONDS * 0.8  # serialized by the locks, in the approved order
    assert await scalar(committed_session_factory, "SELECT deleted_at FROM units WHERE id = :u", u=unit_id) is None
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM resident_units") == 1
    assert await scalar(committed_session_factory, "SELECT status FROM resident_invitations WHERE id = :i", i=invitation_id) == "USED"


async def test_resend_before_activation_invalidates_the_old_token(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The resident passed the early checks with the old token; a resend rotates it
    # before the locked phase: the activation must not use the stale link.
    unit_id = await make_unit(committed_session_factory, actor, 101)
    invitation_id, token = await invite(committed_session_factory, actor, unit_id)

    async def resend_first() -> None:
        await attempt(
            committed_session_factory, invitation_service.resend_invitation, invitation_id, actor,
            mailer=FakeMailSender(), app_origin=ORIGIN,
        )

    wrap_lock(monkeypatch, before=resend_first)

    result = await activate(committed_session_factory, activation(token))

    assert isinstance(result, InvitationInvalidError)
    assert await scalar(committed_session_factory, "SELECT status FROM resident_invitations WHERE id = :i", i=invitation_id) == "PENDING"
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM resident_units") == 0
