"""HU-004: real concurrency (independent connections, real commits).

Lock order of a payment: block (S) → tower (S) → apartment (X) → current
residence (S) → resident (S) → pending charges (X). The same "apartment first"
order as HU-013 and HU-019, so no cycle (deadlock 1213) can form with them.
"""

import asyncio
from collections.abc import Awaitable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import bogota_today
from app.core.exceptions import AppError, UnitInUseError
from app.models import Charge, User, UserRole, UserStatus
from app.schemas.payment import PaymentCreate
from app.services import payments as payment_service
from app.services import structure as structure_service
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def setup(committed_session_factory: Factory) -> dict[str, int]:
    async with committed_session_factory() as session:
        admin = User(email="admin@example.com", full_name="Admin Uno", role=UserRole.ADMINISTRADOR,
                     status=UserStatus.ACTIVE, password_hash="not-a-real-hash")
        session.add(admin)
        await session.flush()
        community = Community(session, admin.id)
        resident = await community.resident("Laura Gómez", "1012345678", (1, 2, 104))
        unit_id = await session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": resident})
        admin_id = admin.id
        await session.commit()
    return {"admin": admin_id, "resident": resident, "unit": int(unit_id)}


async def add_charge(factory: Factory, unit_id: int, amount: str, due: date) -> int:
    async with factory() as session:
        # Obligations extension: billing period (month of the due date) and author (the administrator).
        author = await session.scalar(select(User.id).where(User.role == UserRole.ADMINISTRADOR).limit(1))
        row = Charge(unit_id=unit_id, concept="MONTHLY_FEE", amount=Decimal(amount), due_date=due,
                     billing_period=due.replace(day=1), created_by_user_id=author)
        session.add(row)
        await session.commit()
        return row.id


def payment(resident_id: int, amount: str) -> PaymentCreate:
    return PaymentCreate.model_validate({"resident_id": resident_id, "amount": amount, "method": "CASH",
                                         "concept": "MONTHLY_FEE", "payment_date": bogota_today().isoformat()})


async def register(factory: Factory, setup: dict[str, int], amount: str) -> Any:
    async with factory() as session:
        try:
            return await payment_service.register_payment(
                session, payment(setup["resident"], amount), Actor(setup["admin"], None), mailer=FakeMailSender()
            )
        except AppError as exc:
            return exc


async def scalar(factory: Factory, sql: str, **params: Any) -> Any:
    async with factory() as session:
        return await session.scalar(text(sql), params)


async def gather(*coroutines: Awaitable[Any]) -> list[Any]:
    try:
        return list(await asyncio.wait_for(asyncio.gather(*coroutines), timeout=60))
    except DBAPIError as exc:
        pytest.fail(f"deadlock or database error: {exc.orig}")


def all_at_the_lock(monkeypatch: pytest.MonkeyPatch, parties: int) -> None:
    """Every payment has read the residence and waits here, right before the apartment lock."""
    barrier = asyncio.Barrier(parties)
    real = payment_service.lock_unit_path

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        await barrier.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(payment_service, "lock_unit_path", wrapper)


async def assert_never_over_allocated(factory: Factory) -> None:
    async with factory() as session:
        over_charge = await session.scalar(text(
            "SELECT COUNT(*) FROM charges c WHERE c.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.charge_id = c.id)"))
        over_payment = await session.scalar(text(
            "SELECT COUNT(*) FROM payments p WHERE p.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.payment_id = p.id)"))
    assert (over_charge, over_payment) == (0, 0)


async def test_two_payments_on_one_pending_charge(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    only = await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, 1, 5))
    all_at_the_lock(monkeypatch, 2)

    results = await gather(
        register(committed_session_factory, setup, "70000"),
        register(committed_session_factory, setup, "70000"),
    )

    # Both registered; the charge received exactly 100.000; the rest is credit.
    assert all(not isinstance(r, Exception) for r in results), results
    assert sorted(r.status for r in results) == ["APPLIED", "PARTIALLY_APPLIED"]
    assert sorted(r.applied_amount for r in results) == [Decimal("30000.00"), Decimal("70000.00")]
    assert sum(r.credit_amount for r in results) == Decimal("40000.00")
    allocated = await scalar(committed_session_factory, "SELECT SUM(amount) FROM payment_allocations WHERE charge_id = :c", c=only)
    assert allocated == Decimal("100000.00")
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments") == 2
    await assert_never_over_allocated(committed_session_factory)
    # The second one (in lock order) saw the first: its balance shows the final state.
    last = max(results, key=lambda r: r.id)
    assert last.account.pending_balance == Decimal("0.00") and last.account.credit_balance == Decimal("40000.00")


