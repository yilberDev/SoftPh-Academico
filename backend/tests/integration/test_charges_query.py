"""Obligations extension: list_charges and get_charge on the MySQL _test database.

Applied amount, outstanding balance and status are computed by MySQL; the tests
compare them with the approved rules (charge_status) for a fixed "today".
"""

import re
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ChargeNotFoundError
from app.models import (
    AuditLog,
    Charge,
    Payment,
    PaymentAllocation,
    Unit,
    User,
    UserRole,
)
from app.schemas.payment import SortOrder
from app.services import charges as charges_service
from app.services.allocation import ChargeStatus, charge_status
from app.services.charges import (
    ChargeInput,
    ChargeListQuery,
    ChargeSortField,
    ChargeSummary,
    create_charge,
    get_charge,
    list_charges,
)
from app.services.structure import Actor
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

TODAY = date(2026, 10, 15)
ALL = 100


@pytest.fixture
async def admin(make_user: Any) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
async def world(db_session: AsyncSession, admin: User, make_user: Any) -> dict[str, Any]:
    """Five apartments (one deleted) in two blocks, charges in every status, payments and allocations."""
    community = Community(db_session, admin.id)
    u = {
        "b1t1_101": await community.unit(1, 1, 101), "b1t1_102": await community.unit(1, 1, 102),
        "b1t2_101": await community.unit(1, 2, 101), "b2t1_101": await community.unit(2, 1, 101),
        "b2t1_102": await community.unit(2, 1, 102),
    }
    resident = await make_user("residente@example.com", role=UserRole.RESIDENTE, password_hash="x")

    async def charge(unit: str, concept: str, amount: str, due: date, *, period: date | None = None,
                     description: str | None = None, source: int | None = None, created: datetime) -> int:
        row = Charge(unit_id=u[unit], concept=concept, description=description, amount=Decimal(amount), due_date=due,
                     billing_period=period, source_charge_id=source, created_by_user_id=admin.id, created_at=created)
        db_session.add(row)
        await db_session.flush()
        return row.id

    async def payment(unit: str, amount: str, paid: date, method: str = "CASH") -> int:
        row = Payment(resident_id=resident.id, unit_id=u[unit], registered_by_user_id=admin.id, amount=Decimal(amount),
                      method=method, concept="MONTHLY_FEE", payment_date=paid, registered_at=datetime(2026, 10, 1, 15),
                      resident_name_snapshot="Laura Gómez", unit_label_snapshot="x")
        db_session.add(row)
        await db_session.flush()
        return row.id

    async def allocate(payment_id: int, charge_id: int, amount: str, at: datetime) -> None:
        db_session.add(PaymentAllocation(payment_id=payment_id, charge_id=charge_id, amount=Decimal(amount), created_at=at))
        await db_session.flush()

    c: dict[str, int] = {}
    # B1-TO1-APTO-101: overdue without payments, partial (two allocations), and its late fee (due today).
    c["overdue"] = await charge("b1t1_101", "MONTHLY_FEE", "100000", date(2026, 9, 5), period=date(2026, 9, 1),
                                created=datetime(2026, 9, 1, 10))
    c["partial"] = await charge("b1t1_101", "MONTHLY_FEE", "100000", date(2026, 10, 20), period=date(2026, 10, 1),
                                created=datetime(2026, 10, 1, 10))
    c["late_fee"] = await charge("b1t1_101", "LATE_FEE", "20000", TODAY, source=c["overdue"],
                                 description="Mora de septiembre", created=datetime(2026, 10, 2, 9))
    # B1-TO1-APTO-102: paid before its due date.
    c["paid"] = await charge("b1t1_102", "MONTHLY_FEE", "100000", date(2026, 10, 20), period=date(2026, 10, 1),
                             created=datetime(2026, 10, 1, 10))
    # B1-TO2-APTO-101: pending penalty (future).
    c["pending"] = await charge("b1t2_101", "PENALTY", "50000", date(2026, 11, 1), description="Ruido",
                                created=datetime(2026, 10, 3, 8))
    # B2-TO1-APTO-101: overdue with a partial payment, and overdue but fully paid.
    c["overdue_partial"] = await charge("b2t1_101", "OTHER", "30000", date(2026, 8, 1), description="Llave",
                                        created=datetime(2026, 8, 1, 8))
    c["overdue_paid"] = await charge("b2t1_101", "MONTHLY_FEE", "100000", date(2026, 9, 5), period=date(2026, 9, 1),
                                     created=datetime(2026, 9, 1, 10))
    # B2-TO1-APTO-102: a charge of an apartment deleted later.
    c["deleted_unit"] = await charge("b2t1_102", "MONTHLY_FEE", "100000", date(2026, 10, 20), period=date(2026, 10, 1),
                                     created=datetime(2026, 10, 1, 10))
    await db_session.execute(update(Unit).where(Unit.id == u["b2t1_102"]).values(deleted_at=datetime(2026, 10, 10)))

    p = {
        "cash": await payment("b1t1_101", "25000", date(2026, 10, 5)),
        "transfer": await payment("b1t1_101", "15000", date(2026, 10, 9), "TRANSFER"),
        "full": await payment("b1t1_102", "100000", date(2026, 10, 2)),
        "part": await payment("b2t1_101", "10000", date(2026, 8, 20), "CARD"),
        "late": await payment("b2t1_101", "100000", date(2026, 10, 1)),
    }
    await allocate(p["cash"], c["partial"], "25000", datetime(2026, 10, 5, 16))
    await allocate(p["transfer"], c["partial"], "15000", datetime(2026, 10, 9, 16))
    await allocate(p["full"], c["paid"], "100000", datetime(2026, 10, 2, 16))
    await allocate(p["part"], c["overdue_partial"], "10000", datetime(2026, 8, 20, 16))
    await allocate(p["late"], c["overdue_paid"], "100000", datetime(2026, 10, 1, 16))
    return {"units": u, "charges": c, "payments": p, "admin": admin}


