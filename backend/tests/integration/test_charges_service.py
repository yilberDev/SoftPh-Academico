"""Obligations extension: create_charge (validation, origin of a late fee, credit,
transaction and audit) on the MySQL _test database."""

from dataclasses import fields
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    FieldValidationError,
    ForbiddenError,
    LateFeeExistsError,
    MonthlyFeeExistsError,
)
from app.models import AuditAction, AuditLog, Charge, Payment, PaymentAllocation, Unit, User, UserRole
from app.schemas.payment import PaymentCreate
from app.services import charges as charges_service
from app.services import payments as payment_service
from app.services.allocation import ChargeStatus, CreditAllocation
from app.services.charges import ChargeInput, CreatedCharge, create_charge
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

TODAY = date(2026, 10, 15)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    # "Today" in Bogotá, deterministic.
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
async def unit(community: Community) -> int:
    return await community.unit(1, 1, 101)


@pytest.fixture
async def other_unit(community: Community) -> int:
    return await community.unit(1, 1, 102)


def monthly(unit_id: int, amount: str = "100000", month: int = 10, due: date | None = None) -> ChargeInput:
    return ChargeInput(unit_id, "MONTHLY_FEE", Decimal(amount), due or date(2026, month, 5),  # type: ignore[arg-type]
                       billing_period=date(2026, month, 1))


def other(unit_id: int, concept: str = "PENALTY", amount: str = "50000", due: date = TOMORROW, **changes: Any) -> ChargeInput:
    values: dict[str, Any] = {"description": "Uso indebido de zona común"} | changes
    return ChargeInput(unit_id, concept, Decimal(amount), due, **values)  # type: ignore[arg-type]


def late_fee(unit_id: int, source: int | None = None, amount: str = "20000", **changes: Any) -> ChargeInput:
    return ChargeInput(unit_id, "LATE_FEE", Decimal(amount), TOMORROW, source_charge_id=source, **changes)  # type: ignore[arg-type]


async def pay(db: AsyncSession, unit_id: int, admin_id: int, amount: str, paid: date = date(2026, 9, 1),
              registered: datetime = datetime(2026, 9, 1, 15, 0)) -> int:
    """A payment of the apartment with no allocations (all of it is credit)."""
    resident = await db.scalar(select(User.id).where(User.role == UserRole.RESIDENTE).limit(1))
    if resident is None:
        resident = (await db.execute(text(
            "INSERT INTO users (email, full_name, role, status, password_hash) "
            "VALUES ('pagos@example.com', 'Laura Gómez', 'RESIDENTE', 'ACTIVE', 'x')"))).lastrowid
    row = Payment(resident_id=resident, unit_id=unit_id, registered_by_user_id=admin_id, amount=Decimal(amount),
                  method="CASH", concept="MONTHLY_FEE", payment_date=paid, registered_at=registered,
                  resident_name_snapshot="Laura Gómez", unit_label_snapshot="B1-TO1-APTO-101")
    db.add(row)
    await db.flush()
    return row.id


async def raw_charge(db: AsyncSession, unit_id: int, admin_id: int, amount: str, due: date,
                     created: datetime | None = None, concept: str = "OTHER") -> int:
    """A charge inserted directly (controlled created_at), without applying credit."""
    row = Charge(unit_id=unit_id, concept=concept, description="Cargo de prueba" if concept != "MONTHLY_FEE" else None,
                 amount=Decimal(amount), due_date=due, billing_period=due.replace(day=1) if concept == "MONTHLY_FEE" else None,
                 created_by_user_id=admin_id, **({"created_at": created} if created else {}))
    db.add(row)
    await db.flush()
    return row.id


async def allocations(db: AsyncSession, **where: int) -> list[tuple[int, int, Decimal]]:
    query = select(PaymentAllocation.payment_id, PaymentAllocation.charge_id, PaymentAllocation.amount)
    for column, value in where.items():
        query = query.where(getattr(PaymentAllocation, column) == value)
    return [tuple(r) for r in (await db.execute(query.order_by(PaymentAllocation.id))).all()]  # type: ignore[misc]


async def applied_to(db: AsyncSession, charge_id: int) -> Decimal:
    return Decimal(await db.scalar(select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
                                   .where(PaymentAllocation.charge_id == charge_id)))