async def test_many_payments_at_once(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    for month in (1, 2):
        await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, month, 5))
    all_at_the_lock(monkeypatch, 5)

    results = await gather(*(register(committed_session_factory, setup, "45000") for _ in range(5)))

    assert all(not isinstance(r, Exception) for r in results), results
    assert sum(r.applied_amount for r in results) == Decimal("200000.00")
    assert sum(r.credit_amount for r in results) == Decimal("25000.00")
    await assert_never_over_allocated(committed_session_factory)
    async with committed_session_factory() as session:
        balance = await payment_service.unit_balance(session, setup["unit"])
    assert (balance.pending_balance, balance.credit_balance) == (Decimal("0.00"), Decimal("25000.00"))


async def test_payment_and_unit_deletion_never_deadlock(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, 1, 5))
    version = await scalar(committed_session_factory, "SELECT version FROM units WHERE id = :u", u=setup["unit"])
    for _ in range(5):
        barrier = asyncio.Barrier(2)
        real = payment_service.lock_unit_path

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            await barrier.wait()
            return await real(*args, **kwargs)

        monkeypatch.setattr(payment_service, "lock_unit_path", wrapper)

        async def delete() -> Any:
            await barrier.wait()
            async with committed_session_factory() as session:
                try:
                    await structure_service.delete_unit(session, setup["unit"], version, Actor(setup["admin"], None))
                except AppError as exc:
                    return exc

        paid, deleted = await gather(register(committed_session_factory, setup, "10000"), delete())
        monkeypatch.undo()

        assert not isinstance(paid, Exception), paid
        assert isinstance(deleted, UnitInUseError)  # the apartment has a resident
    await assert_never_over_allocated(committed_session_factory)


async def test_payment_and_renumbering_never_deadlock(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    for round_ in range(5):
        version = await scalar(committed_session_factory, "SELECT version FROM units WHERE id = :u", u=setup["unit"])
        barrier = asyncio.Barrier(2)
        real = payment_service.lock_unit_path

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            await barrier.wait()
            return await real(*args, **kwargs)

        monkeypatch.setattr(payment_service, "lock_unit_path", wrapper)

        async def renumber(number: int, current: int) -> Any:
            await barrier.wait()
            async with committed_session_factory() as session:
                return await structure_service.update_unit(session, setup["unit"], number, current, Actor(setup["admin"], None))

        paid, renumbered = await gather(register(committed_session_factory, setup, "1000"), renumber(500 + round_, version))
        monkeypatch.undo()

        # Whatever ran first, the receipt label is one of the two consistent states.
        assert paid.unit.label in {f"B1-TO2-APTO-{104 if round_ == 0 else 499 + round_}", renumbered.label}
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments") == 5


async def test_the_guard_catches_a_stale_read(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even if the pending charges were read from an old snapshot (the bug the
    tests above would expose), the guard re-reads with locks and rolls back."""
    from app.services.allocation import PendingCharge

    only = await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, 1, 5))
    first = await register(committed_session_factory, setup, "100000")
    assert first.status == "APPLIED"

    async def stale(_db: Any, _unit_id: int) -> list[PendingCharge]:
        # As if the first payment had not been seen: the charge looks unpaid.
        return [PendingCharge(only, date(2026, 1, 5), first.registered_at.replace(tzinfo=None), Decimal("100000.00"))]

    monkeypatch.setattr(payment_service, "_pending_charges", stale)
    with pytest.raises(RuntimeError, match="invariant"):
        await register(committed_session_factory, setup, "50000")

    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments") == 1
    await assert_never_over_allocated(committed_session_factory)