async def page(db: AsyncSession, **query: Any) -> list[ChargeSummary]:
    result = await list_charges(db, ChargeListQuery(page_size=ALL, **query), TODAY)
    return list(result.items)


def ids(items: list[ChargeSummary], world: dict[str, Any]) -> list[str]:
    names = {v: k for k, v in world["charges"].items()}
    return [names.get(item.id, str(item.id)) for item in items]


@pytest.fixture
def statements() -> Iterator[list[str]]:
    """Every SQL statement sent to MySQL while the test runs."""
    seen: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


# --- 1-5. Status, applied and outstanding --------------------------------------------------------------------


async def test_status_applied_and_outstanding(db_session: AsyncSession, world: dict[str, Any]) -> None:
    items = {name: item for name, item in zip(ids(await page(db_session), world), await page(db_session), strict=True)}
    expected = {
        "overdue": (ChargeStatus.OVERDUE, "0.00", "100000.00"),  # no payments, due 2026-09-05
        "partial": (ChargeStatus.PARTIAL, "40000.00", "60000.00"),  # two allocations, not due yet
        "late_fee": (ChargeStatus.PENDING, "0.00", "20000.00"),  # due today: not overdue yet
        "paid": (ChargeStatus.PAID, "100000.00", "0.00"),
        "pending": (ChargeStatus.PENDING, "0.00", "50000.00"),
        "overdue_partial": (ChargeStatus.OVERDUE, "10000.00", "20000.00"),  # partial and overdue
        "overdue_paid": (ChargeStatus.PAID, "100000.00", "0.00"),  # paid wins over overdue
        "deleted_unit": (ChargeStatus.PENDING, "0.00", "100000.00"),
    }
    assert {k: (v.status, str(v.applied_amount), str(v.outstanding_amount)) for k, v in items.items()} == expected
    for item in items.values():
        # The SQL status is exactly the approved rule (charge_status).
        assert item.status is charge_status(item.amount, item.applied_amount, item.due_date, TODAY)
        assert item.outstanding_amount == item.amount - item.applied_amount


async def test_status_moves_with_today(db_session: AsyncSession, world: dict[str, Any]) -> None:
    query = ChargeListQuery(page_size=ALL, unit_id=world["units"]["b1t1_101"])
    later = {i.id: i.status for i in (await list_charges(db_session, query, date(2026, 10, 21))).items}
    assert later[world["charges"]["partial"]] == ChargeStatus.OVERDUE  # due 2026-10-20 has ended
    assert later[world["charges"]["late_fee"]] == ChargeStatus.OVERDUE  # due 2026-10-15 has ended


