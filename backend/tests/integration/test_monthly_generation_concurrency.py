"""Obligations extension: monthly generation with real commits and concurrent work.

Each apartment is generated in its own transaction that locks it first
(block S → tower S → apartment X), like a payment. Two runs of the same period
share the apartments without duplicates, payments and structure changes are
serialized per apartment, and nothing deadlocks.
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
from app.core.exceptions import AppError, UnitHasBalanceError
from app.models import User, UserRole, UserStatus
from app.schemas.payment import PaymentCreate
from app.services import charges as charges_service
from app.services import payments as payment_service
from app.services import structure as structure_service
from app.services.charges import MonthlyGenerationInput, MonthlyGenerationResult, generate_monthly, preview_monthly
from app.services.monthly_rules import MonthlyParams
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
OCTOBER = MonthlyParams(date(2026, 10, 1), Decimal("100000"), date(2026, 10, 10))
UNITS = 12


@pytest.fixture
async def setup(committed_session_factory: Factory) -> dict[str, Any]:
    """An administrator and apartments in two blocks; the first three have a resident."""
    async with committed_session_factory() as session:
        admin = User(email="admin@example.com", full_name="Admin Uno", role=UserRole.ADMINISTRADOR,
                     status=UserStatus.ACTIVE, password_hash="not-a-real-hash")
        session.add(admin)
        await session.flush()
        community = Community(session, admin.id)
        residents = [await community.resident(f"Residente {n}", f"10123456{n:02d}", (1, 1, 100 + n),
                                              email=f"r{n}@example.com") for n in range(1, 4)]
        for n in range(4, UNITS + 1):
            await community.unit(1 + n // 7, 1, 100 + n)
        rows = (await session.execute(text("SELECT user_id, unit_id FROM resident_units"))).all()
        units = [int(u) for u in (await session.scalars(text("SELECT id FROM units ORDER BY id"))).all()]
        admin_id = admin.id
        await session.commit()
    return {"admin": admin_id, "residents": residents, "units": units,
            "unit_of": {int(user): int(unit) for user, unit in rows}}


async def preview(factory: Factory) -> Any:
    async with factory() as session:
        return await preview_monthly(session, OCTOBER)


async def generate(factory: Factory, setup: dict[str, Any], seen: Any) -> Any:
    async with factory() as session:
        try:
            return await generate_monthly(session, MonthlyGenerationInput(OCTOBER, seen.to_create, seen.preview_fingerprint),
                                          Actor(setup["admin"], None))
        except AppError as exc:
            return exc


async def pay(factory: Factory, setup: dict[str, Any], resident: int, amount: str) -> Any:
    async with factory() as session:
        return await payment_service.register_payment(
            session,
            PaymentCreate.model_validate({"resident_id": resident, "amount": amount, "method": "CASH",
                                          "concept": "MONTHLY_FEE", "payment_date": bogota_today().isoformat()}),
            Actor(setup["admin"], None), mailer=FakeMailSender(),
        )


async def scalar(factory: Factory, sql: str, **params: Any) -> Any:
    async with factory() as session:
        return await session.scalar(text(sql), params)


async def gather(*coroutines: Awaitable[Any]) -> list[Any]:
    try:
        return list(await asyncio.wait_for(asyncio.gather(*coroutines), timeout=120))
    except DBAPIError as exc:
        pytest.fail(f"deadlock or database error: {exc.orig}")


def consistent(result: MonthlyGenerationResult) -> MonthlyGenerationResult:
    t = result.totals
    assert t.existing + t.created + t.omitted + t.failed == t.units_evaluated
    return result


async def assert_never_over_allocated(factory: Factory) -> None:
    async with factory() as session:
        over_charge = await session.scalar(text(
            "SELECT COUNT(*) FROM charges c WHERE c.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.charge_id = c.id)"))
        over_payment = await session.scalar(text(
            "SELECT COUNT(*) FROM payments p WHERE p.amount < "
            "(SELECT COALESCE(SUM(a.amount), 0) FROM payment_allocations a WHERE a.payment_id = p.id)"))
    assert (over_charge, over_payment) == (0, 0)


def start_together(monkeypatch: pytest.MonkeyPatch, parties: int) -> asyncio.Barrier:
    """Every run waits here, after checking its preview and right before its first apartment lock;
    the other parties of the test wait on the returned barrier."""
    barrier = asyncio.Barrier(parties)
    real = charges_service.lock_unit_path
    waited: set[int] = set()

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        task = id(asyncio.current_task())
        if task not in waited:
            waited.add(task)
            await barrier.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(charges_service, "lock_unit_path", wrapper)
    return barrier


async def test_two_runs_of_the_same_period(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = await preview(committed_session_factory)
    assert seen.to_create == UNITS
    start_together(monkeypatch, 2)

    first, second = await gather(generate(committed_session_factory, setup, seen), generate(committed_session_factory, setup, seen))

    for result in (first, second):
        assert not isinstance(result, Exception), result
        consistent(result)
        assert result.totals.units_evaluated == UNITS and result.totals.failed == 0
    # They shared the apartments: every apartment has exactly one monthly fee.
    assert first.totals.created + second.totals.created == UNITS
    assert first.totals.existing + second.totals.existing == UNITS
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM charges WHERE billing_period = '2026-10-01'") == UNITS
    assert await scalar(committed_session_factory,
                        "SELECT COUNT(DISTINCT unit_id) FROM charges WHERE billing_period = '2026-10-01'") == UNITS
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM audit_logs WHERE action = 'CHARGE_CREATED'") == UNITS
    assert await scalar(committed_session_factory,
                        "SELECT COUNT(*) FROM audit_logs WHERE action = 'MONTHLY_CHARGES_GENERATED'") == 2


async def test_a_second_confirmation_after_the_first_is_outdated(
    committed_session_factory: Factory, setup: dict[str, Any]
) -> None:
    # A double click that arrives after the first run finished: nothing is created twice.
    seen = await preview(committed_session_factory)
    first = await generate(committed_session_factory, setup, seen)
    second = await generate(committed_session_factory, setup, seen)
    assert first.totals.created == UNITS
    assert isinstance(second, AppError) and second.code == "MONTHLY_PREVIEW_OUTDATED"
    assert second.preview.to_create == 0  # type: ignore[attr-defined]
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM charges") == UNITS


async def test_generation_and_payments_at_once(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = await preview(committed_session_factory)
    barrier = start_together(monkeypatch, 1 + len(setup["residents"]))

    async def pay_together(resident: int) -> Any:
        await barrier.wait()
        return await pay(committed_session_factory, setup, resident, "60000")

    results = await gather(generate(committed_session_factory, setup, seen), *(pay_together(r) for r in setup["residents"]))

    generated = consistent(results[0])
    assert (generated.totals.created, generated.totals.failed) == (UNITS, 0)
    await assert_never_over_allocated(committed_session_factory)
    # Whatever ran first in each apartment: fee 100.000, payment 60.000 → 40.000 pending, no credit.
    for resident in setup["residents"]:
        async with committed_session_factory() as session:
            balance = await payment_service.unit_balance(session, setup["unit_of"][resident])
        assert (balance.pending_balance, balance.credit_balance) == (Decimal("40000.00"), Decimal("0.00"))


async def test_generation_and_unit_deletion_at_once(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    target = setup["units"][-1]  # no resident: it can be deleted
    version = await scalar(committed_session_factory, "SELECT version FROM units WHERE id = :u", u=target)
    seen = await preview(committed_session_factory)
    barrier = start_together(monkeypatch, 2)

    async def delete() -> Any:
        await barrier.wait()
        async with committed_session_factory() as session:
            try:
                return await structure_service.delete_unit(session, target, version, Actor(setup["admin"], None))
            except AppError as exc:
                return exc

    generated, deleted = await gather(generate(committed_session_factory, setup, seen), delete())

    result = consistent(generated)
    fee = await scalar(committed_session_factory,
                       "SELECT COUNT(*) FROM charges WHERE unit_id = :u AND billing_period = '2026-10-01'", u=target)
    is_deleted = await scalar(committed_session_factory, "SELECT deleted_at IS NOT NULL FROM units WHERE id = :u", u=target)
    # Either the fee was created first and the deletion is refused (D10: it has a pending balance),
    # or the apartment was deleted first and omitted: never deleted with its new fee.
    if result.totals.omitted == 0:
        assert result.totals.created == UNITS and fee == 1
        assert isinstance(deleted, UnitHasBalanceError) and not is_deleted
    else:
        assert (result.totals.created, result.totals.omitted) == (UNITS - 1, 1) and fee == 0
        assert deleted is None and is_deleted


async def test_apartment_created_during_the_run(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = await preview(committed_session_factory)
    tower = await scalar(committed_session_factory, "SELECT tower_id FROM units WHERE id = :u", u=setup["units"][0])
    barrier = start_together(monkeypatch, 2)

    async def create_unit() -> Any:
        await barrier.wait()
        async with committed_session_factory() as session:
            return await structure_service.create_unit(session, tower, 999, Actor(setup["admin"], None))

    generated, created = await gather(generate(committed_session_factory, setup, seen), create_unit())

    # The run works on the apartments it read when confirming; the new one waits for the next run.
    assert consistent(generated).totals.units_evaluated == UNITS
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM charges WHERE unit_id = :u", u=created.id) == 0
    assert (await preview(committed_session_factory)).to_create == 1


async def test_generation_and_individual_charges_compete_for_the_same_credit(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The credit of an apartment is consumed once even if its monthly fee and another
    obligation are created at the same time (both lock the apartment first)."""
    for resident in setup["residents"]:
        assert (await pay(committed_session_factory, setup, resident, "100000")).status == "CREDIT"
    seen = await preview(committed_session_factory)
    start_together(monkeypatch, 1 + len(setup["residents"]))  # create_charge waits at its lock too

    async def penalty(unit_id: int) -> Any:
        async with committed_session_factory() as session:
            data = charges_service.ChargeInput(unit_id, "PENALTY", Decimal("100000"), date(2026, 10, 20), "Ruido")  # type: ignore[arg-type]
            return await charges_service.create_charge(session, data, Actor(setup["admin"], None))

    results = await gather(generate(committed_session_factory, setup, seen),
                           *(penalty(setup["unit_of"][r]) for r in setup["residents"]))

    assert all(not isinstance(r, Exception) for r in results), results
    assert consistent(results[0]).totals.created == UNITS
    await assert_never_over_allocated(committed_session_factory)
    for resident in setup["residents"]:
        async with committed_session_factory() as session:
            balance = await payment_service.unit_balance(session, setup["unit_of"][resident])
        # Fee 100.000 + penalty 100.000 - credit 100.000 (applied once, to one of them).
        assert (balance.pending_balance, balance.credit_balance) == (Decimal("100000.00"), Decimal("0.00"))


