"""Obligations extension: concurrent create_charge with real commits.

Every operation locks the apartment first (block S → tower S → apartment X), as
HU-004 payments do, so operations on one apartment run one after the other:
the credit is consumed once, the UNIQUE rules hold and nothing deadlocks.
"""

import asyncio
from collections.abc import Awaitable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import bogota_today
from app.core.exceptions import AppError, LateFeeExistsError, MonthlyFeeExistsError
from app.models import User, UserRole, UserStatus
from app.schemas.payment import PaymentCreate
from app.services import charges as charges_service
from app.services import payments as payment_service
from app.services.allocation import ChargeStatus
from app.services.charges import ChargeInput, create_charge
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
TODAY = bogota_today()


@pytest.fixture
async def setup(committed_session_factory: Factory) -> dict[str, int]:
    async with committed_session_factory() as session:
        admin = User(email="admin@example.com", full_name="Admin Uno", role=UserRole.ADMINISTRADOR,
                     status=UserStatus.ACTIVE, password_hash="not-a-real-hash")
        session.add(admin)
        await session.flush()
        resident = await Community(session, admin.id).resident("Laura Gómez", "1012345678", (1, 2, 104))
        unit_id = await session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": resident})
        admin_id = admin.id
        await session.commit()
    return {"admin": admin_id, "resident": resident, "unit": int(unit_id)}


def monthly(unit_id: int, amount: str = "100000", month: int = 1) -> ChargeInput:
    return ChargeInput(unit_id, "MONTHLY_FEE", Decimal(amount), date(2026, month, 5),  # type: ignore[arg-type]
                       billing_period=date(2026, month, 1))


async def create(factory: Factory, setup: dict[str, int], data: ChargeInput) -> Any:
    async with factory() as session:
        try:
            return await create_charge(session, data, Actor(setup["admin"], None))
        except AppError as exc:
            return exc


