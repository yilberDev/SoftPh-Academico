"""HU-010: real concurrency of the demo payment service (independent connections, real commits).

Every operation waits at a barrier right before the apartment lock, so they all compete for it at
the same time. The apartment lock (X) is the serialization point: a charge never has two live
transactions, a transaction never creates two payments and a charge is never over-allocated.
"""

import asyncio
from collections.abc import Awaitable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AppError, DemoPaymentNotAppliedError, DemoTransactionFinishedError
from app.models import Charge, User, UserRole, UserStatus
from app.schemas.payment import PaymentCreate
from app.services import demo_payments
from app.services import payments as payment_service
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
        resident = await Community(session, admin.id).resident("Laura Gómez", "1012345678", (1, 2, 104))
        unit_id = await session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": resident})
        admin_id = admin.id
        await session.commit()
    return {"admin": admin_id, "resident": resident, "unit": int(unit_id)}


async def add_charge(factory: Factory, unit_id: int, amount: str, due: date) -> int:
    async with factory() as session:
        author = await session.scalar(select(User.id).where(User.role == UserRole.ADMINISTRADOR).limit(1))
        row = Charge(unit_id=unit_id, concept="MONTHLY_FEE", amount=Decimal(amount), due_date=due,
                     billing_period=due.replace(day=1), created_by_user_id=author)
        session.add(row)
        await session.commit()
        return row.id


async def start(factory: Factory, resident: int, charge_id: int, amount: str) -> Any:
    async with factory() as session:
        try:
            return await demo_payments.start_demo_payment(session, resident, charge_id, Decimal(amount))
        except AppError as exc:
            return exc


async def complete(factory: Factory, resident: int, transaction_id: int, outcome: str) -> Any:
    async with factory() as session:
        try:
            if outcome == "CANCELLED":
                return await demo_payments.cancel_demo_payment(session, resident, transaction_id)
            return await demo_payments.complete_demo_payment(session, resident, transaction_id, outcome)
        except AppError as exc:
            return exc


async def complete_with(factory: Factory, resident: int, transaction_id: int, instrument_id: str) -> Any:
    async with factory() as session:
        try:
            return await demo_payments.complete_demo_payment_with_instrument(session, resident, transaction_id,
                                                                             instrument_id)
        except AppError as exc:
            return exc


async def manual(factory: Factory, setup: dict[str, int], amount: str) -> Any:
    data = PaymentCreate.model_validate({"resident_id": setup["resident"], "amount": amount, "method": "CASH",
                                         "concept": "MONTHLY_FEE", "payment_date": "2026-10-08"})
    async with factory() as session:
        try:
            return await payment_service.register_payment(session, data, Actor(setup["admin"], None),
                                                          mailer=FakeMailSender())
        except AppError as exc:
            return exc


async def scalar(factory: Factory, sql: str, **params: Any) -> Any:
    async with factory() as session:
        return await session.scalar(text(sql), params)


async def gather(*coroutines: Awaitable[Any]) -> list[Any]:
    return list(await asyncio.wait_for(asyncio.gather(*coroutines), timeout=30))


def all_at_the_lock(monkeypatch: pytest.MonkeyPatch, parties: int) -> None:
    """Every operation (demo or manual) waits here, right before the apartment lock."""
    barrier = asyncio.Barrier(parties)
    real = demo_payments.lock_unit_path

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        await barrier.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(demo_payments, "lock_unit_path", wrapper)
    monkeypatch.setattr(payment_service, "lock_unit_path", wrapper)


async def assert_never_over_allocated(factory: Factory) -> None:
    async with factory() as session:
        over_charge = await session.scalar(text(
            "SELECT COUNT(*) FROM charges c WHERE c.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.charge_id = c.id)"))
        over_payment = await session.scalar(text(
            "SELECT COUNT(*) FROM payments p WHERE p.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.payment_id = p.id)"))
        # A demo payment is always applied whole, to one charge.
        partial_demo = await session.scalar(text(
            "SELECT COUNT(*) FROM payments p WHERE p.source = 'DEMO_GATEWAY' AND (p.amount <> "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.payment_id = p.id) OR "
            "(SELECT COUNT(*) FROM payment_allocations a WHERE a.payment_id = p.id) <> 1)"))
    assert (over_charge, over_payment, partial_demo) == (0, 0, 0)


