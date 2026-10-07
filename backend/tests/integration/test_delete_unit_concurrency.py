"""Obligations extension (D10): delete_unit against financial operations, with real commits.

delete_unit locks the apartment (X) first, in the same order as payments and
obligations (block S → tower S → apartment X), and only then reads the balance
(consistent reads whose view starts after that lock). An apartment is never
left deleted with a pending balance or credit.
"""

import asyncio
from collections.abc import Awaitable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import bogota_today
from app.core.exceptions import AppError, FieldValidationError, UnitHasBalanceError, UnitInUseError
from app.models import Charge, Payment, User, UserRole, UserStatus
from app.schemas.payment import PaymentCreate
from app.services import charges as charges_service
from app.services import payments as payment_service
from app.services import structure as structure_service
from app.services.charges import ChargeInput, create_charge
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
ROUNDS = 6


@pytest.fixture
async def setup(committed_session_factory: Factory) -> dict[str, Any]:
    async with committed_session_factory() as session:
        admin = User(email="admin@example.com", full_name="Admin Uno", role=UserRole.ADMINISTRADOR,
                     status=UserStatus.ACTIVE, password_hash="not-a-real-hash")
        session.add(admin)
        await session.flush()
        community = Community(session, admin.id)
        resident = await community.resident("Laura Gómez", "1012345678", (1, 1, 100))
        former = User(email="antes@example.com", full_name="Ana Pérez", role=UserRole.RESIDENTE,
                      status=UserStatus.ACTIVE, password_hash="x")
        session.add(former)
        await session.flush()
        tower = await session.scalar(text("SELECT tower_id FROM units LIMIT 1"))
        resident_unit = await session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": resident})
        ids = {"admin": admin.id, "resident": resident, "former": former.id, "tower": int(tower),
               "resident_unit": int(resident_unit)}
        await session.commit()
    return ids


async def new_unit(factory: Factory, setup: dict[str, Any], number: int) -> int:
    async with factory() as session:
        return (await structure_service.create_unit(session, setup["tower"], number, Actor(setup["admin"], None))).id


async def credit(factory: Factory, setup: dict[str, Any], unit_id: int, amount: str) -> int:
    """A payment of a former resident with nothing applied (all of it is credit)."""
    async with factory() as session:
        row = Payment(resident_id=setup["former"], unit_id=unit_id, registered_by_user_id=setup["admin"],
                      amount=Decimal(amount), method="CASH", concept="OTHER", concept_description="Anticipo",
                      payment_date=date(2026, 9, 1), registered_at=datetime(2026, 9, 1, 15),
                      resident_name_snapshot="Ana Pérez", unit_label_snapshot="x")
        session.add(row)
        await session.commit()
        return row.id


def penalty(unit_id: int, amount: str = "100000") -> ChargeInput:
    return ChargeInput(unit_id, "PENALTY", Decimal(amount), date(2026, 12, 1), "Ruido")  # type: ignore[arg-type]


async def create(factory: Factory, setup: dict[str, Any], data: ChargeInput) -> Any:
    async with factory() as session:
        try:
            return await create_charge(session, data, Actor(setup["admin"], None))
        except AppError as exc:
            return exc


async def delete(factory: Factory, setup: dict[str, Any], unit_id: int) -> Any:
    async with factory() as session:
        version = await session.scalar(text("SELECT version FROM units WHERE id = :u"), {"u": unit_id})
        await session.commit()
        try:
            return await structure_service.delete_unit(session, unit_id, int(version), Actor(setup["admin"], None))
        except AppError as exc:
            return exc


async def state(factory: Factory, unit_id: int) -> tuple[bool, Decimal, Decimal]:
    async with factory() as session:
        deleted = await session.scalar(text("SELECT deleted_at IS NOT NULL FROM units WHERE id = :u"), {"u": unit_id})
        balance = await payment_service.unit_balance(session, unit_id)
    return bool(deleted), balance.pending_balance, balance.credit_balance