async def test_credit_applied_on_creation_is_reflected(db_session: AsyncSession, world: dict[str, Any]) -> None:
    unit = world["units"]["b1t2_101"]
    resident = await db_session.scalar(select(User.id).where(User.role == UserRole.RESIDENTE))
    db_session.add(Payment(resident_id=resident, unit_id=unit, registered_by_user_id=world["admin"].id,
                           amount=Decimal(80000), method="CASH", concept="OTHER", concept_description="Anticipo",
                           payment_date=date(2026, 10, 1), registered_at=datetime(2026, 10, 1, 15),
                           resident_name_snapshot="Laura Gómez", unit_label_snapshot="x"))
    await db_session.flush()
    # The pending penalty (50.000) is the oldest: FIFO covers it first, then 30.000 of the new fee.
    created = await create_charge(db_session, ChargeInput(unit, "MONTHLY_FEE", Decimal(100000), date(2026, 12, 5),  # type: ignore[arg-type]
                                                          billing_period=date(2026, 12, 1)), Actor(world["admin"].id, None))
    rows = {i.id: i for i in await page(db_session, unit_id=unit)}
    assert (rows[world["charges"]["pending"]].status, rows[world["charges"]["pending"]].applied_amount) == (
        ChargeStatus.PAID, Decimal("50000.00"))
    assert (rows[created.id].status, rows[created.id].applied_amount, rows[created.id].outstanding_amount) == (
        ChargeStatus.PARTIAL, Decimal("30000.00"), Decimal("70000.00"))


async def test_apartment_of_each_charge(db_session: AsyncSession, world: dict[str, Any]) -> None:
    items = {name: item.unit for name, item in zip(ids(await page(db_session), world), await page(db_session), strict=True)}
    assert (items["overdue"].id, items["overdue"].label, items["overdue"].deleted) == (
        world["units"]["b1t1_101"], "B1-TO1-APTO-101", False)
    assert items["pending"].label == "B1-TO2-APTO-101"
    assert (items["deleted_unit"].label, items["deleted_unit"].deleted) == ("B2-TO1-APTO-102", True)


# --- Filters -------------------------------------------------------------------------------------------------


async def test_filters(db_session: AsyncSession, world: dict[str, Any]) -> None:
    u = world["units"]
    tower_b2t1 = await db_session.scalar(select(Unit.tower_id).where(Unit.id == u["b2t1_101"]))
    tower_b1t2 = await db_session.scalar(select(Unit.tower_id).where(Unit.id == u["b1t2_101"]))
    block_2 = await db_session.scalar(block_of(u["b2t1_101"]))
    assert ids(await page(db_session, unit_id=u["b1t1_101"]), world) == ["overdue", "late_fee", "partial"]
    assert ids(await page(db_session, tower_id=tower_b1t2), world) == ["pending"]
    assert set(ids(await page(db_session, tower_id=tower_b2t1), world)) == {"overdue_partial", "overdue_paid", "deleted_unit"}
    assert set(ids(await page(db_session, block_id=block_2), world)) == {"overdue_partial", "overdue_paid", "deleted_unit"}
    assert ids(await page(db_session, concept="LATE_FEE"), world) == ["late_fee"]
    assert set(ids(await page(db_session, concept="MONTHLY_FEE"), world)) == {"overdue", "partial", "paid", "overdue_paid",
                                                                               "deleted_unit"}
    assert set(ids(await page(db_session, status=ChargeStatus.OVERDUE), world)) == {"overdue", "overdue_partial"}
    assert set(ids(await page(db_session, status=ChargeStatus.PAID), world)) == {"paid", "overdue_paid"}
    assert ids(await page(db_session, status=ChargeStatus.PARTIAL), world) == ["partial"]
    assert set(ids(await page(db_session, status=ChargeStatus.PENDING), world)) == {"late_fee", "pending", "deleted_unit"}
    assert set(ids(await page(db_session, billing_period=date(2026, 9, 1)), world)) == {"overdue", "overdue_paid"}
    # Inclusive limits.
    assert set(ids(await page(db_session, due_from=date(2026, 10, 15)), world)) == {"late_fee", "partial", "paid", "pending",
                                                                                     "deleted_unit"}
    assert set(ids(await page(db_session, due_to=date(2026, 9, 5)), world)) == {"overdue", "overdue_partial", "overdue_paid"}
    assert ids(await page(db_session, due_from=TODAY, due_to=TODAY), world) == ["late_fee"]


def block_of(unit_id: int) -> Any:
    from app.models import Tower

    return select(Tower.block_id).join(Unit, Unit.tower_id == Tower.id).where(Unit.id == unit_id)


