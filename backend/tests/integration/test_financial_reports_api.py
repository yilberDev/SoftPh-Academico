"""HU-012: financial reports (administrator). GET /api/v1/admin/reports/payments and /pending-balances.

The pending balances report is also how the administrator identifies every resident with pending
obligations (HU-011 criterion 3.2). Totals are checked against payments.unit_balance (HU-004) and
against plain SQL sums.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.models import Payment, User, UserRole
from app.services.payments import unit_balance, unit_balances
from tests.integration.conftest import sign_in
from tests.integration.test_account_statement_api import FUTURE, PAST, TODAY, Books
from tests.integration.test_residents_api import Community, MakeUser

pytestmark = pytest.mark.anyio

API = "/api/v1/admin/reports"


@pytest.fixture
async def admin(make_user: MakeUser) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
async def community(db_session: AsyncSession, admin: User) -> Community:
    return Community(db_session, admin.id)


@pytest.fixture
async def books(db_session: AsyncSession, admin: User) -> Books:
    return Books(db_session, admin.id)


@pytest.fixture
async def signed_in(client: AsyncClient, db_session: AsyncSession, admin: User) -> None:
    await sign_in(client, db_session, admin)


async def get(client: AsyncClient, path: str, **params: Any) -> Response:
    return await client.get(f"{API}{path}", params=params)


def error(response: Response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


async def resident_at(community: Community, name: str, document: str, unit_id: int, *, ended: bool = False) -> int:
    resident = await community.resident(name, document, None, email=f"{document}@example.com")
    await community.residence(resident, unit_id, ended=ended)
    return resident


# --- Access (HU-018) --------------------------------------------------------------------------------------


async def test_reports_are_for_the_administrator_only(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    for path in ("/payments", "/pending-balances"):
        response = await get(client, path, date_from="2026-01-01", date_to="2026-01-31")
        assert error(response) == (401, "NOT_AUTHENTICATED")
        assert response.headers["cache-control"] == "no-store"
    resident = await make_user("residente@example.com", role=UserRole.RESIDENTE, password_hash="not-a-real-hash")
    await sign_in(client, db_session, resident)
    for path in ("/payments", "/pending-balances"):
        assert error(await get(client, path, date_from="2026-01-01", date_to="2026-01-31")) == (403, "FORBIDDEN")


@pytest.mark.parametrize("path", ["/payments", "/pending-balances"])
async def test_unknown_parameters_are_refused(client: AsyncClient, signed_in: None, path: str) -> None:
    for extra in ({"resident_id": "1"}, {"unit_id": "1"}, {"page": "1"}):
        response = await get(client, path, date_from="2026-01-01", date_to="2026-01-31", **extra)
        assert error(response) == (422, "VALIDATION_ERROR"), (path, extra)


# --- Payments report: criteria (CU-012 5a/6a) ------------------------------------------------------------------


@pytest.mark.parametrize(("params", "details"), [
    ({}, [{"field": "query.date_from", "message": "Indica la fecha inicial del reporte."},
          {"field": "query.date_to", "message": "Indica la fecha final del reporte."}]),
    ({"date_to": "2026-10-31"}, [{"field": "query.date_from", "message": "Indica la fecha inicial del reporte."}]),
    ({"date_from": "2026-10-01", "date_to": "  "},
     [{"field": "query.date_to", "message": "Indica la fecha final del reporte."}]),
    ({"date_from": "2026-10-31", "date_to": "2026-10-01"},
     [{"field": "query.date_to", "message": "La fecha inicial no puede ser posterior a la fecha final."}]),
    ({"date_from": "ayer", "date_to": "2026-10-01"}, [{"field": "query.date_from", "message": "El valor no es válido."}]),
])
async def test_the_criteria_are_required(
    client: AsyncClient, signed_in: None, params: dict[str, str], details: list[dict[str, str]]
) -> None:
    response = await get(client, "/payments", **params)

    assert error(response) == (422, "VALIDATION_ERROR")
    assert response.json()["error"]["details"] == details


# --- Payments report: contents (criterion 2) ---------------------------------------------------------------------


async def test_the_payments_of_the_range_each_one_identified(
    client: AsyncClient, db_session: AsyncSession, signed_in: None, community: Community, books: Books
) -> None:
    unit = await community.unit(1, 2, 104)
    laura = await resident_at(community, "Laura Gómez", "1012345678", unit)
    charge = await books.charge(unit, "100000.00", FUTURE)
    before = await books.payment(laura, unit, "1.00", paid_on=date(2026, 9, 30))  # outside the range
    first = await books.payment(laura, unit, "100000.50", paid_on=date(2026, 10, 1))  # first day: included
    await books.apply(first, charge, "100000.00")
    last = await books.payment(laura, unit, "0.25", paid_on=date(2026, 10, 31))  # last day: included
    after = await books.payment(laura, unit, "1.00", paid_on=date(2026, 11, 1))  # outside the range
    await db_session.commit()

    response = await get(client, "/payments", date_from="2026-10-01", date_to="2026-10-31")

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert [item["id"] for item in data["items"]] == [first, last]  # oldest first; never `before` or `after`
    assert before not in [item["id"] for item in data["items"]] and after not in [i["id"] for i in data["items"]]
    item = data["items"][0]
    assert {key: item[key] for key in ("receipt_number", "resident", "unit", "amount", "method", "concept",
                                       "payment_date", "applied_amount", "credit_amount", "status")} == {
        "receipt_number": f"RC-{first:08d}", "resident": {"id": laura, "full_name": "Laura Gómez"},
        "unit": {"id": unit, "label": "B1-TO2-APTO-104"}, "amount": "100000.50", "method": "CASH",
        "concept": "OTHER", "payment_date": "2026-10-01", "applied_amount": "100000.00", "credit_amount": "0.50",
        "status": "PARTIALLY_APPLIED",
    }
    # Exact decimal totals, the same as a plain SQL sum of the range.
    assert (data["has_data"], data["payments"], data["total_amount"], data["total_applied"], data["total_credit"]) == (
        True, 2, "100000.75", "100000.00", "0.75")
    in_sql = await db_session.scalar(text(
        "SELECT SUM(amount) FROM payments WHERE payment_date BETWEEN '2026-10-01' AND '2026-10-31'"))
    assert Decimal(data["total_amount"]) == in_sql
    assert data["by_method"] == [{"method": "CASH", "payments": 2, "amount": "100000.75"}]


async def test_the_totals_by_method(
    client: AsyncClient, db_session: AsyncSession, signed_in: None, community: Community, books: Books, admin: User
) -> None:
    unit = await community.unit(1, 2, 104)
    laura = await resident_at(community, "Laura Gómez", "1012345678", unit)
    await books.payment(laura, unit, "10.10")
    await books.payment(laura, unit, "20.20", method="TRANSFER")
    # A demo payment (HU-010): registered by the resident, with its own method and source.
    db_session.add(Payment(resident_id=laura, unit_id=unit, registered_by_user_id=laura, amount=Decimal("30.30"),
                           method="DEMO_GATEWAY", concept="OTHER", concept_description="Abono", source="DEMO_GATEWAY",
                           payment_date=date(2026, 10, 1), registered_at=utc_now(), resident_name_snapshot="Laura Gómez",
                           unit_label_snapshot="B1-TO2-APTO-104", receipt_email_status="PENDING"))
    await db_session.commit()

    data = (await get(client, "/payments", date_from="2026-10-01", date_to="2026-10-01")).json()

    assert data["by_method"] == [
        {"method": "CASH", "payments": 1, "amount": "10.10"},
        {"method": "DEMO_GATEWAY", "payments": 1, "amount": "30.30"},
        {"method": "TRANSFER", "payments": 1, "amount": "20.20"},
    ]
    assert data["total_amount"] == "60.60"
    assert admin.id not in [item["resident"]["id"] for item in data["items"]]


async def test_a_range_without_payments(client: AsyncClient, signed_in: None) -> None:
    data = (await get(client, "/payments", date_from="2026-10-01", date_to="2026-10-31")).json()

    # CU-012 5b: a valid report that says there is no data, not an error.
    assert (data["has_data"], data["items"], data["payments"], data["total_amount"], data["by_method"]) == (
        False, [], 0, "0.00", [])


# --- Pending balances report (criterion 3; HU-011 criterion 3.2) ---------------------------------------------------


async def test_the_apartments_that_owe_and_their_residents(
    client: AsyncClient, db_session: AsyncSession, signed_in: None, community: Community, books: Books
) -> None:
    # B1-TO1-APTO-101: Laura, en mora (a past obligation partly paid) and a later one; some credit too.
    first = await community.unit(1, 1, 101)
    laura = await resident_at(community, "Laura Gómez", "1012345678", first)
    old = await books.charge(first, "80000.40", PAST, "Vencida")
    await books.charge(first, "30000.00", FUTURE, "Pendiente")
    payment = await books.payment(laura, first, "50000.00")
    await books.apply(payment, old, "40000.00")  # 10 000 left as credit
    # B1-TO1-APTO-102: Pedro, pendiente (nothing past its due date).
    second = await community.unit(1, 1, 102)
    pedro = await resident_at(community, "Pedro Ruiz", "10000002", second)
    await books.charge(second, "25000.00", FUTURE)
    # B1-TO1-APTO-103: owes, but its resident left: no one to link to.
    third = await community.unit(1, 1, 103)
    await resident_at(community, "Ana Gil", "10000003", third, ended=True)
    await books.charge(third, "5000.00", PAST)
    # B1-TO1-APTO-104: paid up, never listed.
    fourth = await community.unit(1, 1, 104)
    ines = await resident_at(community, "Inés Paz", "10000004", fourth)
    paid = await books.charge(fourth, "1000.00", PAST)
    await books.apply(await books.payment(ines, fourth, "1000.00"), paid, "1000.00")
    await db_session.commit()

    response = await get(client, "/pending-balances")

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    rows = [(item["unit"]["label"], item["resident"], item["status"], item["pending_balance"], item["overdue_balance"],
             item["credit_balance"], item["pending_obligations"], item["overdue_obligations"]) for item in data["items"]]
    assert rows == [
        ("B1-TO1-APTO-101", {"id": laura, "full_name": "Laura Gómez"}, "OVERDUE", "70000.40", "40000.40", "10000.00",
         2, 1),
        ("B1-TO1-APTO-102", {"id": pedro, "full_name": "Pedro Ruiz"}, "PENDING", "25000.00", "0.00", "0.00", 1, 0),
        ("B1-TO1-APTO-103", None, "OVERDUE", "5000.00", "5000.00", "0.00", 1, 1),
    ]
    assert data["items"][0]["oldest_due_date"] == PAST.isoformat()
    assert (data["as_of"], data["scope"], data["has_data"], data["units"], data["overdue_units"],
            data["total_pending"], data["total_overdue"]) == (TODAY.isoformat(), "ALL", True, 3, 2, "100000.40",
                                                              "45000.40")
    # The same balances as HU-004 for every apartment, listed or not.
    for unit_id, item in zip((first, second, third), data["items"], strict=True):
        balance = await unit_balance(db_session, unit_id)
        assert (Decimal(item["pending_balance"]), Decimal(item["credit_balance"])) == (
            balance.pending_balance, balance.credit_balance)
    assert (await unit_balance(db_session, fourth)).pending_balance == 0
    # Each resident listed opens the HU-011 statement with the same pending balance.
    statement = (await client.get(f"/api/v1/admin/residents/{laura}/account-statement")).json()
    assert statement["summary"]["pending_balance"] == "70000.40" and statement["summary"]["status"] == "OVERDUE"


async def test_only_the_apartments_en_mora(
    client: AsyncClient, db_session: AsyncSession, signed_in: None, community: Community, books: Books
) -> None:
    late = await community.unit(1, 1, 101)
    await resident_at(community, "Laura Gómez", "1012345678", late)
    await books.charge(late, "100.00", PAST)
    on_time = await community.unit(1, 1, 102)
    await resident_at(community, "Pedro Ruiz", "10000002", on_time)
    await books.charge(on_time, "200.00", FUTURE)
    await db_session.commit()

    data = (await get(client, "/pending-balances", scope="OVERDUE")).json()

    assert [item["unit"]["label"] for item in data["items"]] == ["B1-TO1-APTO-101"]
    assert (data["scope"], data["units"], data["total_pending"], data["total_overdue"]) == ("OVERDUE", 1, "100.00",
                                                                                           "100.00")
    assert error(await get(client, "/pending-balances", scope="TODOS")) == (422, "VALIDATION_ERROR")


async def test_nobody_owes(
    client: AsyncClient, db_session: AsyncSession, signed_in: None, community: Community, books: Books
) -> None:
    unit = await community.unit(1, 1, 101)
    laura = await resident_at(community, "Laura Gómez", "1012345678", unit)
    charge = await books.charge(unit, "100.00", PAST)
    await books.apply(await books.payment(laura, unit, "300.00"), charge, "100.00")  # paid, with credit
    await db_session.commit()

    data = (await get(client, "/pending-balances")).json()

    assert (data["has_data"], data["items"], data["units"], data["total_pending"]) == (False, [], 0, "0.00")


async def test_reports_write_nothing(
    client: AsyncClient, db_session: AsyncSession, signed_in: None, community: Community, books: Books
) -> None:
    unit = await community.unit(1, 1, 101)
    laura = await resident_at(community, "Laura Gómez", "1012345678", unit)
    await books.charge(unit, "100.00", PAST)
    await books.payment(laura, unit, "50.00")
    await db_session.commit()
    tables = ("charges", "payments", "payment_allocations", "audit_logs", "users", "resident_units")
    before = [await db_session.scalar(text(f"SELECT COUNT(*) FROM {table}")) for table in tables]

    assert (await get(client, "/payments", date_from="2026-01-01", date_to="2026-12-31")).status_code == 200
    assert (await get(client, "/pending-balances")).status_code == 200

    assert [await db_session.scalar(text(f"SELECT COUNT(*) FROM {table}")) for table in tables] == before


async def test_the_grouped_balances_are_unit_balance_for_every_apartment(
    db_session: AsyncSession, community: Community, books: Books
) -> None:
    owing = await community.unit(1, 1, 101)
    laura = await resident_at(community, "Laura Gómez", "1012345678", owing)
    charge = await books.charge(owing, "100.10", PAST)
    await books.apply(await books.payment(laura, owing, "60.05"), charge, "60.05")
    credit_only = await community.unit(1, 1, 102)
    pedro = await resident_at(community, "Pedro Ruiz", "10000002", credit_only)
    await books.payment(pedro, credit_only, "33.33")
    untouched = await community.unit(1, 1, 103)
    await db_session.commit()

    grouped = await unit_balances(db_session)

    assert set(grouped) == {owing, credit_only}  # an apartment without charges or payments has nothing to group
    for unit_id in (owing, credit_only):
        assert grouped[unit_id] == await unit_balance(db_session, unit_id)
    assert (await unit_balance(db_session, untouched)).pending_balance == 0
