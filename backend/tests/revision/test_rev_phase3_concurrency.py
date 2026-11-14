"""Deep review, phase 3 A: a mixed concurrency stress on ONE apartment, with real commits.

At the same time: manual payments (HU-004), obligations (extension), two runs of the monthly
generation and demo payments (HU-010: start + approve). Afterwards, for every apartment:

    1. no payment and no charge is over-allocated;
    2. never credit and debt together: credit (saldo a favor) > 0 implies nothing pending,
       because a payment covers the pending charges first and a new charge consumes the credit;
    3. one monthly fee per period;
    4. at most one payment per approved demo transaction.

The existing suites test each pair of operations; this one mixes all of them, several rounds.
Only the *_test database is used and it is emptied afterwards (committed_session_factory).
"""

import asyncio
import random
from collections.abc import Awaitable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import bogota_today
from app.core.exceptions import AppError
from app.models import User, UserRole, UserStatus
from app.schemas.payment import PaymentCreate
from app.services import charges as charge_service
from app.services import demo_payments as demo_service
from app.services import payments as payment_service
from app.services.charges import ChargeInput, MonthlyGenerationInput
from app.services.monthly_rules import MonthlyParams
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
ROUNDS = 4


@pytest.fixture
async def setup(committed_session_factory: Factory) -> dict[str, Any]:
    async with committed_session_factory() as session:
        admin = User(email="admin@example.com", full_name="Admin Uno", role=UserRole.ADMINISTRADOR,
                     status=UserStatus.ACTIVE, password_hash="not-a-real-hash")
        session.add(admin)
        await session.flush()
        community = Community(session, admin.id)
        resident = await community.resident("Laura Gómez", "1012345678", (1, 1, 101))
        neighbor = await community.resident("Pedro Ruiz", "2012345678", (1, 1, 102))
        unit = await session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": resident})
        admin_id = admin.id
        await session.commit()
    return {"admin": admin_id, "resident": resident, "neighbor": neighbor, "unit": int(unit)}


async def attempt(factory: Factory, operation: Any) -> Any:
    async with factory() as session:
        try:
            return await operation(session)
        except AppError as exc:
            return exc


def manual(setup: dict[str, Any], resident: int, amount: str) -> Any:
    data = PaymentCreate.model_validate({"resident_id": resident, "amount": amount, "method": "CASH",
                                         "concept": "MONTHLY_FEE", "payment_date": bogota_today().isoformat()})
    return lambda s: payment_service.register_payment(s, data, Actor(setup["admin"], None), mailer=FakeMailSender())


def penalty(setup: dict[str, Any], unit: int, amount: str) -> Any:
    data = ChargeInput(unit, "PENALTY", Decimal(amount), date(2026, 1, 20), "Ruido")  # type: ignore[arg-type]
    return lambda s: charge_service.create_charge(s, data, Actor(setup["admin"], None))


def monthly(setup: dict[str, Any], period: date) -> Any:
    params = MonthlyParams(period, Decimal("180000.50"), period.replace(day=10))

    async def run(session: AsyncSession) -> Any:
        seen = await charge_service.preview_monthly(session, params)
        return await charge_service.generate_monthly(
            session, MonthlyGenerationInput(params, seen.to_create, seen.preview_fingerprint), Actor(setup["admin"], None))

    return run


def demo(setup: dict[str, Any]) -> Any:
    async def run(session: AsyncSession) -> Any:
        # The first obligation that still owes something (a plain read; start checks everything again).
        charge_id = await session.scalar(text(
            "SELECT c.id FROM charges c WHERE c.unit_id = :u AND c.amount > "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.charge_id = c.id) "
            "ORDER BY c.id LIMIT 1"), {"u": setup["unit"]})
        await session.commit()
        if charge_id is None:
            return None
        started = await demo_service.start_demo_payment(session, setup["resident"], charge_id, Decimal("1000.01"))
        return await demo_service.complete_demo_payment(session, setup["resident"], started.id, "APPROVED")

    return run


async def gather(*coroutines: Awaitable[Any]) -> list[Any]:
    return list(await asyncio.wait_for(asyncio.gather(*coroutines, return_exceptions=True), timeout=120))


async def check_invariants(factory: Factory) -> dict[str, Any]:
    async with factory() as session:
        over_charge = await session.scalar(text(
            "SELECT COUNT(*) FROM charges c WHERE c.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.charge_id = c.id)"))
        over_payment = await session.scalar(text(
            "SELECT COUNT(*) FROM payments p WHERE p.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.payment_id = p.id)"))
        both = await session.scalar(text(
            "SELECT COUNT(*) FROM units u WHERE "
            "(SELECT COALESCE(SUM(c.amount), 0) FROM charges c WHERE c.unit_id = u.id) > "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a JOIN charges c ON c.id = a.charge_id "
            " WHERE c.unit_id = u.id) AND "
            "(SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.unit_id = u.id) > "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a JOIN payments p ON p.id = a.payment_id "
            " WHERE p.unit_id = u.id)"))
        duplicated_fees = await session.scalar(text(
            "SELECT COUNT(*) FROM (SELECT unit_id, billing_period FROM charges WHERE billing_period IS NOT NULL "
            "GROUP BY unit_id, billing_period HAVING COUNT(*) > 1) d"))
        demo_twice = await session.scalar(text(
            "SELECT COUNT(*) FROM (SELECT payment_id FROM demo_payment_transactions WHERE payment_id IS NOT NULL "
            "GROUP BY payment_id HAVING COUNT(*) > 1) d"))
        totals = (await session.execute(text(
            "SELECT (SELECT COUNT(*) FROM payments), (SELECT COUNT(*) FROM charges), "
            "(SELECT COUNT(*) FROM payment_allocations)"))).one()
    result = {"over_charge": over_charge, "over_payment": over_payment, "credit_and_debt": both,
              "duplicated_fees": duplicated_fees, "demo_twice": demo_twice}
    assert result == dict.fromkeys(result, 0), result
    return {"payments": totals[0], "charges": totals[1], "allocations": totals[2]}


async def test_mixed_financial_operations_keep_every_invariant(
    committed_session_factory: Factory, setup: dict[str, Any]
) -> None:
    rng = random.Random(20261113)
    errors: list[Any] = []
    for round_ in range(ROUNDS):
        period = date(2026, 1 + round_, 1)
        operations = [monthly(setup, period), monthly(setup, period), demo(setup), demo(setup)]
        operations += [manual(setup, setup["resident"], f"{rng.randint(1, 300000)}.{rng.randint(0, 99):02d}")
                       for _ in range(5)]
        operations += [manual(setup, setup["neighbor"], "99999.99")]
        operations += [penalty(setup, setup["unit"], f"{rng.randint(1, 90000)}.{rng.randint(0, 99):02d}")
                       for _ in range(3)]
        rng.shuffle(operations)
        results = await gather(*(attempt(committed_session_factory, op) for op in operations))
        # Business refusals (AppError) are fine; anything else (deadlock, 500) is not.
        errors += [r for r in results if isinstance(r, BaseException) and not isinstance(r, AppError)]
        await check_invariants(committed_session_factory)
    assert errors == [], [type(e).__name__ + ": " + str(e)[:200] for e in errors]
    async with committed_session_factory() as session:
        fees = await session.scalar(select(text("COUNT(*)")).select_from(text("charges")).where(
            text("billing_period IS NOT NULL")))
    assert fees == ROUNDS * 2  # two apartments, one fee per period each, despite two runs per period