async def test_two_starts_never_leave_two_live_transactions(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    for round_ in range(1, 4):
        fee = await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, round_, 5))
        all_at_the_lock(monkeypatch, 2)
        results = await gather(start(committed_session_factory, setup["resident"], fee, "60000"),
                               start(committed_session_factory, setup["resident"], fee, "60000"))
        monkeypatch.undo()
        assert sorted(type(r).__name__ for r in results) == ["DemoPaymentInProgressError",
                                                              "DemoPaymentTransactionResult"]
        assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM demo_payment_transactions "
                                                       "WHERE charge_id = :c AND status = 'INITIATED'", c=fee) == 1


async def test_two_approvals_create_one_payment(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    for round_ in range(1, 4):
        fee = await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, round_, 5))
        started = await start(committed_session_factory, setup["resident"], fee, "100000")
        all_at_the_lock(monkeypatch, 2)
        first, second = await gather(complete(committed_session_factory, setup["resident"], started.id, "APPROVED"),
                                     complete(committed_session_factory, setup["resident"], started.id, "APPROVED"))
        monkeypatch.undo()
        # Both answers are the same approved transaction and its single payment.
        assert first == second and first.status == "APPROVED" and first.payment_id is not None
        assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments WHERE transaction_reference = :r",
                            r=started.reference) == 1
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments") == 3
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payment_allocations") == 3
    await assert_never_over_allocated(committed_session_factory)


@pytest.mark.parametrize("other", ["REJECTED", "FAILED", "CANCELLED"])
async def test_approval_and_another_end_at_once(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch, other: str
) -> None:
    for round_ in range(1, 4):
        fee = await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, round_, 5))
        started = await start(committed_session_factory, setup["resident"], fee, "100000")
        all_at_the_lock(monkeypatch, 2)
        approved, ended = await gather(complete(committed_session_factory, setup["resident"], started.id, "APPROVED"),
                                       complete(committed_session_factory, setup["resident"], started.id, other))
        monkeypatch.undo()
        status = await scalar(committed_session_factory, "SELECT status FROM demo_payment_transactions WHERE id = :t",
                              t=started.id)
        payments = await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments WHERE transaction_reference "
                                                           "= :r", r=started.reference)
        # Exactly one of them won; the other changed nothing.
        if status == "APPROVED":
            assert approved.status == "APPROVED" and isinstance(ended, DemoTransactionFinishedError) and payments == 1
        else:
            assert status == other and ended.status == other
            assert isinstance(approved, DemoTransactionFinishedError) and payments == 0
    await assert_never_over_allocated(committed_session_factory)


