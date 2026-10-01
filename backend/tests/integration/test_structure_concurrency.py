"""HU-013: real concurrency, with independent transactions that really commit.

Barriers force both transactions past the Python checks before either one
writes (the race a Python-only `if` cannot prevent); the database must still
keep the invariants. For parent/child races, one transaction holds its lock
for a while and the test measures that the other one really waited for it.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import (
    AppError,
    BlockHasActiveTowersError,
    BlockNotFoundError,
    BlockNumberExistsError,
    TowerHasActiveUnitsError,
    TowerNotFoundError,
    TowerNumberExistsError,
    UnitNotFoundError,
    UnitNumberExistsError,
    VersionConflictError,
)
from app.models import Block, User, UserRole, UserStatus
from app.schemas.structure import BlockRead, TowerRead, UnitRead
from app.services import structure as service
from app.services.structure import Actor

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
HOLD_SECONDS = 0.5


@pytest.fixture
async def actor(committed_session_factory: Factory) -> Actor:
    async with committed_session_factory() as session:
        user = User(
            email="admin@example.com",
            full_name="Ana María Pérez",
            role=UserRole.ADMINISTRADOR,
            status=UserStatus.ACTIVE,
            password_hash="not-a-real-hash",
        )
        session.add(user)
        await session.commit()
        return Actor(user_id=user.id, ip_address="127.0.0.1")


async def attempt(factory: Factory, operation: Callable[..., Awaitable[Any]], *args: Any) -> Any:
    """Runs one service call in its own transaction; expected errors are returned."""
    async with factory() as session:
        try:
            return await operation(session, *args)
        except AppError as exc:
            return exc


async def timed(coroutine: Awaitable[Any]) -> tuple[Any, float]:
    started = time.monotonic()
    result = await coroutine
    return result, time.monotonic() - started


async def scalar(factory: Factory, sql: str, **params: Any) -> Any:
    async with factory() as session:
        return await session.scalar(text(sql), params)


async def gather(*coroutines: Awaitable[Any]) -> list[Any]:
    # A deadlock or a lock that is never released fails the test instead of hanging.
    return list(await asyncio.wait_for(asyncio.gather(*coroutines), timeout=30))


def checks_then_wait(monkeypatch: pytest.MonkeyPatch, name: str) -> list[bool]:
    """Both transactions run the duplicate check before either one writes."""
    real = getattr(service, name)
    barrier = asyncio.Barrier(2)
    results: list[bool] = []

    async def wrapper(*args: Any, **kwargs: Any) -> bool:
        result = await real(*args, **kwargs)
        results.append(result)
        await barrier.wait()
        return result

    monkeypatch.setattr(service, name, wrapper)
    return results


def one_wins(results: list[Any], success: type, error: type) -> Any:
    winners = [r for r in results if isinstance(r, success)]
    losers = [r for r in results if isinstance(r, error)]
    assert (len(winners), len(losers)) == (1, 1), results
    return winners[0]


# --- Duplicates: UNIQUE on the generated column is the final guarantee ----------------


async def test_two_identical_blocks(committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch) -> None:
    checks = checks_then_wait(monkeypatch, "_block_number_taken")

    results = await gather(
        attempt(committed_session_factory, service.create_block, 1, actor),
        attempt(committed_session_factory, service.create_block, 1, actor),
    )

    assert checks == [False, False]
    loser = [r for r in results if isinstance(r, BlockNumberExistsError)]
    one_wins(results, BlockRead, BlockNumberExistsError)
    assert loser[0].message == "Ya existe el bloque B1."
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM blocks WHERE number = 1") == 1
    # The losing transaction was rolled back completely, audit entry included.
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM audit_logs") == 1


async def test_two_identical_towers_in_the_same_block(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    checks = checks_then_wait(monkeypatch, "_tower_number_taken")

    results = await gather(
        attempt(committed_session_factory, service.create_tower, b1.id, 2, actor),
        attempt(committed_session_factory, service.create_tower, b1.id, 2, actor),
    )

    assert checks == [False, False]
    one_wins(results, TowerRead, TowerNumberExistsError)
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM towers WHERE block_id = :b", b=b1.id) == 1


async def test_two_identical_apartments_in_the_same_tower(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    to2 = await attempt(committed_session_factory, service.create_tower, b1.id, 2, actor)
    checks = checks_then_wait(monkeypatch, "_unit_number_taken")

    results = await gather(
        attempt(committed_session_factory, service.create_unit, to2.id, 104, actor),
        attempt(committed_session_factory, service.create_unit, to2.id, 104, actor),
    )

    assert checks == [False, False]
    winner = one_wins(results, UnitRead, UnitNumberExistsError)
    assert winner.label == "B1-TO2-APTO-104"
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM units WHERE tower_id = :t", t=to2.id) == 1


async def test_renumber_while_another_creates_the_same_number(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    checks = checks_then_wait(monkeypatch, "_block_number_taken")

    results = await gather(
        attempt(committed_session_factory, service.update_block, b1.id, 4, 1, actor),
        attempt(committed_session_factory, service.create_block, 4, actor),
    )

    assert checks == [False, False]
    one_wins(results, BlockRead, BlockNumberExistsError)
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM blocks WHERE active_number_marker = 4") == 1


# --- Simultaneous edits: optimistic locking with version -----------------------------------


def start_together(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    real = getattr(service, name)
    barrier = asyncio.Barrier(2)

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        await barrier.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(service, name, wrapper)


async def test_simultaneous_block_edits(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    start_together(monkeypatch, "_lock_block")

    results = await gather(
        attempt(committed_session_factory, service.update_block, b1.id, 5, 1, actor),
        attempt(committed_session_factory, service.update_block, b1.id, 6, 1, actor),
    )

    winner = one_wins(results, BlockRead, VersionConflictError)
    assert winner.version == 2
    assert await scalar(committed_session_factory, "SELECT number FROM blocks WHERE id = :id", id=b1.id) == winner.number
    assert await scalar(committed_session_factory, "SELECT version FROM blocks WHERE id = :id", id=b1.id) == 2


async def test_simultaneous_tower_edits(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    to2 = await attempt(committed_session_factory, service.create_tower, b1.id, 2, actor)
    start_together(monkeypatch, "_lock_tower_path")

    results = await gather(
        attempt(committed_session_factory, service.update_tower, to2.id, 3, 1, actor),
        attempt(committed_session_factory, service.update_tower, to2.id, 4, 1, actor),
    )

    winner = one_wins(results, TowerRead, VersionConflictError)
    assert await scalar(committed_session_factory, "SELECT number FROM towers WHERE id = :id", id=to2.id) == winner.number


async def test_simultaneous_edit_and_delete_of_an_apartment(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    to2 = await attempt(committed_session_factory, service.create_tower, b1.id, 2, actor)
    apto = await attempt(committed_session_factory, service.create_unit, to2.id, 104, actor)
    start_together(monkeypatch, "_lock_unit_path")

    edited, deleted = await gather(
        attempt(committed_session_factory, service.update_unit, apto.id, 105, 1, actor),
        attempt(committed_session_factory, service.delete_unit, apto.id, 1, actor),
    )

    # Both read version 1: exactly one is applied, the other is rejected.
    if isinstance(edited, UnitRead):
        assert isinstance(deleted, VersionConflictError)
    else:
        assert deleted is None  # delete_unit returns None when applied
        # The edit then finds the apartment deleted (404) or the version changed (409).
        assert isinstance(edited, VersionConflictError | UnitNotFoundError)
    assert await scalar(committed_session_factory, "SELECT version FROM units WHERE id = :id", id=apto.id) == 2


# --- Parent and child at the same time: the parent row serializes them ----------------------


def hold_lock(monkeypatch: pytest.MonkeyPatch, name: str, *, when: Callable[[dict[str, Any]], bool]) -> asyncio.Event:
    """After the real call (lock taken), signal and keep the transaction open."""
    real = getattr(service, name)
    locked = asyncio.Event()

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = await real(*args, **kwargs)
        if when(kwargs) and not locked.is_set():
            locked.set()
            await asyncio.sleep(HOLD_SECONDS)
        return result

    monkeypatch.setattr(service, name, wrapper)
    return locked


async def after(event: asyncio.Event, coroutine: Awaitable[Any]) -> tuple[Any, float]:
    await event.wait()
    return await timed(coroutine)


async def active_children_of_deleted_parents(factory: Factory) -> int:
    return await scalar(
        factory,
        "SELECT (SELECT COUNT(*) FROM towers t JOIN blocks b ON b.id = t.block_id "
        "        WHERE t.deleted_at IS NULL AND b.deleted_at IS NOT NULL) "
        "     + (SELECT COUNT(*) FROM units u JOIN towers t ON t.id = u.tower_id "
        "        WHERE u.deleted_at IS NULL AND t.deleted_at IS NOT NULL)",
    )


async def test_delete_block_while_a_tower_is_being_created(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    # The creation takes FOR SHARE on B1 first and keeps its transaction open.
    locked = hold_lock(monkeypatch, "_lock_block", when=lambda kw: kw.get("share") is True)

    created, (deleted, waited) = await gather(
        attempt(committed_session_factory, service.create_tower, b1.id, 1, actor),
        after(locked, attempt(committed_session_factory, service.delete_block, b1.id, 1, actor)),
    )

    assert isinstance(created, TowerRead)
    assert isinstance(deleted, BlockHasActiveTowersError)
    assert waited >= HOLD_SECONDS * 0.8  # The deletion really waited for the creation.
    assert await active_children_of_deleted_parents(committed_session_factory) == 0


async def test_create_tower_while_its_block_is_being_deleted(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    # The deletion holds FOR UPDATE on B1 (already checked: no towers) and waits.
    locked = hold_lock(monkeypatch, "_has_active_children", when=lambda _kw: True)

    deleted, (created, waited) = await gather(
        attempt(committed_session_factory, service.delete_block, b1.id, 1, actor),
        after(locked, attempt(committed_session_factory, service.create_tower, b1.id, 1, actor)),
    )

    assert deleted is None
    assert isinstance(created, BlockNotFoundError)
    assert waited >= HOLD_SECONDS * 0.8
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM towers") == 0
    assert await active_children_of_deleted_parents(committed_session_factory) == 0


async def test_delete_tower_while_an_apartment_is_being_created(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    to2 = await attempt(committed_session_factory, service.create_tower, b1.id, 2, actor)
    locked = hold_lock(monkeypatch, "_lock_tower_path", when=lambda kw: kw.get("share") is True)

    created, (deleted, waited) = await gather(
        attempt(committed_session_factory, service.create_unit, to2.id, 104, actor),
        after(locked, attempt(committed_session_factory, service.delete_tower, to2.id, 1, actor)),
    )

    assert isinstance(created, UnitRead)
    assert isinstance(deleted, TowerHasActiveUnitsError)
    assert waited >= HOLD_SECONDS * 0.8
    assert await active_children_of_deleted_parents(committed_session_factory) == 0


async def test_create_apartment_while_its_tower_is_being_deleted(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    to2 = await attempt(committed_session_factory, service.create_tower, b1.id, 2, actor)
    locked = hold_lock(monkeypatch, "_has_active_children", when=lambda _kw: True)

    deleted, (created, waited) = await gather(
        attempt(committed_session_factory, service.delete_tower, to2.id, 1, actor),
        after(locked, attempt(committed_session_factory, service.create_unit, to2.id, 104, actor)),
    )

    assert deleted is None
    assert isinstance(created, TowerNotFoundError)
    assert waited >= HOLD_SECONDS * 0.8
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM units") == 0


async def test_renumber_block_while_a_tower_is_being_created(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    locked = hold_lock(monkeypatch, "_lock_block", when=lambda kw: kw.get("share") is True)

    created, (renumbered, waited) = await gather(
        attempt(committed_session_factory, service.create_tower, b1.id, 1, actor),
        after(locked, attempt(committed_session_factory, service.update_block, b1.id, 4, 1, actor)),
    )

    # Both succeed: the tower references the block id, not its number.
    assert isinstance(created, TowerRead) and created.label == "B1-TO1"
    assert isinstance(renumbered, BlockRead) and renumbered.label == "B4"
    assert waited >= HOLD_SECONDS * 0.8
    async with committed_session_factory() as session:
        assert (await service.get_tower(session, created.id)).label == "B4-TO1"


async def test_parallel_creations_in_the_same_block_do_not_block_each_other(
    committed_session_factory: Factory, actor: Actor
) -> None:
    # FOR SHARE is compatible with FOR SHARE: different towers can be created at once.
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)

    results = await gather(
        *(attempt(committed_session_factory, service.create_tower, b1.id, n, actor) for n in range(1, 6))
    )

    assert all(isinstance(r, TowerRead) for r in results)
    assert sorted(r.label for r in results) == ["B1-TO1", "B1-TO2", "B1-TO3", "B1-TO4", "B1-TO5"]


async def test_control_without_the_parent_lock_the_invariant_breaks(
    committed_session_factory: Factory, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: proves the tests above detect a missing lock.

    If the creation read its block WITHOUT FOR SHARE, a deletion could commit in
    between and leave an active tower under a deleted block (the foreign key
    alone does not prevent it, because the block row still exists).
    """
    b1 = await attempt(committed_session_factory, service.create_block, 1, actor)
    locked = asyncio.Event()
    deleted_done = asyncio.Event()

    async def unlocked_read(db: AsyncSession, block_id: int, *, share: bool) -> Any:
        block = await db.get(Block, block_id)  # plain read: no lock
        locked.set()
        await deleted_done.wait()
        return block

    monkeypatch.setattr(service, "_lock_block", unlocked_read)

    async def delete_then_signal() -> Any:
        await locked.wait()
        async with committed_session_factory() as session:
            block = await session.get(Block, b1.id)
            assert block is not None
            block.deleted_at = text("UTC_TIMESTAMP(6)")
            await session.commit()
        deleted_done.set()

    created, _ = await gather(
        attempt(committed_session_factory, service.create_tower, b1.id, 1, actor),
        delete_then_signal(),
    )

    assert isinstance(created, TowerRead)
    assert await active_children_of_deleted_parents(committed_session_factory) == 1
