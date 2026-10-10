"""HU-010-D: GET /resident/demo-payments/obligations, the obligations the signed-in resident can pay.

Over real HTTP against MySQL: the balances come from the persisted allocations (manual payments of
HU-004 and approved demo payments alike), only the current apartment's obligations with an outstanding
balance are listed, the resident is always the session's and nothing is written. The list is only for
choosing an obligation: starting checks everything again (test_demo_payments_api.py).
"""

from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_demo_payments_api import (
    BASE,
    NO_STORE,
    as_user,
    disabled_client,
    start,
    statuses,
)
from tests.integration.test_demo_payments_service import (
    NOW,
    TTL,
    Clock,
    World,
    clock,
    count,
    financial_state,
    manual_payment,
    rows,
    world,
)
from tests.integration.test_payments_api import charge

pytestmark = pytest.mark.anyio

__all__ = ["clock", "disabled_client", "world"]  # fixtures shared with the demo payment tests

URL = f"{BASE}/obligations"
ITEM_FIELDS = {"charge_id", "concept", "description", "billing_period", "due_date", "amount", "applied_amount",
               "outstanding_amount", "status", "in_progress_transaction_id"}


async def allocate(db: AsyncSession, world: World, charge_id: int, amount: str) -> None:
    """A manual payment of Laura applied entirely to one obligation (as HU-004 leaves it)."""
    await db.execute(text(
        "INSERT INTO payments (resident_id, unit_id, registered_by_user_id, amount, method, concept, payment_date, "
        "registered_at, resident_name_snapshot, unit_label_snapshot) VALUES (:r, :u, :a, :m, 'CASH', 'MONTHLY_FEE', "
        "'2026-10-01', UTC_TIMESTAMP(6), 'Laura Gómez', 'B1-TO1-APTO-101')"),
        {"r": world.laura, "u": world.laura_unit, "a": world.admin, "m": amount})
    await db.execute(text("INSERT INTO payment_allocations (payment_id, charge_id, amount) SELECT MAX(id), :c, :m "
                          "FROM payments"), {"c": charge_id, "m": amount})


async def obligations(client: AsyncClient) -> Response:
    response = await client.get(URL)
    assert response.headers.get("cache-control") == NO_STORE
    return response


def balances(response: Response) -> list[tuple[Any, ...]]:
    return [(i["charge_id"], i["applied_amount"], i["outstanding_amount"], i["status"])
            for i in response.json()["items"]]


async def everything(db: AsyncSession) -> list[Any]:
    """Every row a read must leave alone."""
    return [*await financial_state(db), await statuses(db),
            await rows(db, "SELECT COUNT(*) FROM audit_logs")]


# --- What is listed ---------------------------------------------------------------------------------------


async def test_lists_the_outstanding_obligations_with_their_balances(client: AsyncClient, db_session: AsyncSession,
                                                                     world: World, clock: Clock) -> None:
    repair = await charge(db_session, world.laura_unit, "50000.50", date(2026, 10, 20), "OTHER",
                          "Reparación de la fachada")
    penalty = await charge(db_session, world.laura_unit, "20000", date(2026, 11, 5), "PENALTY", "Ruido")
    paid = await charge(db_session, world.laura_unit, "1000", date(2026, 9, 5))  # fully covered: never listed
    await allocate(db_session, world, paid, "1000")
    await allocate(db_session, world, repair, "10000.25")  # partially covered
    await as_user(client, db_session, world.laura)
    before = await everything(db_session)

    response = await obligations(client)

    assert response.status_code == 200 and set(response.json()) == {"items"}
    items = response.json()["items"]
    assert all(set(item) == ITEM_FIELDS for item in items)  # no apartment, resident, author or allocations
    # FIFO (due date first); the status follows the existing rules for Bogotá's today (2026-10-09).
    assert balances(response) == [
        (world.fee, "0.00", "100000.00", "OVERDUE"),  # due 2026-10-05: its due date already ended
        (repair, "10000.25", "40000.25", "PARTIAL"),
        (penalty, "0.00", "20000.00", "PENDING"),
    ]
    assert items[0] == {
        "charge_id": world.fee, "concept": "MONTHLY_FEE", "description": None, "billing_period": "2026-10",
        "due_date": "2026-10-05", "amount": "100000.00", "applied_amount": "0.00", "outstanding_amount": "100000.00",
        "status": "OVERDUE", "in_progress_transaction_id": None,
    }
    assert (items[1]["concept"], items[1]["description"], items[1]["billing_period"], items[1]["amount"]) == (
        "OTHER", "Reparación de la fachada", None, "50000.50")
    assert await everything(db_session) == before  # a read: nothing written, not even an audit entry


async def test_a_resident_without_obligations_gets_an_empty_list(client: AsyncClient, db_session: AsyncSession,
                                                                 world: World, clock: Clock) -> None:
    await as_user(client, db_session, world.pedro)  # Pedro's apartment has no obligations
    response = await obligations(client)
    assert response.status_code == 200 and response.json() == {"items": []}