async def credit_left(db: AsyncSession, payment_id: int) -> Decimal:
    amount = await db.scalar(select(Payment.amount).where(Payment.id == payment_id))
    used = await db.scalar(select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
                           .where(PaymentAllocation.payment_id == payment_id))
    return Decimal(amount) - Decimal(used)


async def audits(db: AsyncSession, action: AuditAction) -> list[AuditLog]:
    return list((await db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))).all())


async def counts(db: AsyncSession) -> tuple[int, int, int]:
    charges = await db.scalar(select(func.count()).select_from(Charge))
    allocated = await db.scalar(select(func.count()).select_from(PaymentAllocation))
    audited = await db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action.in_([AuditAction.CHARGE_CREATED, AuditAction.CREDIT_APPLIED])))
    return int(charges or 0), int(allocated or 0), int(audited or 0)


async def field_error(db: AsyncSession, data: ChargeInput, actor: Actor) -> tuple[str, str]:
    with pytest.raises(FieldValidationError) as error:
        await create_charge(db, data, actor)
    [detail] = error.value.details or []
    return detail["field"], detail["message"]


# --- 1-10. Creation ------------------------------------------------------------------------------------------


async def test_monthly_fee(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 1
    created = await create_charge(db_session, monthly(unit, due=date(2026, 10, 20)), actor)

    assert isinstance(created, CreatedCharge)
    assert (created.concept, created.amount, created.billing_period, created.description) == (
        "MONTHLY_FEE", Decimal("100000.00"), date(2026, 10, 1), None)
    assert (created.unit_label, created.status, created.applied_amount, created.outstanding_amount) == (
        "B1-TO1-APTO-101", ChargeStatus.PENDING, Decimal("0.00"), Decimal("100000.00"))
    assert created.credit_applied == () and created.created_at is not None
    assert (created.account.pending_balance, created.account.credit_balance) == (Decimal("100000.00"), Decimal("0.00"))
    row = await db_session.get(Charge, created.id, populate_existing=True)
    assert row is not None and (row.unit_id, row.due_date, row.billing_period, row.source_charge_id) == (
        unit, date(2026, 10, 20), date(2026, 10, 1), None)
    [audit] = await audits(db_session, AuditAction.CHARGE_CREATED)
    assert (audit.entity_type, audit.entity_id, audit.actor_user_id, audit.ip_address) == (
        "charge", created.id, actor.user_id, "203.0.113.7")
    assert audit.context == {"concept": "MONTHLY_FEE", "amount": "100000.00", "due_date": "2026-10-20",
                             "billing_period": "2026-10-01", "unit_id": unit, "label": "B1-TO1-APTO-101",
                             "source_charge_id": None}
    assert await audits(db_session, AuditAction.CREDIT_APPLIED) == []


async def test_historical_late_fee(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 2
    created = await create_charge(db_session, late_fee(unit), actor)
    assert (created.concept, created.source_charge_id, created.description) == ("LATE_FEE", None, None)
    second = await create_charge(db_session, late_fee(unit, description="Mora de 2025"), actor)
    assert second.description == "Mora de 2025"


async def test_late_fee_with_a_valid_origin(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 3, 16
    origin = await create_charge(db_session, monthly(unit, month=9), actor)  # due 2026-09-05 < today
    created = await create_charge(db_session, late_fee(unit, origin.id), actor)
    assert created.source_charge_id == origin.id
    [_, audit] = await audits(db_session, AuditAction.CHARGE_CREATED)
    assert audit.context["source_charge_id"] == origin.id


@pytest.mark.parametrize("concept", ["PENALTY", "OTHER"])
async def test_penalty_and_other(db_session: AsyncSession, unit: int, actor: Actor, concept: str) -> None:  # 4, 5
    created = await create_charge(db_session, other(unit, concept, description="  Llave perdida  "), actor)
    assert (created.concept, created.description, created.billing_period) == (concept, "Llave perdida", None)


@pytest.mark.parametrize("concept", ["PENALTY", "OTHER"])
@pytest.mark.parametrize("description", [None, "", "   "])
async def test_description_is_required(  # 6
    db_session: AsyncSession, unit: int, actor: Actor, concept: str, description: str | None
) -> None:
    assert (await field_error(db_session, other(unit, concept, description=description), actor))[0] == "description"


async def test_description_rules(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 7
    with_description = ChargeInput(unit, "MONTHLY_FEE", Decimal("1"), TOMORROW, "Octubre", date(2026, 10, 1))  # type: ignore[arg-type]
    assert await field_error(db_session, with_description, actor) == ("description", "La mensualidad no lleva descripción.")
    too_long = other(unit, description="x" * 201)
    assert (await field_error(db_session, too_long, actor))[0] == "description"
    assert (await create_charge(db_session, other(unit, description="x" * 200), actor)).description == "x" * 200


async def test_billing_period_rules(db_session: AsyncSession, unit: int, actor: Actor) -> None:
    def fee(period: Any, concept: str = "MONTHLY_FEE") -> ChargeInput:
        return ChargeInput(unit, concept, Decimal("1"), TOMORROW, None, period)  # type: ignore[arg-type]

    assert await field_error(db_session, fee(None), actor) == ("billing_period", "El periodo es obligatorio para la mensualidad.")
    assert (await field_error(db_session, fee(date(2026, 10, 2)), actor))[0] == "billing_period"
    assert (await field_error(db_session, fee(datetime(2026, 10, 1)), actor))[0] == "billing_period"
    assert await field_error(db_session, fee(date(2026, 10, 1), "LATE_FEE"), actor) == (
        "billing_period", "Solo la mensualidad tiene periodo.")


@pytest.mark.parametrize(
    ("amount", "field"),
    [(Decimal("0"), "amount"), (Decimal("-1"), "amount"), (Decimal("0.001"), "amount"), (Decimal("10000000000"), "amount"),
     (Decimal("NaN"), "amount"), (100000.0, "amount"), (100000, "amount")],
)
async def test_invalid_amount(db_session: AsyncSession, unit: int, actor: Actor, amount: Any, field: str) -> None:
    data = ChargeInput(unit, "PENALTY", amount, TOMORROW, "Ruido")  # type: ignore[arg-type]
    assert (await field_error(db_session, data, actor))[0] == field


async def test_invalid_concept_and_due_date(db_session: AsyncSession, unit: int, actor: Actor) -> None:
    assert (await field_error(db_session, other(unit, "RENT"), actor))[0] == "concept"
    assert (await field_error(db_session, other(unit, due=datetime(2026, 10, 20)), actor))[0] == "due_date"  # type: ignore[arg-type]


async def test_missing_unit(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 8
    assert await field_error(db_session, other(999999), actor) == ("unit_id", "Selecciona un apartamento existente.")
    assert (await field_error(db_session, other(0), actor))[0] == "unit_id"


async def test_deleted_unit(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 9
    await db_session.execute(update(Unit).where(Unit.id == unit).values(deleted_at=datetime(2026, 10, 1)))
    assert await field_error(db_session, other(unit), actor) == ("unit_id", "El apartamento fue eliminado.")
    assert await counts(db_session) == (0, 0, 0)


async def test_unit_deleted_after_the_first_read(
    db_session: AsyncSession, unit: int, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = charges_service.lock_unit_path

    async def deleted_meanwhile(db: AsyncSession, unit_id: int, **kwargs: Any) -> Any:
        await db.execute(update(Unit).where(Unit.id == unit_id).values(deleted_at=datetime(2026, 10, 1)))
        return await real(db, unit_id, **kwargs)

    monkeypatch.setattr(charges_service, "lock_unit_path", deleted_meanwhile)
    assert await field_error(db_session, other(unit), actor) == ("unit_id", "El apartamento fue eliminado.")
    assert await counts(db_session) == (0, 0, 0)


async def test_the_author_is_the_actor(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 10
    # No "on behalf of" parameter: the author is always the authenticated actor.
    assert "created_by_user_id" not in {f.name for f in fields(ChargeInput)}
    created = await create_charge(db_session, other(unit), actor)
    assert created.created_by_user_id == actor.user_id
    assert await db_session.scalar(select(Charge.created_by_user_id).where(Charge.id == created.id)) == actor.user_id


async def test_only_an_active_administrator_creates(
    db_session: AsyncSession, unit: int, admin: User, make_user: Any
) -> None:
    resident = await make_user("residente@example.com", role=UserRole.RESIDENTE, password_hash="x")
    for actor in (Actor(resident.id, None), Actor(999999, None)):
        with pytest.raises(ForbiddenError):
            await create_charge(db_session, other(unit), actor)
    # The same administrator, disabled and then deleted.
    for change in ({"status": "DISABLED", "deleted_at": None}, {"status": "ACTIVE", "deleted_at": datetime(2026, 10, 1)}):
        await db_session.execute(update(User).where(User.id == admin.id).values(**change))
        with pytest.raises(ForbiddenError):
            await create_charge(db_session, other(unit), Actor(admin.id, None))
    assert await counts(db_session) == (0, 0, 0)


async def test_duplicate_monthly_fee(db_session: AsyncSession, unit: int, other_unit: int, actor: Actor) -> None:
    await create_charge(db_session, monthly(unit), actor)
    with pytest.raises(MonthlyFeeExistsError, match="B1-TO1-APTO-101 ya tiene la mensualidad de 2026-10"):
        await create_charge(db_session, monthly(unit, amount="5000"), actor)
    await create_charge(db_session, monthly(other_unit), actor)  # same period, another apartment
    await create_charge(db_session, monthly(unit, month=11), actor)  # another period
    assert (await counts(db_session))[0] == 3


async def test_duplicate_monthly_fee_caught_by_the_unique_index(
    db_session: AsyncSession, unit: int, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the check missed a concurrent winner, the UNIQUE index gives the same 409 and nothing is left."""
    await create_charge(db_session, monthly(unit), actor)
    real_scalar = db_session.scalar

    async def blind(statement: Any, *args: Any, **kwargs: Any) -> Any:
        if "billing_period" in str(statement) and "source_charge_id" not in str(statement):
            return None  # the existence check sees nothing
        return await real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", blind)
    with pytest.raises(MonthlyFeeExistsError):
        await create_charge(db_session, monthly(unit, amount="5000"), actor)
    monkeypatch.undo()
    assert await counts(db_session) == (1, 0, 1)


# --- 11-17. Origin of a late fee -----------------------------------------------------------------------------


async def test_missing_origin(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 11
    assert await field_error(db_session, late_fee(unit, 999999), actor) == (
        "source_charge_id", "La obligación de origen no existe.")


async def test_origin_of_another_apartment(db_session: AsyncSession, unit: int, other_unit: int, actor: Actor) -> None:  # 12
    foreign = await create_charge(db_session, monthly(other_unit, month=9), actor)
    assert await field_error(db_session, late_fee(unit, foreign.id), actor) == (
        "source_charge_id", "La obligación de origen es de otro apartamento.")


async def test_origin_that_is_a_late_fee(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 13
    historical = await create_charge(db_session, ChargeInput(unit, "LATE_FEE", Decimal("1"), YESTERDAY), actor)  # type: ignore[arg-type]
    assert await field_error(db_session, late_fee(unit, historical.id), actor) == (
        "source_charge_id", "Una mora no puede originar otra mora.")


@pytest.mark.parametrize("due", [TODAY, TOMORROW, date(2027, 1, 5)])
async def test_origin_not_yet_overdue(db_session: AsyncSession, unit: int, actor: Actor, due: date) -> None:  # 14, 15
    origin = await create_charge(db_session, other(unit, due=due), actor)
    assert await field_error(db_session, late_fee(unit, origin.id), actor) == (
        "source_charge_id", "La obligación de origen todavía no está vencida.")


async def test_origin_overdue_since_yesterday(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 16
    origin = await create_charge(db_session, other(unit, due=YESTERDAY), actor)
    assert (await create_charge(db_session, late_fee(unit, origin.id), actor)).source_charge_id == origin.id


async def test_one_late_fee_per_origin(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 17 (D9)
    origin = await create_charge(db_session, monthly(unit, month=9), actor)
    await create_charge(db_session, late_fee(unit, origin.id), actor)
    with pytest.raises(LateFeeExistsError):
        await create_charge(db_session, late_fee(unit, origin.id, amount="5000"), actor)
    # Historical late fees are not limited.
    await create_charge(db_session, late_fee(unit), actor)
    await create_charge(db_session, late_fee(unit), actor)
    assert await db_session.scalar(select(func.count()).select_from(Charge).where(Charge.concept == "LATE_FEE")) == 3


@pytest.mark.parametrize("concept", ["MONTHLY_FEE", "PENALTY", "OTHER"])
async def test_only_a_late_fee_has_an_origin(db_session: AsyncSession, unit: int, actor: Actor, concept: str) -> None:
    origin = await create_charge(db_session, other(unit, due=YESTERDAY), actor)
    period, description = (date(2026, 10, 1), None) if concept == "MONTHLY_FEE" else (None, "Detalle")
    data = ChargeInput(unit, concept, Decimal("1"), TOMORROW, description, period, origin.id)  # type: ignore[arg-type]
    assert (await field_error(db_session, data, actor))[0] == "source_charge_id"


# --- 18-28. Credit (saldo a favor) ---------------------------------------------------------------------------


async def test_without_credit_nothing_is_allocated(db_session: AsyncSession, unit: int, actor: Actor) -> None:  # 18
    created = await create_charge(db_session, monthly(unit), actor)
    assert (created.credit_applied, created.status) == ((), ChargeStatus.OVERDUE)  # due 2026-10-05 < today
    assert await allocations(db_session) == []


async def test_exact_credit_pays_the_charge(db_session: AsyncSession, unit: int, admin: User, actor: Actor) -> None:  # 19
    payment = await pay(db_session, unit, admin.id, "100000")
    created = await create_charge(db_session, monthly(unit), actor)

    assert await allocations(db_session) == [(payment, created.id, Decimal("100000.00"))]
    assert (created.status, created.applied_amount, created.outstanding_amount) == (
        ChargeStatus.PAID, Decimal("100000.00"), Decimal("0.00"))
    assert [(c.payment_id, c.receipt_number, c.charge_id, c.amount) for c in created.credit_applied] == [
        (payment, f"RC-{payment:08d}", created.id, Decimal("100000.00"))]
    assert await credit_left(db_session, payment) == Decimal("0.00")
    assert (created.account.pending_balance, created.account.credit_balance) == (Decimal("0.00"), Decimal("0.00"))
    [audit] = await audits(db_session, AuditAction.CREDIT_APPLIED)
    assert (audit.entity_type, audit.entity_id, audit.actor_user_id) == ("charge", created.id, actor.user_id)
    assert audit.context == {"unit_id": unit, "charge_id": created.id, "total": "100000.00",
                             "payments": [{"payment_id": payment, "receipt_number": f"RC-{payment:08d}", "amount": "100000.00"}]}


async def test_partial_credit(db_session: AsyncSession, unit: int, admin: User, actor: Actor) -> None:  # 20
    payment = await pay(db_session, unit, admin.id, "30000")
    created = await create_charge(db_session, other(unit, amount="100000"), actor)
    assert (created.status, created.applied_amount, created.outstanding_amount) == (
        ChargeStatus.PARTIAL, Decimal("30000.00"), Decimal("70000.00"))
    assert await credit_left(db_session, payment) == Decimal("0.00")
    assert (created.account.pending_balance, created.account.credit_balance) == (Decimal("70000.00"), Decimal("0.00"))


async def test_larger_credit_leaves_credit(db_session: AsyncSession, unit: int, admin: User, actor: Actor) -> None:  # 21
    payment = await pay(db_session, unit, admin.id, "250000")
    created = await create_charge(db_session, monthly(unit), actor)
    assert (created.status, created.applied_amount) == (ChargeStatus.PAID, Decimal("100000.00"))
    assert await credit_left(db_session, payment) == Decimal("150000.00")
    assert (created.account.pending_balance, created.account.credit_balance) == (Decimal("0.00"), Decimal("150000.00"))


async def test_several_payments_with_credit(db_session: AsyncSession, unit: int, admin: User, actor: Actor) -> None:  # 22
    newer = await pay(db_session, unit, admin.id, "60000", paid=date(2026, 9, 20))
    older = await pay(db_session, unit, admin.id, "50000", paid=date(2026, 9, 2))
    same_day_later = await pay(db_session, unit, admin.id, "10000", paid=date(2026, 9, 2), registered=datetime(2026, 9, 2, 18))
    created = await create_charge(db_session, monthly(unit), actor)

    # Oldest payment first: payment_date, then registered_at, then id.
    assert await allocations(db_session) == [
        (older, created.id, Decimal("50000.00")), (same_day_later, created.id, Decimal("10000.00")),
        (newer, created.id, Decimal("40000.00")),
    ]
    assert await credit_left(db_session, newer) == Decimal("20000.00")
    [audit] = await audits(db_session, AuditAction.CREDIT_APPLIED)
    assert audit.context["total"] == "100000.00" and [p["payment_id"] for p in audit.context["payments"]] == [
        older, same_day_later, newer]


async def test_credit_reaches_older_pending_charges_first(  # 23, 24
    db_session: AsyncSession, unit: int, admin: User, actor: Actor
) -> None:
    # Pending charges created while there was no credit (directly, without credit settlement).
    january = await raw_charge(db_session, unit, admin.id, "40000", date(2026, 1, 5))
    march = await raw_charge(db_session, unit, admin.id, "40000", date(2026, 3, 5))
    payment = await pay(db_session, unit, admin.id, "100000")
    created = await create_charge(db_session, monthly(unit), actor)  # due 2026-10-05

    assert await allocations(db_session) == [
        (payment, january, Decimal("40000.00")), (payment, march, Decimal("40000.00")),
        (payment, created.id, Decimal("20000.00")),
    ]
    assert (created.status, created.applied_amount, created.outstanding_amount) == (
        ChargeStatus.OVERDUE, Decimal("20000.00"), Decimal("80000.00"))  # partial and overdue
    assert [a.entity_id for a in await audits(db_session, AuditAction.CREDIT_APPLIED)] == [january, march, created.id]
    assert {c.charge_id for c in created.credit_applied} == {january, march, created.id}


async def test_credit_ties_use_created_at_then_id(db_session: AsyncSession, unit: int, admin: User, actor: Actor) -> None:
    due = date(2026, 12, 5)
    later = await raw_charge(db_session, unit, admin.id, "10000", due, datetime(2026, 9, 1, 12, 0, 1))
    first_a = await raw_charge(db_session, unit, admin.id, "10000", due, datetime(2026, 9, 1, 12, 0, 0))
    first_b = await raw_charge(db_session, unit, admin.id, "10000", due, datetime(2026, 9, 1, 12, 0, 0))
    payment = await pay(db_session, unit, admin.id, "25000")
    await create_charge(db_session, other(unit, due=date(2027, 1, 5)), actor)
    assert await allocations(db_session) == [
        (payment, first_a, Decimal("10000.00")), (payment, first_b, Decimal("10000.00")), (payment, later, Decimal("5000.00"))]


async def test_credit_covers_a_future_charge(db_session: AsyncSession, unit: int, admin: User, actor: Actor) -> None:  # 25 (D7)
    payment = await pay(db_session, unit, admin.id, "100000")
    created = await create_charge(db_session, monthly(unit, month=12), actor)  # due 2026-12-05 > today
    assert await allocations(db_session) == [(payment, created.id, Decimal("100000.00"))]
    assert created.status == ChargeStatus.PAID


async def test_never_negative_or_over_allocated(  # 26, 27, 28
    db_session: AsyncSession, unit: int, other_unit: int, admin: User, actor: Actor
) -> None:
    payments = [await pay(db_session, unit, admin.id, amount, paid=date(2026, 9, day))
                for day, amount in ((1, "0.01"), (2, "33333.33"), (3, "7"))]
    foreign = await pay(db_session, other_unit, admin.id, "500000")  # another apartment's credit is never used
    for amount in ("12345.67", "0.02", "50000"):
        await create_charge(db_session, other(unit, amount=amount), actor)
    rows = await allocations(db_session)
    assert rows and all(amount > 0 for _, _, amount in rows)
    assert all(payment_id != foreign for payment_id, _, _ in rows)
    for payment_id in payments:
        assert await credit_left(db_session, payment_id) >= 0
    for charge_id in {charge_id for _, charge_id, _ in rows}:
        amount = await db_session.scalar(select(Charge.amount).where(Charge.id == charge_id))
        assert await applied_to(db_session, charge_id) <= amount
    assert sum(amount for _, _, amount in rows) == Decimal("33340.34")  # all the credit, never more
    assert await credit_left(db_session, foreign) == Decimal("500000.00")


async def test_existing_full_credit_payment_is_consumed(  # section 17: the development scenario
    db_session: AsyncSession, community: Community, actor: Actor
) -> None:
    """A payment registered (HU-004) when the apartment had no charges is 100 % credit;
    the next obligation consumes it automatically."""
    resident = await community.resident("Laura Gómez", "1012345678", (1, 2, 104), email="laura@example.com")
    unit_id = await db_session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": resident})
    registered = await payment_service.register_payment(
        db_session,
        PaymentCreate.model_validate({"resident_id": resident, "amount": "250000", "method": "CASH",
                                      "concept": "MONTHLY_FEE", "payment_date": "2026-10-01"}),
        actor, mailer=FakeMailSender(),
    )
    assert (registered.status, registered.applied_amount, registered.credit_amount) == ("CREDIT", Decimal("0.00"), Decimal("250000.00"))

    created = await create_charge(db_session, monthly(int(unit_id), amount="180000", month=11), actor)

    assert await allocations(db_session) == [(registered.id, created.id, Decimal("180000.00"))]
    assert (created.applied_amount, created.outstanding_amount, created.status) == (
        Decimal("180000.00"), Decimal("0.00"), ChargeStatus.PAID)
    assert await credit_left(db_session, registered.id) == Decimal("70000.00")
    assert (created.account.pending_balance, created.account.credit_balance) == (Decimal("0.00"), Decimal("70000.00"))
    # The payment's derived status changed: it is now partially applied (HU-004 summary).
    page = await payment_service.list_payments(db_session, payment_service.PaymentListQuery())
    assert [(p.id, p.status, p.applied_amount) for p in page.items] == [(registered.id, "PARTIALLY_APPLIED", Decimal("180000.00"))]


# --- 29-32. Transaction and audit ----------------------------------------------------------------------------


async def test_a_later_failure_leaves_nothing(  # 29
    db_session: AsyncSession, unit: int, admin: User, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    await pay(db_session, unit, admin.id, "100000")

    async def broken(*_args: Any) -> Any:
        raise RuntimeError("settlement failed")

    monkeypatch.setattr(charges_service, "_settle_credit", broken)
    with pytest.raises(RuntimeError, match="settlement failed"):
        await create_charge(db_session, monthly(unit), actor)
    assert await counts(db_session) == (0, 0, 0)


async def test_an_over_allocation_is_rolled_back(  # 30
    db_session: AsyncSession, unit: int, admin: User, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    payment = await pay(db_session, unit, admin.id, "100000")

    def wrong(credits: Any, charges: Any) -> tuple[CreditAllocation, ...]:
        # More than the charge (and than the payment): the invariant guard must stop it.
        return (CreditAllocation(payment, charges[0].id, Decimal("150000.00")),)

    monkeypatch.setattr(charges_service, "allocate_credit", wrong)
    with pytest.raises(RuntimeError, match="invariant"):
        await create_charge(db_session, monthly(unit), actor)
    assert await counts(db_session) == (0, 0, 0)
    assert await credit_left(db_session, payment) == Decimal("100000.00")


async def test_an_audit_failure_rolls_everything_back(  # 31
    db_session: AsyncSession, unit: int, admin: User, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    await pay(db_session, unit, admin.id, "100000")
    real = charges_service.record_audit

    def failing(db: AsyncSession, **kwargs: Any) -> Any:
        if kwargs["action"] == AuditAction.CREDIT_APPLIED:
            raise RuntimeError("audit failed")
        return real(db, **kwargs)

    monkeypatch.setattr(charges_service, "record_audit", failing)
    with pytest.raises(RuntimeError, match="audit failed"):
        await create_charge(db_session, monthly(unit), actor)
    assert await counts(db_session) == (0, 0, 0)


async def test_a_commit_failure_rolls_everything_back(
    db_session: AsyncSession, unit: int, admin: User, actor: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    await pay(db_session, unit, admin.id, "100000")
    real_commit = db_session.commit
    calls = 0

    async def failing_last_commit() -> None:
        nonlocal calls
        calls += 1
        if calls == 3:  # the commit of the locked transaction (after two fresh-view commits)
            raise RuntimeError("commit failed")
        await real_commit()

    monkeypatch.setattr(db_session, "commit", failing_last_commit)
    with pytest.raises(RuntimeError, match="commit failed"):
        await create_charge(db_session, monthly(unit), actor)
    monkeypatch.undo()
    assert await counts(db_session) == (0, 0, 0)


async def test_audit_only_after_success(db_session: AsyncSession, unit: int, admin: User, actor: Actor) -> None:  # 32
    for data in (other(unit, description=None), late_fee(unit, 999999), other(999999)):
        with pytest.raises(FieldValidationError):
            await create_charge(db_session, data, actor)
    assert await counts(db_session) == (0, 0, 0)
    await pay(db_session, unit, admin.id, "10")
    created = await create_charge(db_session, other(unit), actor)
    assert [a.entity_id for a in await audits(db_session, AuditAction.CHARGE_CREATED)] == [created.id]
    assert [a.entity_id for a in await audits(db_session, AuditAction.CREDIT_APPLIED)] == [created.id]
