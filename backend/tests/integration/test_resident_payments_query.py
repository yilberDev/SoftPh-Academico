"""HU-008: the resident's payments (service), on the test database.

Payments are registered with the real HU-004 service (FIFO, credit, status), so
what the resident reads is exactly what the administrator registered.
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import bogota_today
from app.core.exceptions import PaymentNotFoundError
from app.models import FinancialConcept, Payment, PaymentAllocation, PaymentMethod
from app.schemas.payment import PaymentCreate, PaymentListQuery
from app.services import charges as charge_service
from app.services import payments as payment_service
from app.services.allocation import PaymentStatus, receipt_number
from app.services.resident_payments import (
    PaymentAllocationSummary,
    PaymentUnit,
    ResidentPaymentQuery,
    get_resident_payment,
    list_resident_payments,
)
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender
from tests.integration.test_payments_api import charge, unit_of
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

TODAY = bogota_today()


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


class World:
    """Laura (B1-TO1-APTO-101) with 4 payments of every status, Pedro with 1, and an apartment
    (B1-TO1-APTO-103) where Ana lived and paid before Bruno moved in and paid."""

    def __init__(self, db: AsyncSession, admin_id: int) -> None:
        self.db = db
        self.actor = Actor(user_id=admin_id, ip_address=None)
        self.mailer = FakeMailSender()
        self.ids: dict[str, int] = {}

    async def pay(self, resident_id: int, amount: str, days_ago: int, **fields: Any) -> int:
        data = PaymentCreate(resident_id=resident_id, amount=amount, method=fields.pop("method", "TRANSFER"),
                             concept=fields.pop("concept", "MONTHLY_FEE"), payment_date=ago(days_ago).isoformat(), **fields)
        registered = await payment_service.register_payment(self.db, data, self.actor, mailer=self.mailer)
        return registered.id


@pytest.fixture
async def world(db_session: AsyncSession, make_user: Any) -> World:
    admin = await make_user("admin@example.com", password_hash="not-a-real-hash")
    community = Community(db_session, admin.id)
    w = World(db_session, admin.id)
    ids = w.ids
    ids["laura"] = await community.resident("Laura Gómez", "1012345678", (1, 1, 101))
    ids["pedro"] = await community.resident("Pedro Ruiz", "1012345679", (1, 1, 102))
    ids["ana"] = await community.resident("Ana Pérez", "1012345680", None)
    ids["bruno"] = await community.resident("Bruno Díaz", "1012345681", None)
    ids["shared_unit"] = await community.unit(1, 1, 103)
    await community.residence(ids["ana"], ids["shared_unit"])

    laura_unit = await unit_of(db_session, ids["laura"])
    ids["laura_unit"] = laura_unit
    ids["fee"] = await charge(db_session, laura_unit, "100000", date(2026, 1, 5))  # MONTHLY_FEE, period 2026-01
    ids["penalty"] = await charge(db_session, laura_unit, "50000", date(2026, 2, 10), "PENALTY", "Ruido")

    ids["p1"] = await w.pay(ids["laura"], "100000", 30)  # covers the fee: APPLIED
    ids["p2"] = await w.pay(ids["laura"], "80000", 20, concept="PENALTY")  # 50.000 to the penalty, 30.000 credit
    ids["p3"] = await w.pay(ids["laura"], "40000", 10, method="CASH", transaction_reference="TRF-2026-0042",
                            notes="Pagó en la portería.\nTrajo el soporte.")  # nothing pending: CREDIT
    ids["p4"] = await w.pay(ids["laura"], "10000", 10)  # same date as p3: the higher id first
    ids["p5"] = await w.pay(ids["pedro"], "25000", 10)
    ids["p6"] = await w.pay(ids["ana"], "60000", 40)
    await db_session.execute(text("UPDATE resident_units SET ended_at = UTC_TIMESTAMP(6) WHERE user_id = :u"),
                             {"u": ids["ana"]})
    await community.residence(ids["bruno"], ids["shared_unit"])
    ids["p7"] = await w.pay(ids["bruno"], "70000", 5)
    return w


async def listed(db: AsyncSession, resident_id: int, **query: Any) -> list[int]:
    page = await list_resident_payments(db, resident_id, ResidentPaymentQuery(**query))
    return [item.id for item in page.items]


# --- 1-4. Ownership ----------------------------------------------------------------------------------------------------


async def test_a_resident_lists_only_their_own_payments(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    assert await listed(db_session, i["laura"]) == [i["p4"], i["p3"], i["p2"], i["p1"]]
    assert await listed(db_session, i["pedro"]) == [i["p5"]]
    # Same apartment, different residents: the apartment is never the owner.
    assert await listed(db_session, i["bruno"]) == [i["p7"]]
    # Ana moved out: her payments are still hers.
    assert await listed(db_session, i["ana"]) == [i["p6"]]


async def test_another_residents_payment_is_not_found(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    assert (await get_resident_payment(db_session, i["laura"], i["p1"])).id == i["p1"]
    for resident, payment_id in ((i["pedro"], i["p1"]), (i["bruno"], i["p6"]), (i["laura"], i["p7"])):
        with pytest.raises(PaymentNotFoundError):
            await get_resident_payment(db_session, resident, payment_id)


async def test_missing_and_foreign_payments_look_the_same(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    missing = await db_session.scalar(select(func.max(Payment.id))) + 1000
    errors = []
    for payment_id in (i["p5"], missing, 0, -1):  # another resident's, missing, invalid ids
        with pytest.raises(PaymentNotFoundError) as error:
            await get_resident_payment(db_session, i["laura"], payment_id)
        errors.append((error.value.status_code, error.value.code, error.value.message, error.value.details))
    assert len(set(map(str, errors))) == 1
    assert errors[0] == (404, "PAYMENT_NOT_FOUND", "El pago no existe.", None)


async def test_a_payment_id_alone_never_authorizes(db_session: AsyncSession, world: World) -> None:
    for resident_id in (None, 0, True):
        with pytest.raises(ValueError, match="resident_id"):
            await get_resident_payment(db_session, resident_id, world.ids["p1"])  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="resident_id"):
            await list_resident_payments(db_session, resident_id, ResidentPaymentQuery())  # type: ignore[arg-type]


# --- 5-16. List: order, pagination, filters and search ------------------------------------------------------------------


async def test_newest_payment_date_first_and_ties_by_id(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    page = await list_resident_payments(db_session, i["laura"], ResidentPaymentQuery())
    assert [item.payment_date for item in page.items] == [ago(10), ago(10), ago(20), ago(30)]
    assert i["p4"] > i["p3"] and [item.id for item in page.items][:2] == [i["p4"], i["p3"]]


async def test_pagination_and_total(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    first = await list_resident_payments(db_session, i["laura"], ResidentPaymentQuery(page=1, page_size=3))
    second = await list_resident_payments(db_session, i["laura"], ResidentPaymentQuery(page=2, page_size=3))
    beyond = await list_resident_payments(db_session, i["laura"], ResidentPaymentQuery(page=3, page_size=3))
    assert [p.id for p in first.items] == [i["p4"], i["p3"], i["p2"]]
    assert [p.id for p in second.items] == [i["p1"]]
    assert beyond.items == ()
    for page in (first, second, beyond):
        assert (page.total, page.total_pages, page.page_size) == (4, 2, 3)
    assert (first.page, second.page, beyond.page) == (1, 2, 3)


async def test_no_payments(db_session: AsyncSession, world: World, make_user: Any) -> None:
    nobody = await Community(db_session, world.actor.user_id).resident("Sin Pagos", "1012345699", (2, 1, 201),
                                                                        email="sin.pagos@example.com")
    page = await list_resident_payments(db_session, nobody, ResidentPaymentQuery())
    assert (page.items, page.total, page.total_pages) == ((), 0, 0)


async def test_filter_by_status(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    assert await listed(db_session, i["laura"], status=PaymentStatus.APPLIED) == [i["p1"]]
    assert await listed(db_session, i["laura"], status=PaymentStatus.PARTIALLY_APPLIED) == [i["p2"]]
    assert await listed(db_session, i["laura"], status=PaymentStatus.CREDIT) == [i["p4"], i["p3"]]
    # The SQL filter and the status of every row are the same rule (payment_status).
    for status in PaymentStatus:
        page = await list_resident_payments(db_session, i["laura"], ResidentPaymentQuery(status=status))
        assert all(item.status is status for item in page.items)


async def test_filter_by_payment_date(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    assert await listed(db_session, i["laura"], date_from=ago(20)) == [i["p4"], i["p3"], i["p2"]]
    assert await listed(db_session, i["laura"], date_to=ago(20)) == [i["p2"], i["p1"]]
    assert await listed(db_session, i["laura"], date_from=ago(20), date_to=ago(20)) == [i["p2"]]
    assert await listed(db_session, i["laura"], date_from=TODAY) == []


async def test_filters_combined(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    assert await listed(db_session, i["laura"], status=PaymentStatus.CREDIT, date_from=ago(15)) == [i["p4"], i["p3"]]
    assert await listed(db_session, i["laura"], status=PaymentStatus.APPLIED, date_from=ago(15)) == []
    assert await listed(db_session, i["laura"], status=PaymentStatus.CREDIT, search=ago(10).isoformat()) == [i["p4"], i["p3"]]
    assert await listed(db_session, i["laura"], status=PaymentStatus.CREDIT, search=receipt_number(i["p3"])) == [i["p3"]]
    assert await listed(db_session, i["laura"], status=PaymentStatus.APPLIED, search=receipt_number(i["p3"])) == []


async def test_search_by_receipt_number(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    assert await listed(db_session, i["laura"], search=receipt_number(i["p2"])) == [i["p2"]]  # RC-0000000N
    assert await listed(db_session, i["laura"], search=str(i["p2"])) == [i["p2"]]  # N
    assert await listed(db_session, i["laura"], search=f"rc-{i['p2']}") == [i["p2"]]


async def test_search_by_a_specific_date(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    assert await listed(db_session, i["laura"], search=ago(10).isoformat()) == [i["p4"], i["p3"]]
    assert await listed(db_session, i["laura"], search=ago(30).isoformat()) == [i["p1"]]


async def test_search_without_matches(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    page = await list_resident_payments(db_session, i["laura"], ResidentPaymentQuery(search=ago(11).isoformat()))
    assert (page.items, page.total, page.total_pages) == ((), 0, 0)
    # Another resident's receipt number finds nothing (it is not revealed either).
    assert await listed(db_session, i["laura"], search=receipt_number(i["p5"])) == []
    missing = await db_session.scalar(select(func.max(Payment.id))) + 1000
    assert await listed(db_session, i["laura"], search=str(missing)) == []


async def test_list_items_carry_the_table_columns(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    page = await list_resident_payments(db_session, i["laura"], ResidentPaymentQuery(search=receipt_number(i["p3"])))
    [item] = page.items
    assert item.receipt_number == receipt_number(i["p3"])
    assert (item.payment_date, item.concept, item.concept_description, item.amount, item.method, item.status) == (
        ago(10), FinancialConcept.MONTHLY_FEE, None, Decimal("40000.00"), PaymentMethod.CASH, PaymentStatus.CREDIT
    )
    assert item.transaction_reference == "TRF-2026-0042"
    assert not hasattr(item, "notes")  # the notes travel in the detail only


# --- 17-22. Detail ------------------------------------------------------------------------------------------------------


async def test_detail_with_reference_notes_and_credit(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    detail = await get_resident_payment(db_session, i["laura"], i["p3"])
    assert (detail.id, detail.receipt_number, detail.payment_date) == (i["p3"], receipt_number(i["p3"]), ago(10))
    assert isinstance(detail.registered_at, datetime) and detail.registered_at.tzinfo is None  # UTC, as stored
    assert (detail.concept, detail.concept_description, detail.amount, detail.method) == (
        FinancialConcept.MONTHLY_FEE, None, Decimal("40000.00"), PaymentMethod.CASH
    )
    assert detail.transaction_reference == "TRF-2026-0042"
    assert detail.notes == "Pagó en la portería.\nTrajo el soporte."
    assert detail.unit == PaymentUnit(id=i["laura_unit"], label="B1-TO1-APTO-101")
    assert (detail.applied_amount, detail.credit_amount, detail.status) == (Decimal("0.00"), Decimal("40000.00"), PaymentStatus.CREDIT)
    assert detail.allocations == ()


async def test_detail_with_applied_amount_credit_and_covered_obligations(db_session: AsyncSession, world: World) -> None:
    i = world.ids
    partial = await get_resident_payment(db_session, i["laura"], i["p2"])
    assert (partial.applied_amount, partial.credit_amount, partial.status) == (
        Decimal("50000.00"), Decimal("30000.00"), PaymentStatus.PARTIALLY_APPLIED
    )
    assert (partial.transaction_reference, partial.notes) == (None, None)
    [penalty] = partial.allocations
    assert penalty == PaymentAllocationSummary(
        charge_id=i["penalty"], concept=FinancialConcept.PENALTY, description="Ruido", billing_period=None,
        due_date=date(2026, 2, 10), charge_amount=Decimal("50000.00"), allocated_amount=Decimal("50000.00"),
        allocated_at=penalty.allocated_at,
    )
    applied = await get_resident_payment(db_session, i["laura"], i["p1"])
    assert (applied.applied_amount, applied.credit_amount, applied.status) == (
        Decimal("100000.00"), Decimal("0.00"), PaymentStatus.APPLIED
    )
    [fee] = applied.allocations
    assert (fee.charge_id, fee.concept, fee.description, fee.billing_period, fee.due_date) == (
        i["fee"], FinancialConcept.MONTHLY_FEE, None, date(2026, 1, 1), date(2026, 1, 5)
    )
    assert (fee.charge_amount, fee.allocated_amount) == (Decimal("100000.00"), Decimal("100000.00"))


async def test_detail_shows_the_fifo_allocations_as_registered(db_session: AsyncSession, world: World) -> None:
    # Pedro: three pending obligations; one payment covers two and part of the third (FIFO of HU-004).
    i = world.ids
    unit = await unit_of(db_session, i["pedro"])
    older = await charge(db_session, unit, "30000", date(2026, 1, 5))
    late = await charge(db_session, unit, "5000", date(2026, 1, 20), "LATE_FEE")
    newer = await charge(db_session, unit, "30000", date(2026, 2, 5))
    data = PaymentCreate(resident_id=i["pedro"], amount="50000", method="CARD", concept="MONTHLY_FEE",
                         payment_date=TODAY.isoformat())
    registered = await payment_service.register_payment(db_session, data, world.actor, mailer=world.mailer)

    detail = await get_resident_payment(db_session, i["pedro"], registered.id)
    assert [(a.charge_id, a.allocated_amount) for a in detail.allocations] == [
        (a.charge_id, a.amount) for a in registered.allocations
    ] == [(older, Decimal("30000.00")), (late, Decimal("5000.00")), (newer, Decimal("15000.00"))]
    assert [a.concept for a in detail.allocations] == [FinancialConcept.MONTHLY_FEE, FinancialConcept.LATE_FEE,
                                                       FinancialConcept.MONTHLY_FEE]
    assert [a.billing_period for a in detail.allocations] == [date(2026, 1, 1), None, date(2026, 2, 1)]
    assert (detail.applied_amount, detail.credit_amount, detail.status) == (
        registered.applied_amount, registered.credit_amount, registered.status
    )


# --- Financial regression: the same status, applied amount and credit as HU-004 ----------------------------------------------


async def statuses_of_the_administrator(db: AsyncSession) -> dict[int, tuple[PaymentStatus, Decimal, Decimal]]:
    page = await payment_service.list_payments(db, PaymentListQuery(page_size=100))
    return {p.id: (p.status, p.applied_amount, p.credit_amount) for p in page.items}


async def statuses_of_the_residents(db: AsyncSession, world: World) -> dict[int, tuple[PaymentStatus, Decimal, Decimal]]:
    seen = {}
    for resident in ("laura", "pedro", "ana", "bruno"):
        for item in (await list_resident_payments(db, world.ids[resident], ResidentPaymentQuery(page_size=100))).items:
            detail = await get_resident_payment(db, world.ids[resident], item.id)
            assert item.status is detail.status
            seen[item.id] = (detail.status, detail.applied_amount, detail.credit_amount)
    return seen


async def test_same_status_applied_and_credit_as_the_administrator(db_session: AsyncSession, world: World) -> None:
    assert await statuses_of_the_residents(db_session, world) == await statuses_of_the_administrator(db_session)


async def test_status_follows_the_credit_consumed_by_a_new_obligation(db_session: AsyncSession, world: World) -> None:
    # A new obligation consumes Laura's credit (obligations extension): both views change the same way.
    i = world.ids
    await charge_service.create_charge(
        db_session,
        charge_service.ChargeInput(unit_id=i["laura_unit"], concept=FinancialConcept.OTHER, amount=Decimal("35000"),
                                   due_date=TODAY + timedelta(days=10), description="Llaves"),
        world.actor,
    )
    assert await statuses_of_the_residents(db_session, world) == await statuses_of_the_administrator(db_session)
    credit_left = [await get_resident_payment(db_session, i["laura"], p) for p in (i["p2"], i["p3"], i["p4"])]
    # 80.000 of credit before (30 + 40 + 10), 35.000 consumed: 45.000 left, never more or less.
    assert sum(p.credit_amount for p in credit_left) == Decimal("45000.00")
    assert sum(sum(a.allocated_amount for a in p.allocations) for p in credit_left) == Decimal("85000.00")


async def test_reading_changes_nothing(db_session: AsyncSession, world: World) -> None:
    i = world.ids

    async def snapshot() -> tuple[Any, ...]:
        payments = await db_session.scalar(select(func.count(Payment.id)))
        allocations = (await db_session.execute(
            select(PaymentAllocation.payment_id, PaymentAllocation.charge_id, PaymentAllocation.amount)
            .order_by(PaymentAllocation.id))).all()
        balance = await payment_service.unit_balance(db_session, i["laura_unit"])
        return payments, allocations, balance

    before = await snapshot()
    await list_resident_payments(db_session, i["laura"], ResidentPaymentQuery(status=PaymentStatus.CREDIT))
    await get_resident_payment(db_session, i["laura"], i["p2"])
    assert await snapshot() == before


# --- Queries: no N+1 --------------------------------------------------------------------------------------------------------


@pytest.fixture
def statements() -> Iterator[list[str]]:
    """Every SQL statement sent to MySQL while the test runs."""
    seen: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        # The test session's own SAVEPOINT statements are not queries of the service.
        if not statement.lstrip().upper().startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


async def test_list_is_one_count_and_one_page(db_session: AsyncSession, world: World, statements: list[str]) -> None:
    i = world.ids
    for query in (ResidentPaymentQuery(), ResidentPaymentQuery(page_size=1), ResidentPaymentQuery(status=PaymentStatus.CREDIT)):
        statements.clear()
        await list_resident_payments(db_session, i["laura"], query)
        assert len(statements) == 2, statements
        assert all("resident_id" in s for s in statements)  # ownership in every query
    statements.clear()
    await list_resident_payments(db_session, i["laura"], ResidentPaymentQuery(page=9))
    assert len(statements) == 1  # beyond the last page: only the count


async def test_detail_is_two_queries_whatever_the_allocations(db_session: AsyncSession, world: World, statements: list[str]) -> None:
    i = world.ids
    for payment_id in (i["p3"], i["p1"]):  # no allocation, one allocation
        statements.clear()
        await get_resident_payment(db_session, i["laura"], payment_id)
        assert len(statements) == 2, statements
    statements.clear()
    with pytest.raises(PaymentNotFoundError):
        await get_resident_payment(db_session, i["pedro"], i["p1"])
    assert len(statements) == 1  # not found: the allocations are never read