async def test_combined_filters(db_session: AsyncSession, world: dict[str, Any]) -> None:
    u = world["units"]
    block1 = await db_session.scalar(block_of(u["b1t1_101"]))
    assert set(ids(await page(db_session, block_id=block1, concept="MONTHLY_FEE", status=ChargeStatus.PARTIAL), world)) == {"partial"}
    assert ids(await page(db_session, unit_id=u["b2t1_101"], status=ChargeStatus.OVERDUE, due_to=date(2026, 8, 31)), world) == [
        "overdue_partial"]
    assert await page(db_session, unit_id=u["b1t1_102"], status=ChargeStatus.OVERDUE) == []
    result = await list_charges(db_session, ChargeListQuery(block_id=block1, status=ChargeStatus.PENDING), TODAY)
    assert (result.total, result.total_pages, len(result.items)) == (2, 1, 2)  # COUNT honors the status filter too


# --- Order ---------------------------------------------------------------------------------------------------

KEYS = {
    ChargeSortField.DUE_DATE: lambda i: (i.due_date,),
    ChargeSortField.UNIT: lambda i: tuple(int(n) for n in re.findall(r"\d+", i.unit.label)),  # block, tower, apartment
    ChargeSortField.CONCEPT: lambda i: ({"MONTHLY_FEE": 1, "LATE_FEE": 2, "OTHER": 3, "PENALTY": 4}[i.concept],),
    ChargeSortField.AMOUNT: lambda i: (i.amount,),
    ChargeSortField.OUTSTANDING: lambda i: (i.outstanding_amount,),
    ChargeSortField.STATUS: lambda i: ({"PAID": 1, "PARTIAL": 2, "PENDING": 3, "OVERDUE": 4}[i.status],),
    ChargeSortField.CREATED: lambda i: (i.created_at,),
}


@pytest.mark.parametrize("order", [SortOrder.ASC, SortOrder.DESC])
@pytest.mark.parametrize("field", list(ChargeSortField))
async def test_every_order(db_session: AsyncSession, world: dict[str, Any], field: ChargeSortField, order: SortOrder) -> None:
    items = await page(db_session, sort_by=field, sort_order=order)
    # The expected order: the key, then charges.id, both in the chosen direction.
    expected = sorted(items, key=lambda i: (*KEYS[field](i), i.id), reverse=order == SortOrder.DESC)
    assert [i.id for i in items] == [i.id for i in expected]
    assert len(items) == len(world["charges"])


async def test_ties_are_broken_by_id_in_the_same_direction(db_session: AsyncSession, world: dict[str, Any]) -> None:
    hundred = [world["charges"][k] for k in ("overdue", "partial", "paid", "overdue_paid", "deleted_unit")]
    ascending = [i.id for i in await page(db_session, sort_by=ChargeSortField.AMOUNT, sort_order=SortOrder.ASC)
                 if i.amount == Decimal("100000.00")]
    descending = [i.id for i in await page(db_session, sort_by=ChargeSortField.AMOUNT, sort_order=SortOrder.DESC)
                  if i.amount == Decimal("100000.00")]
    assert ascending == sorted(hundred) and descending == sorted(hundred, reverse=True)


async def test_default_order_is_due_date_ascending(db_session: AsyncSession, world: dict[str, Any]) -> None:
    items = (await list_charges(db_session, ChargeListQuery(page_size=ALL), TODAY)).items
    assert [i.due_date for i in items] == sorted(i.due_date for i in items)


# --- Pagination ----------------------------------------------------------------------------------------------


async def test_pagination(db_session: AsyncSession, world: dict[str, Any]) -> None:
    def query(n: int) -> ChargeListQuery:
        return ChargeListQuery(page=n, page_size=3, sort_by=ChargeSortField.CREATED)

    first, second, third = [await list_charges(db_session, query(n), TODAY) for n in (1, 2, 3)]
    assert (first.total, first.total_pages, first.page, first.page_size) == (8, 3, 1, 3)
    assert (len(first.items), len(second.items), len(third.items)) == (3, 3, 2)
    every = [i.id for i in (*first.items, *second.items, *third.items)]
    assert every == [i.id for i in await page(db_session, sort_by=ChargeSortField.CREATED)]  # no gaps, no repeats
    beyond = await list_charges(db_session, query(4), TODAY)
    assert (beyond.items, beyond.total, beyond.total_pages, beyond.page) == ((), 8, 3, 4)


async def test_empty_list(db_session: AsyncSession, admin: User) -> None:
    result = await list_charges(db_session, ChargeListQuery(), TODAY)
    assert (result.items, result.total, result.total_pages) == ((), 0, 0)


