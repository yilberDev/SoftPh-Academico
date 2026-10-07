"""Obligations extension: preview and generation of the monthly fees of a period
(MySQL _test database)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FieldValidationError, ForbiddenError, MonthlyPreviewOutdatedError
from app.models import AuditAction, AuditLog, Charge, Payment, PaymentAllocation, Unit, User
from app.services import charges as charges_service
from app.services.allocation import ChargeStatus
from app.services.charges import (
    ChargeInput,
    MonthlyGenerationInput,
    MonthlyGenerationResult,
    MonthlyPreview,
    create_charge,
    generate_monthly,
    preview_monthly,
)
from app.services.monthly_rules import MonthlyParams
from app.services.structure import Actor
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

TODAY = date(2026, 10, 15)
OCTOBER = MonthlyParams(date(2026, 10, 1), Decimal("180000"), date(2026, 10, 10))
FAILED_MESSAGE = "No fue posible generar la mensualidad de este apartamento."


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(charges_service, "bogota_today", lambda: TODAY)


@pytest.fixture
async def admin(make_user: Any) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
def actor(admin: User) -> Actor:
    return Actor(admin.id, "203.0.113.7")


@pytest.fixture
async def community(db_session: AsyncSession, admin: User) -> Community:
    return Community(db_session, admin.id)


@pytest.fixture
async def units(community: Community) -> list[int]:
    """Four active apartments (none has a resident: E2 does not require one)."""
    return [await community.unit(1, 1, 101), await community.unit(1, 1, 102),
            await community.unit(1, 2, 101), await community.unit(2, 1, 101)]


async def run(db: AsyncSession, actor: Actor, params: MonthlyParams = OCTOBER) -> MonthlyGenerationResult:
    """Preview, then confirm exactly what it showed."""
    preview = await preview_monthly(db, params)
    return await generate_monthly(db, MonthlyGenerationInput(params, preview.to_create, preview.preview_fingerprint), actor)


def consistent(result: MonthlyGenerationResult) -> MonthlyGenerationResult:
    t = result.totals
    assert t.existing + t.created + t.omitted + t.failed == t.units_evaluated
    return result


async def fees(db: AsyncSession, period: date = OCTOBER.billing_period) -> dict[int, tuple[Decimal, date]]:
    rows = await db.execute(select(Charge.unit_id, Charge.amount, Charge.due_date)
                            .where(Charge.billing_period == period, Charge.concept == "MONTHLY_FEE"))
    return {row.unit_id: (row.amount, row.due_date) for row in rows}


async def audits(db: AsyncSession, action: AuditAction) -> list[AuditLog]:
    return list((await db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))).all())


async def count(db: AsyncSession, model: Any) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def pay(db: AsyncSession, unit_id: int, admin_id: int, amount: str, paid: date = date(2026, 9, 1)) -> int:
    resident = await db.scalar(text("SELECT id FROM users WHERE role = 'RESIDENTE' LIMIT 1"))
    if resident is None:
        resident = (await db.execute(text(
            "INSERT INTO users (email, full_name, role, status, password_hash) "
            "VALUES ('pagos@example.com', 'Laura Gómez', 'RESIDENTE', 'ACTIVE', 'x')"))).lastrowid
    row = Payment(resident_id=resident, unit_id=unit_id, registered_by_user_id=admin_id, amount=Decimal(amount),
                  method="CASH", concept="MONTHLY_FEE", payment_date=paid, registered_at=datetime(2026, 9, 1, 15),
                  resident_name_snapshot="Laura Gómez", unit_label_snapshot="B1-TO1-APTO-101")
    db.add(row)
    await db.flush()
    return row.id


async def field_error(coroutine: Any) -> tuple[str, str]:
    with pytest.raises(FieldValidationError) as error:
        await coroutine
    [detail] = error.value.details or []
    return detail["field"], detail["message"]


# --- Preview -------------------------------------------------------------------------------------------------


async def test_preview(db_session: AsyncSession, units: list[int], actor: Actor) -> None:
    await create_charge(db_session, ChargeInput(units[0], "MONTHLY_FEE", Decimal("150000"), date(2026, 10, 5),  # type: ignore[arg-type]
                                                billing_period=date(2026, 10, 1)), actor)
    await db_session.execute(update(Unit).where(Unit.id == units[3]).values(deleted_at=datetime(2026, 10, 1)))
    before = (await count(db_session, Charge), await count(db_session, AuditLog))

    preview = await preview_monthly(db_session, OCTOBER)

    assert isinstance(preview, MonthlyPreview)
    assert (preview.billing_period, preview.amount, preview.due_date) == (date(2026, 10, 1), Decimal("180000.00"), date(2026, 10, 10))
    assert (preview.active_units, preview.existing, preview.missing, preview.to_create) == (3, 1, 2, 2)
    assert preview.total_amount == Decimal("360000.00")
    assert preview.existing_with_other_amount == 1  # the existing fee is 150.000
    assert len(preview.preview_fingerprint) == 64
    assert (await count(db_session, Charge), await count(db_session, AuditLog)) == before  # read only
    assert preview == await preview_monthly(db_session, OCTOBER)  # deterministic


async def test_preview_without_active_units(db_session: AsyncSession, actor: Actor) -> None:
    preview = await preview_monthly(db_session, OCTOBER)
    assert (preview.active_units, preview.to_create, preview.total_amount) == (0, 0, Decimal("0.00"))


@pytest.mark.parametrize("period", [date(1998, 1, 1), date(2019, 3, 1), date(2031, 12, 1), date(2100, 6, 1)])
async def test_historical_and_future_periods(db_session: AsyncSession, units: list[int], period: date) -> None:
    # No artificial range: any valid month.
    preview = await preview_monthly(db_session, MonthlyParams(period, Decimal("1000"), period))
    assert preview.to_create == 4


@pytest.mark.parametrize(
    ("params", "field", "message"),
    [
        (MonthlyParams(date(2026, 10, 2), Decimal("1"), date(2026, 10, 10)), "billing_period", "El periodo no es válido."),
        (MonthlyParams(datetime(2026, 10, 1), Decimal("1"), date(2026, 10, 10)), "billing_period", "El periodo no es válido."),
        (MonthlyParams(date(2026, 10, 1), Decimal("1"), date(2026, 9, 30)), "due_date",
         "El vencimiento no puede ser anterior al inicio del periodo."),
        (MonthlyParams(date(2026, 10, 1), Decimal("1"), datetime(2026, 10, 10)), "due_date", "La fecha de vencimiento no es válida."),
        (MonthlyParams(date(2026, 10, 1), Decimal("0"), date(2026, 10, 10)), "amount", "El monto debe ser mayor que cero."),
        (MonthlyParams(date(2026, 10, 1), Decimal("1.005"), date(2026, 10, 10)), "amount", "El monto admite hasta 2 decimales."),
        (MonthlyParams(date(2026, 10, 1), 180000.0, date(2026, 10, 10)), "amount", "El monto no es válido."),  # type: ignore[arg-type]
    ],
)
async def test_invalid_parameters(db_session: AsyncSession, actor: Actor, params: MonthlyParams, field: str, message: str) -> None:
    assert await field_error(preview_monthly(db_session, params)) == (field, message)
    data = MonthlyGenerationInput(params, 0, "x" * 64)
    assert await field_error(generate_monthly(db_session, data, actor)) == (field, message)


async def test_due_date_on_the_first_day_of_the_period(db_session: AsyncSession, units: list[int], actor: Actor) -> None:
    result = await run(db_session, actor, MonthlyParams(date(2026, 10, 1), Decimal("1000"), date(2026, 10, 1)))
    assert result.totals.created == 4


async def test_individual_monthly_fee_due_before_its_period(db_session: AsyncSession, units: list[int], actor: Actor) -> None:
    data = ChargeInput(units[0], "MONTHLY_FEE", Decimal("1"), date(2026, 9, 30), billing_period=date(2026, 10, 1))  # type: ignore[arg-type]
    assert (await field_error(create_charge(db_session, data, actor)))[0] == "due_date"


# --- Generation ----------------------------------------------------------------------------------------------


async def test_generation(db_session: AsyncSession, community: Community, units: list[int], actor: Actor) -> None:
    resident = await community.resident("Laura Gómez", "1012345678", (3, 1, 101), email="laura@example.com")
    with_resident = await db_session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": resident})
    deleted = await community.unit(3, 1, 102)
    await db_session.execute(update(Unit).where(Unit.id == deleted).values(deleted_at=datetime(2026, 10, 1)))

    result = consistent(await run(db_session, actor))

    t = result.totals
    assert (t.units_evaluated, t.created, t.existing, t.omitted, t.failed) == (5, 5, 0, 0, 0)
    assert (t.generated_total, t.outstanding_total, t.credit_applied_total) == (
        Decimal("900000.00"), Decimal("900000.00"), Decimal("0.00"))
    assert (t.omitted_units, t.errors, result.audit_recorded) == ((), (), True)
    # Every active apartment (with or without a resident), the same amount and due date; never the deleted one.
    assert await fees(db_session) == {u: (Decimal("180000.00"), date(2026, 10, 10)) for u in [*units, with_resident]}
    created = await audits(db_session, AuditAction.CHARGE_CREATED)
    assert len(created) == 5 and all(a.context["origin"] == "MONTHLY_GENERATION" for a in created)
    assert all(a.actor_user_id == actor.user_id for a in created)
    [summary] = await audits(db_session, AuditAction.MONTHLY_CHARGES_GENERATED)
    assert (summary.entity_type, summary.entity_id, summary.actor_user_id, summary.ip_address) == (
        "billing_period", None, actor.user_id, "203.0.113.7")
    assert summary.context == {
        "billing_period": "2026-10-01", "amount": "180000.00", "due_date": "2026-10-10", "active_units": 5,
        "created": 5, "existing": 0, "omitted": 0, "failed": 0, "generated_total": "900000.00",
        "credit_applied_total": "0.00", "omitted_unit_ids": [], "error_unit_ids": [],
    }


async def test_repeated_generation_is_idempotent(db_session: AsyncSession, units: list[int], actor: Actor) -> None:
    await run(db_session, actor)
    before = await fees(db_session)

    again = consistent(await run(db_session, actor))

    assert (again.totals.units_evaluated, again.totals.created, again.totals.existing) == (4, 0, 4)
    assert again.totals.generated_total == Decimal("0.00")
    assert await fees(db_session) == before
    assert len(await audits(db_session, AuditAction.CHARGE_CREATED)) == 4
    assert len(await audits(db_session, AuditAction.MONTHLY_CHARGES_GENERATED)) == 2  # also when nothing is created


async def test_existing_fees_are_never_changed(db_session: AsyncSession, units: list[int], actor: Actor) -> None:
    await create_charge(db_session, ChargeInput(units[0], "MONTHLY_FEE", Decimal("150000"), date(2026, 10, 5),  # type: ignore[arg-type]
                                                billing_period=date(2026, 10, 1)), actor)
    result = consistent(await run(db_session, actor))
    assert (result.totals.created, result.totals.existing) == (3, 1)
    assert (await fees(db_session))[units[0]] == (Decimal("150000.00"), date(2026, 10, 5))


async def test_a_new_apartment_is_included_in_the_next_run(
    db_session: AsyncSession, community: Community, units: list[int], actor: Actor
) -> None:
    await run(db_session, actor)
    new = await community.unit(2, 1, 102)
    result = consistent(await run(db_session, actor))
    assert (result.totals.units_evaluated, result.totals.created, result.totals.existing) == (5, 1, 4)
    assert new in await fees(db_session)


async def test_without_active_units(db_session: AsyncSession, actor: Actor) -> None:
    result = consistent(await run(db_session, actor))
    assert (result.totals.units_evaluated, result.totals.created, result.audit_recorded) == (0, 0, True)


async def test_historical_period(db_session: AsyncSession, units: list[int], actor: Actor) -> None:
    params = MonthlyParams(date(2025, 3, 1), Decimal("120000"), date(2025, 3, 10))
    result = consistent(await run(db_session, actor, params))
    assert result.totals.created == 4
    assert set((await fees(db_session, date(2025, 3, 1))).values()) == {(Decimal("120000.00"), date(2025, 3, 10))}


async def test_more_apartments_than_any_round_number(db_session: AsyncSession, community: Community, actor: Actor) -> None:
    """No fixed limit: 401 apartments (more than the 336 of the current scenario) in two blocks."""
    for i in range(401):
        await community.unit(1 + i // 200, 1 + (i % 200) // 50, 101 + i % 50)
    result = consistent(await run(db_session, actor, MonthlyParams(date(2026, 10, 1), Decimal("1000"), date(2026, 10, 10))))
    assert (result.totals.units_evaluated, result.totals.created) == (401, 401)
    assert result.totals.generated_total == Decimal("401000.00")
    assert len(await fees(db_session)) == 401


# --- Preview outdated ----------------------------------------------------------------------------------------


async def assert_outdated(db: AsyncSession, data: MonthlyGenerationInput, actor: Actor) -> MonthlyPreview:
    charges, audited = await count(db, Charge), await count(db, AuditLog)
    with pytest.raises(MonthlyPreviewOutdatedError) as error:
        await generate_monthly(db, data, actor)
    assert error.value.code == "MONTHLY_PREVIEW_OUTDATED"
    assert (await count(db, Charge), await count(db, AuditLog)) == (charges, audited)  # nothing created
    current = error.value.preview
    assert isinstance(current, MonthlyPreview)
    return current


async def test_outdated_when_the_count_changes(
    db_session: AsyncSession, community: Community, units: list[int], actor: Actor
) -> None:
    preview = await preview_monthly(db_session, OCTOBER)
    await community.unit(2, 1, 102)
    current = await assert_outdated(db_session, MonthlyGenerationInput(OCTOBER, preview.to_create, preview.preview_fingerprint), actor)
    assert current.to_create == 5


async def test_outdated_when_the_composition_changes_with_the_same_count(
    db_session: AsyncSession, community: Community, units: list[int], actor: Actor
) -> None:
    preview = await preview_monthly(db_session, OCTOBER)
    # One apartment deleted, another created: still 4 to create, but not the same 4.
    await db_session.execute(update(Unit).where(Unit.id == units[3]).values(deleted_at=datetime(2026, 10, 1)))
    await community.unit(2, 1, 102)
    current = await assert_outdated(db_session, MonthlyGenerationInput(OCTOBER, preview.to_create, preview.preview_fingerprint), actor)
    assert current.to_create == preview.to_create and current.preview_fingerprint != preview.preview_fingerprint


async def test_outdated_when_a_fee_was_created_meanwhile(
    db_session: AsyncSession, community: Community, units: list[int], actor: Actor
) -> None:
    preview = await preview_monthly(db_session, OCTOBER)
    await create_charge(db_session, ChargeInput(units[0], "MONTHLY_FEE", Decimal("180000"), date(2026, 10, 10),  # type: ignore[arg-type]
                                                billing_period=date(2026, 10, 1)), actor)
    await community.unit(2, 1, 102)  # same count to create, another composition
    await assert_outdated(db_session, MonthlyGenerationInput(OCTOBER, preview.to_create, preview.preview_fingerprint), actor)


async def test_outdated_with_other_parameters_or_values(db_session: AsyncSession, units: list[int], actor: Actor) -> None:
    preview = await preview_monthly(db_session, OCTOBER)
    other_amount = MonthlyParams(OCTOBER.billing_period, Decimal("190000"), OCTOBER.due_date)
    await assert_outdated(db_session, MonthlyGenerationInput(other_amount, preview.to_create, preview.preview_fingerprint), actor)
    await assert_outdated(db_session, MonthlyGenerationInput(OCTOBER, preview.to_create - 1, preview.preview_fingerprint), actor)
    await assert_outdated(db_session, MonthlyGenerationInput(OCTOBER, preview.to_create, "0" * 64), actor)


@pytest.mark.parametrize(("expected", "fingerprint", "field"), [
    (-1, "x", "expected_to_create"), (True, "x", "expected_to_create"), ("4", "x", "expected_to_create"),
    (4, "", "preview_fingerprint"), (4, None, "preview_fingerprint"),
])
async def test_invalid_confirmation(db_session: AsyncSession, actor: Actor, expected: Any, fingerprint: Any, field: str) -> None:
    data = MonthlyGenerationInput(OCTOBER, expected, fingerprint)
    assert (await field_error(generate_monthly(db_session, data, actor)))[0] == field


async def test_only_an_active_administrator_generates(db_session: AsyncSession, units: list[int], make_user: Any) -> None:
    resident = await make_user("residente@example.com", role="RESIDENTE", password_hash="x")
    preview = await preview_monthly(db_session, OCTOBER)
    with pytest.raises(ForbiddenError):
        await generate_monthly(db_session, MonthlyGenerationInput(OCTOBER, preview.to_create, preview.preview_fingerprint),
                               Actor(resident.id, None))
    assert await count(db_session, Charge) == 0


# --- Credit (saldo a favor) ----------------------------------------------------------------------------------


async def test_credit_is_applied_per_apartment(db_session: AsyncSession, units: list[int], admin: User, actor: Actor) -> None:
    exact = await pay(db_session, units[0], admin.id, "180000")
    partial = await pay(db_session, units[1], admin.id, "50000")
    larger = await pay(db_session, units[2], admin.id, "300000")
    # units[3]: an older pending charge is covered first (FIFO), then the new fee.
    older = (await create_charge(db_session, ChargeInput(units[3], "PENALTY", Decimal("30000"), date(2026, 8, 1), "Ruido"), actor)).id  # type: ignore[arg-type]
    fifo = await pay(db_session, units[3], admin.id, "100000")

    result = consistent(await run(db_session, actor))

    t = result.totals
    assert t.created == 4
    assert t.credit_applied_total == Decimal("180000.00") + Decimal("50000.00") + Decimal("180000.00") + Decimal("100000.00")
    assert t.credit_applied_to_new_charges == Decimal("180000.00") + Decimal("50000.00") + Decimal("180000.00") + Decimal("70000.00")
    assert t.units_with_credit_applied == 4
    assert t.outstanding_total == Decimal("0.00") + Decimal("130000.00") + Decimal("0.00") + Decimal("110000.00")
    applied = dict((await db_session.execute(
        select(PaymentAllocation.payment_id, func.sum(PaymentAllocation.amount)).group_by(PaymentAllocation.payment_id))).all())
    assert applied == {exact: Decimal("180000.00"), partial: Decimal("50000.00"), larger: Decimal("180000.00"), fifo: Decimal("100000.00")}
    assert await db_session.scalar(select(func.sum(PaymentAllocation.amount)).where(PaymentAllocation.charge_id == older)) == Decimal("30000.00")
    assert len(await audits(db_session, AuditAction.CREDIT_APPLIED)) == 5  # one per charge that received credit


async def test_historical_fees_are_overdue_and_first_in_fifo(
    db_session: AsyncSession, units: list[int], admin: User, actor: Actor
) -> None:
    await run(db_session, actor)  # October 2026
    await run(db_session, actor, MonthlyParams(date(2026, 3, 1), Decimal("100000"), date(2026, 3, 10)))  # historical
    created = await create_charge(db_session, ChargeInput(units[0], "OTHER", Decimal("1"), date(2027, 1, 1), "x"), actor)  # type: ignore[arg-type]
    assert created.status == ChargeStatus.PENDING
    march = await db_session.scalar(select(Charge.id).where(Charge.unit_id == units[0], Charge.billing_period == date(2026, 3, 1)))
    await pay(db_session, units[0], admin.id, "100000")
    await create_charge(db_session, ChargeInput(units[0], "OTHER", Decimal("1"), date(2027, 2, 1), "y"), actor)  # type: ignore[arg-type]
    # The credit went to March (oldest due date), not to October.
    assert await db_session.scalar(select(func.sum(PaymentAllocation.amount)).where(PaymentAllocation.charge_id == march)) == Decimal("100000.00")


# --- Isolation, retries and per-apartment outcomes -----------------------------------------------------------


async def test_a_failing_apartment_does_not_stop_the_others(
    db_session: AsyncSession, units: list[int], admin: User, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    await pay(db_session, units[1], admin.id, "50000")
    real = charges_service._settle_credit

    async def broken_for_one(db: AsyncSession, unit_id: int) -> Any:
        if unit_id == units[1]:
            raise RuntimeError("broken")
        return await real(db, unit_id)

    monkeypatch.setattr(charges_service, "_settle_credit", broken_for_one)
    result = consistent(await run(db_session, actor))

    t = result.totals
    assert (t.created, t.failed) == (3, 1)
    [error] = t.errors
    assert (error.unit_id, error.label, error.code, error.message) == (units[1], "B1-TO1-APTO-102", "INTERNAL_ERROR", FAILED_MESSAGE)
    # The failing apartment was left untouched: no charge, allocation or audit entry.
    assert units[1] not in await fees(db_session)
    assert await count(db_session, PaymentAllocation) == 0
    assert units[1] not in [a.context["unit_id"] for a in await audits(db_session, AuditAction.CHARGE_CREATED)]
    [summary] = await audits(db_session, AuditAction.MONTHLY_CHARGES_GENERATED)
    assert (summary.context["failed"], summary.context["error_unit_ids"]) == (1, [units[1]])

    monkeypatch.undo()
    monkeypatch.setattr(charges_service, "bogota_today", lambda: TODAY)
    retry = consistent(await run(db_session, actor))  # a new run completes it
    assert (retry.totals.created, retry.totals.existing, retry.totals.credit_applied_total) == (1, 3, Decimal("50000.00"))


def deadlock(code: int = 1213) -> OperationalError:
    return OperationalError("INSERT ...", {}, Exception(code, "Deadlock found when trying to get lock"))


async def test_deadlock_is_retried_once_in_a_new_transaction(
    db_session: AsyncSession, units: list[int], actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = charges_service._create_locked
    attempts: list[int] = []

    async def first_attempt_aborts(db: AsyncSession, data: ChargeInput, *args: Any, **kwargs: Any) -> Any:
        if data.unit_id == units[2] and not attempts:
            attempts.append(1)
            # Work done in the transaction that aborts: it must disappear, never be reused.
            await db.execute(text("INSERT INTO audit_logs (action, entity_type) VALUES ('ABORTED_WORK', 'test')"))
            raise deadlock()
        return await real(db, data, *args, **kwargs)

    monkeypatch.setattr(charges_service, "_create_locked", first_attempt_aborts)
    result = consistent(await run(db_session, actor))

    assert (result.totals.created, result.totals.failed) == (4, 0)
    assert units[2] in await fees(db_session)
    assert await db_session.scalar(text("SELECT COUNT(*) FROM audit_logs WHERE action = 'ABORTED_WORK'")) == 0


@pytest.mark.parametrize("code", [1213, 1205])
async def test_a_second_lock_conflict_fails_only_that_apartment(
    db_session: AsyncSession, units: list[int], actor: Actor, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    real = charges_service._create_locked
    calls: list[int] = []

    async def always(db: AsyncSession, data: ChargeInput, *args: Any, **kwargs: Any) -> Any:
        if data.unit_id == units[0]:
            calls.append(data.unit_id)
            raise deadlock(code)
        return await real(db, data, *args, **kwargs)

    monkeypatch.setattr(charges_service, "_create_locked", always)
    result = consistent(await run(db_session, actor))

    assert calls == [units[0], units[0]]  # exactly one retry
    assert (result.totals.created, result.totals.failed) == (3, 1)
    assert result.totals.errors[0].code == "LOCK_CONFLICT" and result.totals.errors[0].message == FAILED_MESSAGE


async def test_other_errors_are_not_retried(
    db_session: AsyncSession, units: list[int], actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = charges_service._create_locked
    calls: list[int] = []

    async def broken(db: AsyncSession, data: ChargeInput, *args: Any, **kwargs: Any) -> Any:
        if data.unit_id == units[0]:
            calls.append(1)
            raise OperationalError("SELECT 1", {}, Exception(2013, "Lost connection"))
        return await real(db, data, *args, **kwargs)

    monkeypatch.setattr(charges_service, "_create_locked", broken)
    result = consistent(await run(db_session, actor))
    assert calls == [1] and result.totals.errors[0].code == "INTERNAL_ERROR"


async def test_apartment_deleted_during_the_run_is_omitted(
    db_session: AsyncSession, units: list[int], actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = charges_service.lock_unit_path

    async def deleted_meanwhile(db: AsyncSession, unit_id: int, **kwargs: Any) -> Any:
        if unit_id == units[2]:
            await db.execute(update(Unit).where(Unit.id == unit_id).values(deleted_at=datetime(2026, 10, 1)))
        return await real(db, unit_id, **kwargs)

    monkeypatch.setattr(charges_service, "lock_unit_path", deleted_meanwhile)
    result = consistent(await run(db_session, actor))

    assert (result.totals.created, result.totals.omitted) == (3, 1)
    [omitted] = result.totals.omitted_units
    assert (omitted.unit_id, omitted.code, omitted.message) == (units[2], "UNIT_DELETED", "El apartamento fue eliminado.")
    assert units[2] not in await fees(db_session)


async def test_fee_created_meanwhile_counts_as_existing(
    db_session: AsyncSession, units: list[int], admin: User, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = charges_service.lock_unit_path

    async def created_meanwhile(db: AsyncSession, unit_id: int, **kwargs: Any) -> Any:
        if unit_id == units[1]:
            db.add(Charge(unit_id=unit_id, concept="MONTHLY_FEE", amount=Decimal("1"), due_date=date(2026, 10, 1),
                          billing_period=date(2026, 10, 1), created_by_user_id=admin.id))
            await db.flush()
        return await real(db, unit_id, **kwargs)

    monkeypatch.setattr(charges_service, "lock_unit_path", created_meanwhile)
    result = consistent(await run(db_session, actor))

    assert (result.totals.units_evaluated, result.totals.created, result.totals.existing) == (4, 3, 1)


async def test_summary_failure_keeps_the_fees(
    db_session: AsyncSession, units: list[int], actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = charges_service.record_audit

    def failing(db: AsyncSession, **kwargs: Any) -> Any:
        if kwargs["action"] == AuditAction.MONTHLY_CHARGES_GENERATED:
            raise RuntimeError("audit down")
        return real(db, **kwargs)

    monkeypatch.setattr(charges_service, "record_audit", failing)
    result = consistent(await run(db_session, actor))
    assert (result.totals.created, result.audit_recorded) == (4, False)
    assert len(await fees(db_session)) == 4


async def test_credit_of_several_payments_covers_a_future_fee(
    db_session: AsyncSession, units: list[int], admin: User, actor: Actor
) -> None:
    """D7: a fee not yet due (December) is covered by the credit; oldest payment first."""
    newer = await pay(db_session, units[0], admin.id, "60000", paid=date(2026, 9, 20))
    older = await pay(db_session, units[0], admin.id, "70000", paid=date(2026, 9, 2))
    december = MonthlyParams(date(2026, 12, 1), Decimal("100000"), date(2026, 12, 10))  # due after TODAY

    result = consistent(await run(db_session, actor, december))

    fee = await db_session.scalar(select(Charge.id).where(Charge.unit_id == units[0], Charge.billing_period == date(2026, 12, 1)))
    rows = (await db_session.execute(select(PaymentAllocation.payment_id, PaymentAllocation.amount)
                                     .where(PaymentAllocation.charge_id == fee).order_by(PaymentAllocation.id))).all()
    assert [tuple(r) for r in rows] == [(older, Decimal("70000.00")), (newer, Decimal("30000.00"))]
    t = result.totals
    assert (t.credit_applied_total, t.credit_applied_to_new_charges, t.units_with_credit_applied) == (
        Decimal("100000.00"), Decimal("100000.00"), 1)
    assert t.outstanding_total == Decimal("300000.00")  # the other three apartments have no credit
