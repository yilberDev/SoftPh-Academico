"""HU-011 / HU-014: the account statement of a registered resident's current apartment.

GET /api/v1/admin/residents/{resident_id}/account-statement (administrator) and
GET /api/v1/resident/account-statement (the signed-in resident). The data is built in the test
transaction (Community of HU-006 plus charges, payments and allocations); the totals are checked
against payments.unit_balance, the balance of HU-004.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import bogota_today, utc_now
from app.models import Charge, Payment, PaymentAllocation, User, UserRole, UserStatus
from app.services.payments import unit_balance
from tests.integration.conftest import sign_in
from tests.integration.test_residents_api import Community, MakeUser

pytestmark = pytest.mark.anyio

API = "/api/v1"
TODAY = bogota_today()
PAST = TODAY - timedelta(days=10)  # an obligation due then and still unpaid is OVERDUE (en mora)
FUTURE = TODAY + timedelta(days=10)


class Books:
    """Charges of an apartment, payments of a resident and their allocations (test transaction)."""

    def __init__(self, db: AsyncSession, admin_id: int) -> None:
        self.db = db
        self.admin_id = admin_id

    async def charge(self, unit_id: int, amount: str, due: date, description: str = "Ruido") -> int:
        charge = Charge(unit_id=unit_id, concept="PENALTY", description=description, amount=Decimal(amount),
                        due_date=due, created_by_user_id=self.admin_id)
        self.db.add(charge)
        await self.db.flush()
        return charge.id

    async def payment(self, resident_id: int, unit_id: int, amount: str, *, name: str = "Laura Gómez",
                      paid_on: date = date(2026, 10, 1), method: str = "CASH") -> int:
        payment = Payment(resident_id=resident_id, unit_id=unit_id, registered_by_user_id=self.admin_id,
                          amount=Decimal(amount), method=method, concept="OTHER", concept_description="Abono",
                          source="MANUAL", payment_date=paid_on, registered_at=utc_now() - timedelta(days=1),
                          resident_name_snapshot=name, unit_label_snapshot="B1-TO2-APTO-104",
                          receipt_email_status="SENT", receipt_sent_at=utc_now())
        self.db.add(payment)
        await self.db.flush()
        return payment.id

    async def apply(self, payment_id: int, charge_id: int, amount: str) -> None:
        self.db.add(PaymentAllocation(payment_id=payment_id, charge_id=charge_id, amount=Decimal(amount)))
        await self.db.flush()


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
async def unit(community: Community) -> int:
    return await community.unit(1, 2, 104)


@pytest.fixture
async def laura(community: Community, unit: int) -> int:
    laura = await community.resident("Laura Gómez", "1012345678", None, email="laura@example.com")
    await community.residence(laura, unit)
    return laura


@pytest.fixture
async def ledger(books: Books, db_session: AsyncSession, unit: int, laura: int) -> dict[str, int]:
    """Four obligations, one of each status, and two payments of Laura (one with credit left)."""
    paid = await books.charge(unit, "50000.00", PAST, "Pagada")
    overdue = await books.charge(unit, "80000.00", PAST, "Vencida")
    partial = await books.charge(unit, "100000.00", FUTURE, "Abonada")
    pending = await books.charge(unit, "30000.00", FUTURE, "Pendiente")
    first = await books.payment(laura, unit, "90000.00", paid_on=date(2026, 10, 1))
    await books.apply(first, paid, "50000.00")
    await books.apply(first, partial, "40000.00")
    second = await books.payment(laura, unit, "15000.00", paid_on=date(2026, 10, 2))  # nothing applied: credit
    await db_session.commit()
    return {"paid": paid, "overdue": overdue, "partial": partial, "pending": pending, "first": first, "second": second}


async def admin_statement(client: AsyncClient, resident_id: int) -> Response:
    return await client.get(f"{API}/admin/residents/{resident_id}/account-statement")


async def own_statement(client: AsyncClient) -> Response:
    return await client.get(f"{API}/resident/account-statement")


async def as_resident(client: AsyncClient, db: AsyncSession, resident_id: int) -> None:
    user = await db.get(User, resident_id)
    assert user is not None
    await sign_in(client, db, user)


async def table_counts(db: AsyncSession) -> list[int]:
    tables = ("charges", "payments", "payment_allocations", "demo_payment_transactions", "audit_logs", "users")
    return [int(await db.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0) for table in tables]


def error(response: Response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


# --- Access (HU-018; HU-014 criterion 5.1) ----------------------------------------------------------------


async def test_without_session_both_are_401(client: AsyncClient, laura: int) -> None:
    for response in (await admin_statement(client, laura), await own_statement(client)):
        assert error(response) == (401, "NOT_AUTHENTICATED")
        assert response.headers["cache-control"] == "no-store"


async def test_each_role_only_reaches_its_own_route(
    client: AsyncClient, db_session: AsyncSession, admin: User, laura: int
) -> None:
    await as_resident(client, db_session, laura)
    assert error(await admin_statement(client, laura)) == (403, "FORBIDDEN")
    await sign_in(client, db_session, admin)
    assert error(await own_statement(client)) == (403, "FORBIDDEN")


@pytest.mark.parametrize("params", [{"resident_id": "1"}, {"user_id": "1"}, {"unit_id": "1"}, {"page": "1"}])
async def test_no_query_parameter_selects_anything(
    client: AsyncClient, db_session: AsyncSession, admin: User, laura: int, params: dict[str, str]
) -> None:
    await as_resident(client, db_session, laura)
    response = await client.get(f"{API}/resident/account-statement", params=params)
    assert error(response) == (422, "VALIDATION_ERROR")
    await sign_in(client, db_session, admin)
    response = await client.get(f"{API}/admin/residents/{laura}/account-statement", params=params)
    assert error(response) == (422, "VALIDATION_ERROR")


# --- The administrator's statement (HU-011) ----------------------------------------------------------------


async def test_the_statement_of_a_resident(
    client: AsyncClient, db_session: AsyncSession, admin: User, unit: int, laura: int, ledger: dict[str, int]
) -> None:
    await sign_in(client, db_session, admin)

    response = await admin_statement(client, laura)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["resident"] == {"id": laura, "full_name": "Laura Gómez", "status": "ACTIVE",
                                "unit": {"id": unit, "label": "B1-TO2-APTO-104"}}
    assert data["as_of"] == TODAY.isoformat() and data["has_movements"] is True
    # Criterion 1.2 / 3: every obligation (FIFO order) with its amounts and status.
    assert [(item["id"], item["description"], item["amount"], item["applied_amount"], item["outstanding_amount"],
             item["status"]) for item in data["obligations"]] == [
        (ledger["paid"], "Pagada", "50000.00", "50000.00", "0.00", "PAID"),
        (ledger["overdue"], "Vencida", "80000.00", "0.00", "80000.00", "OVERDUE"),
        (ledger["partial"], "Abonada", "100000.00", "40000.00", "60000.00", "PARTIAL"),
        (ledger["pending"], "Pendiente", "30000.00", "0.00", "30000.00", "PENDING"),
    ]
    # Criterion 2: the total pending, what was applied, the credit, and "en mora" (criterion 3.1).
    assert data["summary"] == {
        "status": "OVERDUE", "total_charged": "260000.00", "total_applied": "90000.00",
        "pending_balance": "170000.00", "overdue_balance": "80000.00", "credit_balance": "15000.00",
        "obligations": 4, "pending_obligations": 3, "overdue_obligations": 1, "payments": 2,
    }
    # Criterion 2.2: the payments, newest first, with what each one covered.
    payments = data["payments"]
    assert [(p["id"], p["amount"], p["applied_amount"], p["credit_amount"], p["status"], p["by_this_resident"],
             p["payer_name"]) for p in payments] == [
        (ledger["second"], "15000.00", "0.00", "15000.00", "CREDIT", True, "Laura Gómez"),
        (ledger["first"], "90000.00", "90000.00", "0.00", "APPLIED", True, "Laura Gómez"),
    ]
    assert payments[1]["allocations"] == [{"charge_id": ledger["paid"], "amount": "50000.00"},
                                          {"charge_id": ledger["partial"], "amount": "40000.00"}]
    assert payments[1]["receipt_number"] == f"RC-{ledger['first']:08d}"


async def test_the_totals_reconcile_with_the_balance_of_hu004(
    client: AsyncClient, db_session: AsyncSession, admin: User, unit: int, laura: int, ledger: dict[str, int]
) -> None:
    await sign_in(client, db_session, admin)
    data = (await admin_statement(client, laura)).json()
    balance = await unit_balance(db_session, unit)

    outstanding = sum(Decimal(item["outstanding_amount"]) for item in data["obligations"])
    assert Decimal(data["summary"]["pending_balance"]) == outstanding == balance.pending_balance
    assert Decimal(data["summary"]["credit_balance"]) == balance.credit_balance
    paid = sum(Decimal(p["amount"]) for p in data["payments"])
    applied = sum(Decimal(a["amount"]) for p in data["payments"] for a in p["allocations"])
    assert paid - applied == balance.credit_balance
    assert Decimal(data["summary"]["total_applied"]) == applied


@pytest.mark.parametrize(("dues", "applied", "status"), [
    ([PAST], ["0.00"], "OVERDUE"),
    ([PAST], ["10000.00"], "OVERDUE"),  # partly paid and past its due date: still en mora
    ([FUTURE], ["0.00"], "PENDING"),
    ([FUTURE, PAST], ["0.00", "50000.00"], "PENDING"),  # the past one is paid: pendiente, not en mora
    ([PAST], ["50000.00"], "UP_TO_DATE"),
    ([TODAY], ["0.00"], "PENDING"),  # due today: not overdue yet
])
async def test_pending_or_en_mora(
    client: AsyncClient, db_session: AsyncSession, admin: User, books: Books, unit: int, laura: int,
    dues: list[date], applied: list[str], status: str,
) -> None:
    payment = await books.payment(laura, unit, "100000.00")
    for due, amount in zip(dues, applied, strict=True):
        charge = await books.charge(unit, "50000.00", due)
        if amount != "0.00":
            await books.apply(payment, charge, amount)
    await db_session.commit()
    await sign_in(client, db_session, admin)

    assert (await admin_statement(client, laura)).json()["summary"]["status"] == status


async def test_a_payment_of_someone_else_keeps_who_made_it(
    client: AsyncClient, db_session: AsyncSession, admin: User, community: Community, books: Books, unit: int,
) -> None:
    # Pedro lived in the apartment before Laura: his payment is the apartment's, never Laura's.
    pedro = await community.resident("Pedro Ruiz", "10000002", None, email="pedro@example.com")
    await community.residence(pedro, unit, ended=True)
    laura = await community.resident("Laura Gómez", "1012345678", None, email="laura@example.com")
    await community.residence(laura, unit)
    charge = await books.charge(unit, "100000.00", FUTURE)
    old = await books.payment(pedro, unit, "60000.00", name="Pedro Ruiz", paid_on=date(2026, 9, 1))
    await books.apply(old, charge, "60000.00")
    own = await books.payment(laura, unit, "10000.00", paid_on=date(2026, 10, 5))
    await books.apply(own, charge, "10000.00")
    await db_session.commit()

    await sign_in(client, db_session, admin)
    admin_view = (await admin_statement(client, laura)).json()
    await as_resident(client, db_session, laura)
    resident_view = (await own_statement(client)).json()

    assert [(p["id"], p["by_this_resident"], p["payer_name"]) for p in admin_view["payments"]] == [
        (own, True, "Laura Gómez"), (old, False, "Pedro Ruiz")]
    # The resident only sees their own payment (criterion 5.1), never Pedro's name or payment ...
    assert [(p["id"], p["by_this_resident"], p["payer_name"]) for p in resident_view["payments"]] == [(own, True, None)]
    assert "Pedro" not in (await own_statement(client)).text
    # ... while the obligation and the totals are the apartment's, the same in both views.
    assert resident_view["obligations"] == admin_view["obligations"]
    assert resident_view["obligations"][0]["applied_amount"] == "70000.00"
    assert {k: v for k, v in resident_view["summary"].items() if k != "payments"} == {
        k: v for k, v in admin_view["summary"].items() if k != "payments"}


async def test_without_movements(
    client: AsyncClient, db_session: AsyncSession, admin: User, unit: int, laura: int
) -> None:
    await sign_in(client, db_session, admin)

    data = (await admin_statement(client, laura)).json()

    # CU-011 5a / CU-014 3a: a statement saying there is nothing, not an error.
    assert data["has_movements"] is False
    assert data["obligations"] == [] and data["payments"] == []
    assert data["summary"] == {
        "status": "UP_TO_DATE", "total_charged": "0.00", "total_applied": "0.00", "pending_balance": "0.00",
        "overdue_balance": "0.00", "credit_balance": "0.00", "obligations": 0, "pending_obligations": 0,
        "overdue_obligations": 0, "payments": 0,
    }


async def test_who_is_not_a_registered_resident_is_not_available(
    client: AsyncClient, db_session: AsyncSession, admin: User, community: Community
) -> None:
    others = [
        await community.resident("Pendiente Invitada", None, None, status=UserStatus.PENDING_ACTIVATION),
        await community.resident("Residencia Terminada", "10000004", (1, 1, 104), ended=True),
        await community.resident("Sin Residencia", "10000005", None),
        await community.resident("Cuenta Eliminada", "10000006", (1, 1, 105), deleted=True),
        admin.id,
        999_999,
    ]
    await sign_in(client, db_session, admin)

    for resident_id in others:
        response = await admin_statement(client, resident_id)
        # CU-011 5b/6b: "no disponible", never a partial statement.
        assert error(response) == (404, "RESIDENT_NOT_FOUND"), resident_id
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("resident_id", ["0", "-1", "abc", str(2**63)])
async def test_invalid_ids_are_422(client: AsyncClient, db_session: AsyncSession, admin: User, resident_id: str) -> None:
    await sign_in(client, db_session, admin)
    assert error(await admin_statement(client, resident_id)) == (422, "VALIDATION_ERROR")  # type: ignore[arg-type]


# --- The resident's own statement (HU-014) ------------------------------------------------------------------


async def test_the_resident_reads_the_statement_of_their_apartment(
    client: AsyncClient, db_session: AsyncSession, admin: User, laura: int, ledger: dict[str, int]
) -> None:
    await sign_in(client, db_session, admin)
    admin_view = (await admin_statement(client, laura)).json()
    await as_resident(client, db_session, laura)

    response = await own_statement(client)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    # Criteria 1-3: the same obligations, totals and statuses; the payments are their own, without payer_name.
    assert data["obligations"] == admin_view["obligations"]
    assert data["summary"] == admin_view["summary"]
    assert [p["id"] for p in data["payments"]] == [p["id"] for p in admin_view["payments"]]
    assert {p["payer_name"] for p in data["payments"]} == {None}
    pending = [item for item in data["obligations"] if item["outstanding_amount"] != "0.00"]
    assert [(item["description"], item["outstanding_amount"]) for item in pending] == [  # criterion 3.2
        ("Vencida", "80000.00"), ("Abonada", "60000.00"), ("Pendiente", "30000.00")]


async def test_each_resident_only_reads_their_own_apartment(
    client: AsyncClient, db_session: AsyncSession, community: Community, books: Books, laura: int,
    ledger: dict[str, int],
) -> None:
    pedro = await community.resident("Pedro Ruiz", "10000002", (2, 1, 201), email="pedro@example.com")
    pedro_unit = await db_session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": pedro})
    await books.charge(int(pedro_unit), "999.00", FUTURE, "De Pedro")
    await db_session.commit()

    await as_resident(client, db_session, pedro)
    data = (await own_statement(client)).json()

    assert data["resident"]["id"] == pedro
    assert [item["description"] for item in data["obligations"]] == ["De Pedro"]
    assert data["payments"] == []
    for laura_data in ("Laura", "Vencida", "80000", "1012345678", str(ledger["first"])):
        assert laura_data not in (await own_statement(client)).text


async def test_a_resident_without_a_current_apartment(
    client: AsyncClient, db_session: AsyncSession, community: Community
) -> None:
    moved = await community.resident("Residencia Terminada", "10000004", (1, 1, 104), ended=True)
    await as_resident(client, db_session, moved)

    response = await own_statement(client)

    assert error(response) == (404, "ACCOUNT_STATEMENT_UNAVAILABLE")
    assert response.json()["error"]["message"] == (
        "No tienes una unidad residencial vigente, por lo que no hay un estado de cuenta para consultar.")


# --- Up to date after a payment (criterion 4) and read only ---------------------------------------------------


async def test_a_new_payment_is_reflected_on_the_next_query(
    client: AsyncClient, db_session: AsyncSession, admin: User, unit: int, laura: int, books: Books
) -> None:
    await books.charge(unit, "100000.00", PAST)
    await db_session.commit()
    headers = await sign_in(client, db_session, admin)
    before = (await admin_statement(client, laura)).json()["summary"]

    registered = await client.post(f"{API}/admin/payments", headers=headers, json={
        "resident_id": laura, "amount": "100000", "method": "CASH", "concept": "PENALTY",
        "payment_date": TODAY.isoformat()})
    assert registered.status_code == 201, registered.text
    after = (await admin_statement(client, laura)).json()["summary"]

    assert (before["status"], before["pending_balance"]) == ("OVERDUE", "100000.00")
    assert (after["status"], after["pending_balance"], after["payments"]) == ("UP_TO_DATE", "0.00", 1)
    await as_resident(client, db_session, laura)
    assert (await own_statement(client)).json()["summary"] == after


async def test_reading_a_statement_writes_nothing(
    client: AsyncClient, db_session: AsyncSession, admin: User, laura: int, ledger: dict[str, int]
) -> None:
    before = await table_counts(db_session)

    await sign_in(client, db_session, admin)
    after_sign_in = await table_counts(db_session)
    assert (await admin_statement(client, laura)).status_code == 200
    await as_resident(client, db_session, laura)
    after_second_sign_in = await table_counts(db_session)
    assert (await own_statement(client)).status_code == 200

    assert after_sign_in == before == after_second_sign_in  # sessions are another table
    assert await table_counts(db_session) == before


async def test_the_resident_role_is_required_for_their_own_statement(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    # A RESIDENTE account without any residence row at all (never activated through HU-019).
    lonely = await make_user("sola@example.com", role=UserRole.RESIDENTE, password_hash="not-a-real-hash")
    await sign_in(client, db_session, lonely)

    assert error(await own_statement(client)) == (404, "ACCOUNT_STATEMENT_UNAVAILABLE")