async def test_one_count_and_one_page_query(db_session: AsyncSession, world: dict[str, Any], statements: list[str]) -> None:
    statements.clear()  # only what list_charges sends
    await list_charges(db_session, ChargeListQuery(page_size=ALL, status=ChargeStatus.OVERDUE), TODAY)
    # Exactly two statements whatever the number of rows (no N+1), read only, never locking.
    assert len(statements) == 2
    assert "count(" in statements[0].lower() and "LIMIT" in statements[1] and "ORDER BY" in statements[1]
    assert not any("FOR UPDATE" in s or "FOR SHARE" in s for s in statements)


# --- Detail --------------------------------------------------------------------------------------------------


async def test_detail_with_allocations_and_late_fee(db_session: AsyncSession, world: dict[str, Any]) -> None:
    c, p = world["charges"], world["payments"]
    detail = await get_charge(db_session, c["partial"], TODAY)
    assert (detail.charge.id, detail.charge.status, detail.charge.applied_amount, detail.charge.outstanding_amount) == (
        c["partial"], ChargeStatus.PARTIAL, Decimal("40000.00"), Decimal("60000.00"))
    assert (detail.charge.billing_period, detail.charge.unit.label, detail.created_by_user_id) == (
        date(2026, 10, 1), "B1-TO1-APTO-101", world["admin"].id)
    assert [(a.payment_id, a.receipt_number, a.payment_date, a.payment_method, a.applied_amount, a.allocation_date)
            for a in detail.allocations] == [
        (p["cash"], f"RC-{p['cash']:08d}", date(2026, 10, 5), "CASH", Decimal("25000.00"), datetime(2026, 10, 5, 16)),
        (p["transfer"], f"RC-{p['transfer']:08d}", date(2026, 10, 9), "TRANSFER", Decimal("15000.00"), datetime(2026, 10, 9, 16)),
    ]
    assert (detail.source, detail.late_fees) == (None, ())


async def test_detail_of_an_origin_lists_its_late_fees(db_session: AsyncSession, world: dict[str, Any]) -> None:
    c = world["charges"]
    origin = await get_charge(db_session, c["overdue"], TODAY)
    assert [(f.id, f.concept, f.description, f.status) for f in origin.late_fees] == [
        (c["late_fee"], "LATE_FEE", "Mora de septiembre", ChargeStatus.PENDING)]
    assert origin.allocations == () and origin.charge.status == ChargeStatus.OVERDUE

    late = await get_charge(db_session, c["late_fee"], TODAY)
    assert late.source is not None
    assert (late.source.id, late.source.status, late.source.outstanding_amount) == (c["overdue"], ChargeStatus.OVERDUE,
                                                                                    Decimal("100000.00"))
    assert late.charge.source_charge_id == c["overdue"] and late.late_fees == ()


async def test_detail_queries_and_no_writes(db_session: AsyncSession, world: dict[str, Any], statements: list[str]) -> None:
    before = (await db_session.scalar(select(func.count()).select_from(Charge)),
              await db_session.scalar(select(func.count()).select_from(AuditLog)))
    statements.clear()
    await get_charge(db_session, world["charges"]["late_fee"], TODAY)  # with an origin: the most queries
    assert len(statements) == 4  # charge, origin, late fees, allocations: never one per allocation
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert not any("FOR UPDATE" in s or "FOR SHARE" in s for s in statements)
    statements.clear()
    after = (await db_session.scalar(select(func.count()).select_from(Charge)),
             await db_session.scalar(select(func.count()).select_from(AuditLog)))
    assert after == before


@pytest.mark.parametrize("charge_id", [999999, 0, -1, True, "1"])
async def test_missing_charge(db_session: AsyncSession, world: dict[str, Any], charge_id: Any) -> None:
    with pytest.raises(ChargeNotFoundError) as error:
        await get_charge(db_session, charge_id, TODAY)
    assert (error.value.status_code, error.value.message) == (404, "La obligación no existe.")


async def test_detail_of_a_deleted_apartment(db_session: AsyncSession, world: dict[str, Any]) -> None:
    detail = await get_charge(db_session, world["charges"]["deleted_unit"], TODAY)
    assert (detail.charge.unit.label, detail.charge.unit.deleted) == ("B2-TO1-APTO-102", True)


def test_queries_never_lock() -> None:
    # Read only by construction: no locking clause anywhere in the list/detail code.
    import inspect

    source = inspect.getsource(charges_service.list_charges) + inspect.getsource(charges_service.get_charge)
    source += inspect.getsource(charges_service._charge_columns)
    assert "with_for_update" not in source and "commit" not in source