async def pay(factory: Factory, setup: dict[str, int], amount: str) -> Any:
    async with factory() as session:
        try:
            return await payment_service.register_payment(
                session,
                PaymentCreate.model_validate({"resident_id": setup["resident"], "amount": amount, "method": "CASH",
                                              "concept": "MONTHLY_FEE", "payment_date": TODAY.isoformat()}),
                Actor(setup["admin"], None), mailer=FakeMailSender(),
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
    """Every operation has done its unlocked reads and waits here, right before the apartment lock."""
    barrier = asyncio.Barrier(parties)
    for module in (charges_service, payment_service):
        real = module.lock_unit_path

        async def wrapper(*args: Any, _real: Any = real, **kwargs: Any) -> Any:
            await barrier.wait()
            return await _real(*args, **kwargs)

        monkeypatch.setattr(module, "lock_unit_path", wrapper)


async def assert_never_over_allocated(factory: Factory) -> None:
    async with factory() as session:
        over_charge = await session.scalar(text(
            "SELECT COUNT(*) FROM charges c WHERE c.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.charge_id = c.id)"))
        over_payment = await session.scalar(text(
            "SELECT COUNT(*) FROM payments p WHERE p.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.payment_id = p.id)"))
        not_positive = await session.scalar(text("SELECT COUNT(*) FROM payment_allocations WHERE amount <= 0"))
    assert (over_charge, over_payment, not_positive) == (0, 0, 0)


async def test_two_charges_compete_for_the_same_credit(  # 33-37
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    credit = await pay(committed_session_factory, setup, "100000")
    assert credit.status == "CREDIT"
    all_at_the_lock(monkeypatch, 2)

    results = await gather(create(committed_session_factory, setup, monthly(setup["unit"], month=1)),
                           create(committed_session_factory, setup, monthly(setup["unit"], month=2)))

    assert all(not isinstance(r, Exception) for r in results), results
    # Exactly one of them received the credit (whichever got the lock first).
    assert sorted(r.applied_amount for r in results) == [Decimal("0.00"), Decimal("100000.00")]
    assert sorted(r.status for r in results) == sorted([ChargeStatus.PAID, ChargeStatus.OVERDUE])
    assert await scalar(committed_session_factory, "SELECT SUM(amount) FROM payment_allocations WHERE payment_id = :p",
                        p=credit.id) == Decimal("100000.00")
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM audit_logs WHERE action = 'CREDIT_APPLIED'") == 1
    await assert_never_over_allocated(committed_session_factory)


async def test_many_charges_and_credits_at_once(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    for amount in ("70000", "45000"):
        await pay(committed_session_factory, setup, amount)
    all_at_the_lock(monkeypatch, 5)

    results = await gather(*(create(committed_session_factory, setup, monthly(setup["unit"], "30000", month))
                             for month in range(1, 6)))

    assert all(not isinstance(r, Exception) for r in results), results
    assert sum(r.applied_amount for r in results) == Decimal("115000.00")  # all the credit, once
    assert await scalar(committed_session_factory, "SELECT SUM(amount) FROM payment_allocations") == Decimal("115000.00")
    await assert_never_over_allocated(committed_session_factory)
    async with committed_session_factory() as session:
        balance = await payment_service.unit_balance(session, setup["unit"])
    assert (balance.pending_balance, balance.credit_balance) == (Decimal("35000.00"), Decimal("0.00"))


async def test_charge_and_payment_at_once_never_deadlock(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    await pay(committed_session_factory, setup, "40000")  # credit
    for month in range(1, 6):
        all_at_the_lock(monkeypatch, 2)
        created, paid = await gather(create(committed_session_factory, setup, monthly(setup["unit"], "50000", month)),
                                     pay(committed_session_factory, setup, "30000"))
        monkeypatch.undo()
        assert not isinstance(created, Exception) and not isinstance(paid, Exception), (created, paid)
    await assert_never_over_allocated(committed_session_factory)
    async with committed_session_factory() as session:
        balance = await payment_service.unit_balance(session, setup["unit"])
    # Charges 250.000; payments 40.000 + 5 × 30.000 = 190.000: whatever the order, credit and pending never coexist.
    assert (balance.pending_balance, balance.credit_balance) == (Decimal("60000.00"), Decimal("0.00"))


async def test_same_monthly_fee_at_once(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    all_at_the_lock(monkeypatch, 3)
    results = await gather(*(create(committed_session_factory, setup, monthly(setup["unit"])) for _ in range(3)))
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert sum(isinstance(r, MonthlyFeeExistsError) for r in results) == 2
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM charges") == 1
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM audit_logs WHERE action = 'CHARGE_CREATED'") == 1


async def test_two_late_fees_for_one_origin_at_once(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = await create(committed_session_factory, setup, monthly(setup["unit"]))  # due 2026-01-05, overdue
    late = ChargeInput(setup["unit"], "LATE_FEE", Decimal("5000"), TODAY, source_charge_id=origin.id)  # type: ignore[arg-type]
    all_at_the_lock(monkeypatch, 3)

    results = await gather(*(create(committed_session_factory, setup, late) for _ in range(3)))

    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert sum(isinstance(r, LateFeeExistsError) for r in results) == 2
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM charges WHERE source_charge_id = :o", o=origin.id) == 1


async def test_charges_on_different_apartments_never_deadlock(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (see test_payments_on_different_apartments_never_deadlock): obligations of
    different apartments, each consuming its own credit, at the same time."""
    async with committed_session_factory() as session:
        community = Community(session, setup["admin"])
        others = [await community.resident(f"Vecino {n}", f"10987654{n:02d}", (2, 1, 200 + n), email=f"v{n}@example.com")
                  for n in range(1, 4)]
        units = {int(user): int(unit) for user, unit in
                 (await session.execute(text("SELECT user_id, unit_id FROM resident_units"))).all()}
        await session.commit()
    residents = [setup["resident"], *others]
    for resident in residents:
        neighbor = {**setup, "resident": resident}
        assert (await pay(committed_session_factory, neighbor, "250000")).status == "CREDIT"

    for month in range(1, 6):
        all_at_the_lock(monkeypatch, len(residents))
        results = await gather(*(create(committed_session_factory, setup, monthly(units[r], "50000", month)) for r in residents))
        monkeypatch.undo()
        assert all(not isinstance(r, Exception) for r in results), results
        assert [r.status for r in results] == [ChargeStatus.PAID] * len(residents)

    await assert_never_over_allocated(committed_session_factory)
    for resident in residents:
        async with committed_session_factory() as session:
            balance = await payment_service.unit_balance(session, units[resident])
        assert (balance.pending_balance, balance.credit_balance) == (Decimal("0.00"), Decimal("0.00"))