async def test_payments_already_applied_are_reflected(client: AsyncClient, db_session: AsyncSession, world: World,
                                                      clock: Clock) -> None:
    await manual_payment(db_session, world, "30000")  # HU-004: FIFO over Laura's apartment
    await as_user(client, db_session, world.laura)
    assert balances(await obligations(client)) == [(world.fee, "30000.00", "70000.00", "OVERDUE")]

    await manual_payment(db_session, world, "70000")  # covers the rest
    assert (await obligations(client)).json() == {"items": []}


async def test_an_approved_demo_payment_updates_the_list(client: AsyncClient, db_session: AsyncSession, world: World,
                                                         clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)

    async def approve(amount: str) -> None:
        started = (await start(client, headers, world.fee, amount)).json()
        approved = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json={"outcome": "APPROVED"})
        assert approved.status_code == 200 and approved.json()["status"] == "APPROVED", approved.text

    await approve("25000.75")  # partial
    assert balances(await obligations(client)) == [(world.fee, "25000.75", "74999.25", "OVERDUE")]
    await approve("74999.25")  # the exact remaining balance
    assert (await obligations(client)).json() == {"items": []}
    assert await count(db_session, "payment_allocations") == 2


@pytest.mark.parametrize("outcome", ["REJECTED", "FAILED", "CANCELLED"])
async def test_an_unsuccessful_transaction_leaves_the_balance(client: AsyncClient, db_session: AsyncSession,
                                                              world: World, clock: Clock, outcome: str) -> None:
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "100000")).json()
    if outcome == "CANCELLED":
        ended = await client.post(f"{BASE}/{started['id']}/cancel", headers=headers)
    else:
        ended = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json={"outcome": outcome})
    assert ended.json()["status"] == outcome
    assert balances(await obligations(client)) == [(world.fee, "0.00", "100000.00", "OVERDUE")]


# --- The resident's own live transaction -------------------------------------------------------------------


async def test_reports_the_residents_live_transaction_without_persisting_its_expiry(
    client: AsyncClient, db_session: AsyncSession, world: World, clock: Clock
) -> None:
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "1000")).json()

    [item] = (await obligations(client)).json()["items"]
    assert item["in_progress_transaction_id"] == started["id"]
    assert item["outstanding_amount"] == "100000.00"  # nothing applied while INITIATED

    clock.now = NOW + TTL - timedelta(microseconds=1)
    assert (await obligations(client)).json()["items"][0]["in_progress_transaction_id"] == started["id"]
    clock.now = NOW + TTL  # expired from expires_at on: no longer reported...
    assert (await obligations(client)).json()["items"][0]["in_progress_transaction_id"] is None
    # ...but the list never writes: the transaction is still INITIATED until get or start settles it.
    assert await statuses(db_session) == [(started["id"], "INITIATED", None)]
    assert (await client.get(f"{BASE}/{started['id']}")).json()["status"] == "EXPIRED"


# --- Whose obligations ---------------------------------------------------------------------------------------


async def test_only_the_current_apartment_of_the_session_resident(client: AsyncClient, db_session: AsyncSession,
                                                                  world: World, clock: Clock) -> None:
    pedro_fee = await charge(db_session, world.pedro_unit, "90000", date(2026, 10, 5))
    await as_user(client, db_session, world.laura)

    laura = await obligations(client)
    assert [i["charge_id"] for i in laura.json()["items"]] == [world.fee]
    # Headers and cookies select nothing either: the session decides.
    client.cookies.set("resident_id", str(world.pedro))
    response = await client.get(URL, headers={"X-Resident-Id": str(world.pedro), "X-User-Id": str(world.pedro)})
    assert response.status_code == 200 and response.json() == laura.json()

    await as_user(client, db_session, world.pedro)
    assert [i["charge_id"] for i in (await obligations(client)).json()["items"]] == [pedro_fee]


@pytest.mark.parametrize("query", ["resident_id={pedro}", "user_id={pedro}", "unit_id={pedro_unit}",
                                   "charge_id={pedro_fee}", "page=1", "status=PAID", "resident_id="])
async def test_unknown_query_parameters_are_rejected(client: AsyncClient, db_session: AsyncSession, world: World,
                                                     clock: Clock, query: str) -> None:
    # As HU-008: the route takes no query parameter, and a resident is never chosen by the client.
    pedro_fee = await charge(db_session, world.pedro_unit, "90000", date(2026, 10, 5))
    await as_user(client, db_session, world.laura)
    before = await everything(db_session)
    name = query.split("=")[0]

    response = await client.get(f"{URL}?{query.format(pedro=world.pedro, pedro_unit=world.pedro_unit, pedro_fee=pedro_fee)}")

    assert response.status_code == 422 and response.headers.get("cache-control") == NO_STORE
    assert response.json()["error"]["details"] == [{"field": f"query.{name}", "message": "Este campo no está permitido."}]
    assert "items" not in response.json()  # no obligation of anyone, not even Laura's
    assert await everything(db_session) == before