async def test_partial_run_then_a_new_run(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing apartment is rolled back alone; the others stay committed; a new run completes it."""
    broken_unit = setup["units"][4]
    real = charges_service._settle_credit

    async def broken_for_one(db: AsyncSession, unit_id: int) -> Any:
        if unit_id == broken_unit:
            raise RuntimeError("broken")
        return await real(db, unit_id)

    monkeypatch.setattr(charges_service, "_settle_credit", broken_for_one)
    first = consistent(await generate(committed_session_factory, setup, await preview(committed_session_factory)))
    monkeypatch.undo()

    assert (first.totals.created, first.totals.failed) == (UNITS - 1, 1)
    assert [e.unit_id for e in first.totals.errors] == [broken_unit]
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM charges WHERE unit_id = :u", u=broken_unit) == 0
    created_audits = "SELECT COUNT(*) FROM audit_logs WHERE action = 'CHARGE_CREATED' AND JSON_EXTRACT(context, '$.unit_id') = :u"
    assert await scalar(committed_session_factory, created_audits, u=broken_unit) == 0
    assert await scalar(committed_session_factory, created_audits, u=setup["units"][0]) == 1
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM charges") == UNITS - 1

    second = consistent(await generate(committed_session_factory, setup, await preview(committed_session_factory)))
    assert (second.totals.created, second.totals.existing, second.totals.failed) == (1, UNITS - 1, 0)
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM charges") == UNITS


def deadlock(code: int) -> Exception:
    from sqlalchemy.exc import OperationalError

    return OperationalError("INSERT ...", {}, Exception(code, "Deadlock found when trying to get lock"))


async def test_one_deadlock_is_retried_in_a_new_transaction(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    target = setup["units"][2]
    real = charges_service._create_locked
    attempts: list[int] = []

    async def first_attempt_aborts(db: AsyncSession, data: Any, *args: Any, **kwargs: Any) -> Any:
        if data.unit_id == target and not attempts:
            attempts.append(1)
            # Real work in the transaction that aborts: the retry must never reuse it.
            await db.execute(text("INSERT INTO audit_logs (action, entity_type) VALUES ('ABORTED_WORK', 'test')"))
            raise deadlock(1213)
        return await real(db, data, *args, **kwargs)

    monkeypatch.setattr(charges_service, "_create_locked", first_attempt_aborts)
    result = consistent(await generate(committed_session_factory, setup, await preview(committed_session_factory)))

    assert (result.totals.created, result.totals.failed) == (UNITS, 0)
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM charges WHERE unit_id = :u", u=target) == 1
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM audit_logs WHERE action = 'ABORTED_WORK'") == 0


async def test_two_deadlocks_fail_only_that_apartment(
    committed_session_factory: Factory, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    target = setup["units"][2]
    real = charges_service._create_locked
    calls: list[int] = []

    async def always(db: AsyncSession, data: Any, *args: Any, **kwargs: Any) -> Any:
        if data.unit_id == target:
            calls.append(1)
            raise deadlock(1213 if not calls[1:] else 1205)
        return await real(db, data, *args, **kwargs)

    monkeypatch.setattr(charges_service, "_create_locked", always)
    result = consistent(await generate(committed_session_factory, setup, await preview(committed_session_factory)))

    assert len(calls) == 2  # one retry, never more
    assert (result.totals.created, result.totals.failed) == (UNITS - 1, 1)
    assert result.totals.errors[0].unit_id == target and result.totals.errors[0].code == "LOCK_CONFLICT"
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM charges WHERE unit_id = :u", u=target) == 0