async def gather(*coroutines: Awaitable[Any]) -> list[Any]:
    try:
        return list(await asyncio.wait_for(asyncio.gather(*coroutines), timeout=60))
    except DBAPIError as exc:
        pytest.fail(f"deadlock or database error: {exc.orig}")


async def assert_never_over_allocated(factory: Factory) -> None:
    async with factory() as session:
        over_charge = await session.scalar(text(
            "SELECT COUNT(*) FROM charges c WHERE c.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.charge_id = c.id)"))
        over_payment = await session.scalar(text(
            "SELECT COUNT(*) FROM payments p WHERE p.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.payment_id = p.id)"))
    assert (over_charge, over_payment) == (0, 0)


def both_at_their_lock(monkeypatch: pytest.MonkeyPatch, *modules: Any) -> None:
    """Each operation has done its unlocked reads and waits here, right before the apartment lock."""
    barrier = asyncio.Barrier(len(modules))
    for module in modules:
        real = module.lock_unit_path

        async def wrapper(*args: Any, _real: Any = real, **kwargs: Any) -> Any:
            await barrier.wait()
            return await _real(*args, **kwargs)

        monkeypatch.setattr(module, "lock_unit_path", wrapper)


def delete_waits_after_its_first_read(monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    """delete_unit read the apartment and waits, before its lock, until the other operation committed."""
    ready, go = asyncio.Event(), asyncio.Event()
    real = structure_service.lock_unit_path

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        ready.set()
        await go.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(structure_service, "lock_unit_path", wrapper)
    return ready, go


# --- 1. delete_unit + create_charge ----------------------------------------------------------------------


async def test_a_charge_committed_before_the_lock_is_seen(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact race: delete_unit has read the apartment (balance 0), then a charge is committed,
    then delete_unit gets the lock. It must see the charge and refuse (a view from before the lock
    would see balance 0 and leave the apartment deleted with a debt)."""
    unit_id = await new_unit(committed_session_factory, setup, 201)
    ready, go = delete_waits_after_its_first_read(monkeypatch)

    async def charge_in_between() -> Any:
        await ready.wait()
        try:
            return await create(committed_session_factory, setup, penalty(unit_id))
        finally:
            go.set()

    deleted, created = await gather(delete(committed_session_factory, setup, unit_id), charge_in_between())

    assert not isinstance(created, Exception), created
    assert isinstance(deleted, UnitHasBalanceError)
    assert await state(committed_session_factory, unit_id) == (False, Decimal("100000.00"), Decimal("0.00"))


async def test_delete_and_charge_at_once(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes = set()
    for round_ in range(ROUNDS):
        unit_id = await new_unit(committed_session_factory, setup, 300 + round_)
        both_at_their_lock(monkeypatch, structure_service, charges_service)
        deleted, created = await gather(delete(committed_session_factory, setup, unit_id),
                                        create(committed_session_factory, setup, penalty(unit_id)))
        monkeypatch.undo()

        is_deleted, pending, _ = await state(committed_session_factory, unit_id)
        if is_deleted:  # the deletion ran first: the charge found the apartment deleted
            assert deleted is None and isinstance(created, FieldValidationError) and pending == 0
            outcomes.add("deleted first")
        else:  # the charge ran first: the deletion saw it
            assert isinstance(deleted, UnitHasBalanceError) and not isinstance(created, Exception)
            assert pending == Decimal("100000.00")
            outcomes.add("charge first")
        assert not (is_deleted and pending > 0)  # never deleted with a debt
    assert outcomes  # at least one outcome observed per round (both are valid)


# --- 2. delete_unit + payment ----------------------------------------------------------------------------


async def test_delete_and_payment_at_once(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A payment needs a current resident, so the apartment is in use (HU-003 rule first): the
    deletion is refused, the payment is applied, nothing deadlocks, nothing is over-allocated."""
    unit_id = setup["resident_unit"]
    for round_ in range(ROUNDS):
        async with committed_session_factory() as session:
            session.add(Charge(unit_id=unit_id, concept="MONTHLY_FEE", amount=Decimal("100000"),
                               due_date=date(2026, round_ + 1, 5), billing_period=date(2026, round_ + 1, 1),
                               created_by_user_id=setup["admin"]))
            await session.commit()
        both_at_their_lock(monkeypatch, structure_service, payment_service)

        async def pay() -> Any:
            async with committed_session_factory() as session:
                return await payment_service.register_payment(
                    session,
                    PaymentCreate.model_validate({"resident_id": setup["resident"], "amount": "100000", "method": "CASH",
                                                  "concept": "MONTHLY_FEE", "payment_date": bogota_today().isoformat()}),
                    Actor(setup["admin"], None), mailer=FakeMailSender())

        deleted, paid = await gather(delete(committed_session_factory, setup, unit_id), pay())
        monkeypatch.undo()

        assert isinstance(deleted, UnitInUseError) and paid.status == "APPLIED"
    await assert_never_over_allocated(committed_session_factory)
    assert await state(committed_session_factory, unit_id) == (False, Decimal("0.00"), Decimal("0.00"))


# --- 3. delete_unit + credit settlement ------------------------------------------------------------------


async def test_credit_consumed_before_the_lock_allows_the_deletion(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """delete_unit read the apartment while it had credit; an obligation consumed all of it before
    the lock: the deletion sees balance 0 (fresh view after the lock) and is applied."""
    unit_id = await new_unit(committed_session_factory, setup, 401)
    payment = await credit(committed_session_factory, setup, unit_id, "100000")
    ready, go = delete_waits_after_its_first_read(monkeypatch)

    async def charge_in_between() -> Any:
        await ready.wait()
        try:
            return await create(committed_session_factory, setup, penalty(unit_id))
        finally:
            go.set()

    deleted, created = await gather(delete(committed_session_factory, setup, unit_id), charge_in_between())

    assert created.applied_amount == Decimal("100000.00") and [c.payment_id for c in created.credit_applied] == [payment]
    assert deleted is None
    assert await state(committed_session_factory, unit_id) == (True, Decimal("0.00"), Decimal("0.00"))


async def test_partial_credit_consumed_before_the_lock_still_refuses(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    unit_id = await new_unit(committed_session_factory, setup, 402)
    await credit(committed_session_factory, setup, unit_id, "100000")
    ready, go = delete_waits_after_its_first_read(monkeypatch)

    async def charge_in_between() -> Any:
        await ready.wait()
        try:
            return await create(committed_session_factory, setup, penalty(unit_id, "150000"))  # 50.000 left to pay
        finally:
            go.set()

    deleted, created = await gather(delete(committed_session_factory, setup, unit_id), charge_in_between())

    assert created.applied_amount == Decimal("100000.00")
    assert isinstance(deleted, UnitHasBalanceError)
    assert await state(committed_session_factory, unit_id) == (False, Decimal("50000.00"), Decimal("0.00"))


async def test_delete_and_credit_settlement_at_once(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for round_ in range(ROUNDS):
        unit_id = await new_unit(committed_session_factory, setup, 500 + round_)
        await credit(committed_session_factory, setup, unit_id, "100000")
        both_at_their_lock(monkeypatch, structure_service, charges_service)
        deleted, created = await gather(delete(committed_session_factory, setup, unit_id),
                                        create(committed_session_factory, setup, penalty(unit_id)))
        monkeypatch.undo()

        is_deleted, pending, left = await state(committed_session_factory, unit_id)
        if is_deleted:  # the obligation consumed the credit first: balance 0, deletion allowed
            assert deleted is None and created.applied_amount == Decimal("100000.00")
        else:  # the deletion saw the credit first and refused; the obligation then consumed it
            assert isinstance(deleted, UnitHasBalanceError) and created.applied_amount == Decimal("100000.00")
        assert (pending, left) == (Decimal("0.00"), Decimal("0.00"))  # never inconsistent
    await assert_never_over_allocated(committed_session_factory)