async def test_the_session_is_checked_before_the_query(client: AsyncClient, db_session: AsyncSession, world: World,
                                                       clock: Clock) -> None:
    tampered = f"{URL}?resident_id={world.laura}"
    anonymous = await client.get(tampered)
    assert anonymous.status_code == 401 and anonymous.json()["error"]["code"] == "NOT_AUTHENTICATED"
    await as_user(client, db_session, world.admin)
    administrator = await client.get(tampered)
    assert administrator.status_code == 403 and administrator.json()["error"]["code"] == "FORBIDDEN"
    assert all(r.headers.get("cache-control") == NO_STORE for r in (anonymous, administrator))


async def test_another_residents_live_transaction_is_not_reported(client: AsyncClient, db_session: AsyncSession,
                                                                  world: World, clock: Clock) -> None:
    laura = await as_user(client, db_session, world.laura)
    started = (await start(client, laura, world.fee, "1000")).json()
    # Laura moves out and Pedro moves in: the obligation is now his apartment's, the transaction is hers.
    await db_session.execute(text("UPDATE resident_units SET ended_at = UTC_TIMESTAMP(6) WHERE user_id = :l"),
                             {"l": world.laura})
    await db_session.execute(text("UPDATE resident_units SET unit_id = :u WHERE user_id = :p"),
                             {"p": world.pedro, "u": world.laura_unit})

    assert (await obligations(client)).json() == {"items": []}  # Laura no longer has a current apartment
    await as_user(client, db_session, world.pedro)
    [item] = (await obligations(client)).json()["items"]
    assert item["charge_id"] == world.fee and item["in_progress_transaction_id"] is None  # never Laura's id
    assert await statuses(db_session) == [(started["id"], "INITIATED", None)]  # still hers, still live


async def test_a_deleted_apartment_has_nothing_to_pay(client: AsyncClient, db_session: AsyncSession, world: World,
                                                       clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)
    await db_session.execute(text("UPDATE units SET deleted_at = UTC_TIMESTAMP(6) WHERE id = :u"), {"u": world.laura_unit})
    assert (await obligations(client)).json() == {"items": []}
    # The same rule as starting: an obligation of a deleted apartment cannot be paid.
    assert (await start(client, headers, world.fee, "1000")).json()["error"]["code"] == "CHARGE_NOT_FOUND"


# --- Who can call it --------------------------------------------------------------------------------------


async def test_without_a_session_it_is_401(client: AsyncClient, db_session: AsyncSession, world: World,
                                           clock: Clock) -> None:
    response = await obligations(client)
    assert response.status_code == 401 and response.json()["error"]["code"] == "NOT_AUTHENTICATED"


async def test_a_disabled_resident_is_401(client: AsyncClient, db_session: AsyncSession, world: World,
                                          clock: Clock) -> None:
    await as_user(client, db_session, world.laura)
    await db_session.execute(text("UPDATE users SET status = 'DISABLED' WHERE id = :u"), {"u": world.laura})
    response = await obligations(client)
    assert response.status_code == 401 and response.json()["error"]["code"] == "NOT_AUTHENTICATED"


async def test_the_administrator_is_forbidden(client: AsyncClient, db_session: AsyncSession, world: World,
                                              clock: Clock) -> None:
    await as_user(client, db_session, world.admin)
    response = await obligations(client)
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
    assert "items" not in response.json()


async def test_it_is_not_read_as_a_transaction_id(client: AsyncClient, db_session: AsyncSession, world: World,
                                                  clock: Clock) -> None:
    await as_user(client, db_session, world.laura)
    # Declared before /{transaction_id}: never a 422 for the path, never DEMO_TRANSACTION_NOT_FOUND.
    assert (await obligations(client)).status_code == 200
    assert (await client.get(f"{BASE}/obligation")).status_code == 422  # anything else is still a bad id


async def test_disabled_the_route_does_not_exist(disabled_client: AsyncClient, db_session: AsyncSession,
                                                 world: World, clock: Clock) -> None:
    await as_user(disabled_client, db_session, world.laura)
    response = await disabled_client.get(URL)
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


def test_the_openapi_contract() -> None:
    from app.main import app

    spec = app.openapi()
    operation = spec["paths"][URL]["get"]
    assert "requestBody" not in operation and operation.get("parameters", []) == []
    schemas = spec["components"]["schemas"]
    listed = schemas[operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]]
    item = schemas[listed["properties"]["items"]["items"]["$ref"].split("/")[-1]]
    assert set(item["properties"]) == ITEM_FIELDS
    for field in ("amount", "applied_amount", "outstanding_amount"):  # decimal text, never a JSON float
        assert "number" not in str(item["properties"][field]), item["properties"][field]