async def test_approval_and_a_manual_payment_on_the_same_charge(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever runs first, the charge is never paid twice and the demo amount never goes elsewhere."""
    for round_ in range(1, 6):
        fee = await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, round_, 5))
        started = await start(committed_session_factory, setup["resident"], fee, "100000")
        all_at_the_lock(monkeypatch, 2)
        demo, paid = await gather(complete(committed_session_factory, setup["resident"], started.id, "APPROVED"),
                                  manual(committed_session_factory, setup, "100000"))
        monkeypatch.undo()
        if isinstance(demo, DemoPaymentNotAppliedError):
            # The manual payment came first and paid the fee: the demo transaction FAILED, no payment.
            assert paid.status == "APPLIED"
            assert await scalar(committed_session_factory, "SELECT status FROM demo_payment_transactions WHERE id = :t",
                                t=started.id) == "FAILED"
        else:
            # The demo payment came first: the manual one found nothing pending and stayed as credit.
            assert demo.status == "APPROVED" and paid.status == "CREDIT"
        assert await scalar(committed_session_factory, "SELECT SUM(amount) FROM payment_allocations WHERE charge_id = :c",
                            c=fee) == Decimal("100000.00")
    await assert_never_over_allocated(committed_session_factory)


async def test_two_approvals_by_instrument_create_one_payment(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    for round_ in range(1, 4):
        fee = await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, round_, 5))
        started = await start(committed_session_factory, setup["resident"], fee, "100000")
        all_at_the_lock(monkeypatch, 2)
        first, second = await gather(
            complete_with(committed_session_factory, setup["resident"], started.id, "TEST_CARD_APPROVE"),
            complete_with(committed_session_factory, setup["resident"], started.id, "TEST_CARD_APPROVE"))
        monkeypatch.undo()
        # The second one finds it finished with the same instrument: the same answer, no second payment.
        assert first == second and first.status == "APPROVED" and first.test_instrument == "TEST_CARD_APPROVE"
        assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments WHERE transaction_reference = :r",
                            r=started.reference) == 1
        assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM audit_logs WHERE action = "
                                                       "'DEMO_PAYMENT_APPROVED' AND entity_id = :t", t=started.id) == 1
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payment_allocations") == 3
    await assert_never_over_allocated(committed_session_factory)


@pytest.mark.parametrize("other", ["TEST_CARD_INSUFFICIENT_FUNDS", "TEST_ACCOUNT_TECHNICAL_ERROR"])
async def test_two_instruments_at_once(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch, other: str
) -> None:
    expected = {"TEST_CARD_INSUFFICIENT_FUNDS": "REJECTED", "TEST_ACCOUNT_TECHNICAL_ERROR": "FAILED"}[other]
    for round_ in range(1, 4):
        fee = await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, round_, 5))
        started = await start(committed_session_factory, setup["resident"], fee, "100000")
        all_at_the_lock(monkeypatch, 2)
        approved, ended = await gather(
            complete_with(committed_session_factory, setup["resident"], started.id, "TEST_CARD_APPROVE"),
            complete_with(committed_session_factory, setup["resident"], started.id, other))
        monkeypatch.undo()
        async with committed_session_factory() as session:
            [(status, instrument)] = (await session.execute(text(
                "SELECT status, test_instrument FROM demo_payment_transactions WHERE id = :t"), {"t": started.id})).all()
        payments = await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments WHERE transaction_reference "
                                                           "= :r", r=started.reference)
        # Exactly one instrument resolved it, stored with its status; the other changed nothing (409).
        if status == "APPROVED":
            assert instrument == "TEST_CARD_APPROVE" and approved.status == "APPROVED" and payments == 1
            assert isinstance(ended, DemoTransactionFinishedError)
        else:
            assert (status, instrument) == (expected, other) and ended.status == expected and payments == 0
            assert isinstance(approved, DemoTransactionFinishedError)
    await assert_never_over_allocated(committed_session_factory)


@pytest.fixture
async def neighbors(committed_session_factory: Factory) -> dict[str, Any]:
    """Four residents in four different apartments of the same tower (shared block and tower locks)."""
    async with committed_session_factory() as session:
        admin = User(email="admin@example.com", full_name="Admin Uno", role=UserRole.ADMINISTRADOR,
                     status=UserStatus.ACTIVE, password_hash="not-a-real-hash")
        session.add(admin)
        await session.flush()
        community = Community(session, admin.id)
        residents = [await community.resident(f"Residente {n}", f"10123456{n:02d}", (1, 2, 100 + n),
                                              email=f"r{n}@example.com") for n in range(1, 5)]
        rows = (await session.execute(text("SELECT user_id, unit_id FROM resident_units"))).all()
        await session.commit()
    return {"residents": residents, "unit_of": {int(user): int(unit) for user, unit in rows}}


async def test_approvals_on_different_apartments_never_deadlock(
    committed_session_factory: Factory, neighbors: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    residents = neighbors["residents"]
    for round_ in range(1, 4):
        started = {}
        for resident in residents:
            fee = await add_charge(committed_session_factory, neighbors["unit_of"][resident], "100000",
                                   date(2026, round_, 5))
            started[resident] = await start(committed_session_factory, resident, fee, "100000")
        all_at_the_lock(monkeypatch, len(residents))
        results = await gather(*(complete(committed_session_factory, r, started[r].id, "APPROVED") for r in residents))
        monkeypatch.undo()
        assert [r.status for r in results] == ["APPROVED"] * len(residents)
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments") == 12
    await assert_never_over_allocated(committed_session_factory)
