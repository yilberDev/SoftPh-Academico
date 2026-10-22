"""HU-010-C: the resident's demo payment routes over real HTTP (session cookie, Origin, CSRF, NoStoreRoute).
Completing takes a fictitious test instrument ({"instrument_id"}), never an outcome; GET /instruments
lists the catalog.

The routes are published only with DEMO_PAYMENTS_ENABLED; the integration tests enable them
explicitly on the application under test (tests/integration/conftest.py), and the tests at the end
check both settings with create_app(). The service rules themselves are tested in
test_demo_payments_service.py; here: what the API accepts, who can call it and what it answers.
"""

import re
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.sessions import CSRF_HEADER_NAME
from app.db.session import get_db
from app.main import app, create_app
from app.models import User
from app.services.demo_payments import complete_demo_payment
from tests.integration.conftest import sign_in
from tests.integration.test_demo_payments_service import (
    NOW,
    Clock,
    World,
    audits,
    clock,
    count,
    financial_state,
    outstanding,
    rows,
    world,
)
from tests.integration.test_payments_api import charge

pytestmark = pytest.mark.anyio

__all__ = ["clock", "world"]  # fixtures shared with the service tests

API = "/api/v1"
BASE = f"{API}/resident/demo-payments"
NO_STORE = "no-store"
OPERATIONS = {
    ("post", f"{API}/resident/demo-payments"),
    ("get", f"{API}/resident/demo-payments/obligations"),
    ("get", f"{API}/resident/demo-payments/instruments"),
    ("get", f"{API}/resident/demo-payments/{{transaction_id}}"),
    ("post", f"{API}/resident/demo-payments/{{transaction_id}}/complete"),
    ("post", f"{API}/resident/demo-payments/{{transaction_id}}/cancel"),
}
READ_FIELDS = {"id", "reference", "charge_id", "amount", "status", "created_at", "expires_at", "finished_at",
               "payment_id", "receipt_number", "test_instrument", "result_reason"}
APPROVE = {"instrument_id": "TEST_CARD_APPROVE"}
FUNDS = {"instrument_id": "TEST_CARD_INSUFFICIENT_FUNDS"}
TECHNICAL = {"instrument_id": "TEST_ACCOUNT_TECHNICAL_ERROR"}
NOT_FOUND = {"error": {"code": "DEMO_TRANSACTION_NOT_FOUND", "message": "La transacción no existe.", "details": []}}


async def as_user(client: AsyncClient, db: AsyncSession, user_id: int) -> dict[str, str]:
    user = await db.get(User, user_id)
    assert user is not None
    return await sign_in(client, db, user)


async def start(client: AsyncClient, headers: dict[str, str], charge_id: Any, amount: Any) -> Response:
    return await client.post(BASE, headers=headers, json={"charge_id": charge_id, "amount": amount})


def no_store(response: Response) -> bool:
    return response.headers.get("cache-control") == NO_STORE


async def statuses(db: AsyncSession) -> list[tuple[Any, ...]]:
    return await rows(db, "SELECT id, status, payment_id FROM demo_payment_transactions ORDER BY id")


# --- Start ---------------------------------------------------------------------------------------------------


async def test_start_answers_an_initiated_transaction(client: AsyncClient, db_session: AsyncSession, world: World,
                                                      clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)
    before = await financial_state(db_session)

    response = await start(client, headers, world.fee, "40000.50")

    assert response.status_code == 201 and no_store(response)
    body = response.json()
    assert set(body) == READ_FIELDS  # nothing else: no resident, session, audit or card data
    assert body["status"] == "INITIATED" and body["charge_id"] == world.fee and body["amount"] == "40000.50"
    assert re.fullmatch(r"DEMO-[0-9A-F]{16}", body["reference"])
    assert body["created_at"] == "2026-10-09T20:00:00Z" and body["expires_at"] == "2026-10-09T20:15:00Z"
    assert (body["finished_at"], body["payment_id"], body["receipt_number"]) == (None, None, None)
    assert await rows(db_session, "SELECT resident_id, charge_id, status FROM demo_payment_transactions") == [
        (world.laura, world.fee, "INITIATED")]
    assert await financial_state(db_session) == before  # nothing paid yet
    [(_, _, actor, _)] = await audits(db_session, "DEMO_PAYMENT_STARTED")
    assert actor == world.laura


@pytest.mark.parametrize(("body", "field"), [
    ({"charge_id": 1, "amount": 1000.5}, "amount"),  # a JSON number with a fraction: never money
    ({"charge_id": 1, "amount": "0"}, "amount"),
    ({"charge_id": 1, "amount": "-1000"}, "amount"),
    ({"charge_id": 1, "amount": "1000.001"}, "amount"),  # more than 2 decimals: rejected, never rounded
    ({"charge_id": 1, "amount": "1.000,50"}, "amount"),
    ({"charge_id": 1, "amount": "10000000000"}, "amount"),
    ({"charge_id": 1, "amount": None}, "amount"),
    ({"charge_id": 1}, "amount"),
    ({"charge_id": "1", "amount": "1000"}, "charge_id"),
    ({"charge_id": True, "amount": "1000"}, "charge_id"),
    ({"charge_id": 0, "amount": "1000"}, "charge_id"),
    ({"amount": "1000"}, "charge_id"),
    ({"charge_id": 1, "amount": "1000", "resident_id": 1}, "resident_id"),  # never from the client
    ({"charge_id": 1, "amount": "1000", "reference": "DEMO-X"}, "reference"),
    ({"charge_id": 1, "amount": "1000", "status": "APPROVED"}, "status"),
])
async def test_invalid_start_requests_are_rejected(client: AsyncClient, db_session: AsyncSession, world: World,
                                                   clock: Clock, body: dict[str, Any], field: str) -> None:
    headers = await as_user(client, db_session, world.laura)
    # charge_id 1 stands for Laura's real obligation (True is not 1 here: it must stay a boolean).
    body = {k: (world.fee if k == "charge_id" and type(v) is int and v == 1 else v) for k, v in body.items()}

    response = await client.post(BASE, headers=headers, json=body)

    assert response.status_code == 422 and no_store(response), response.text
    assert [d["field"] for d in response.json()["error"]["details"]] == [field]
    assert await count(db_session, "demo_payment_transactions") == 0


async def test_the_amount_is_limited_by_the_outstanding_balance(client: AsyncClient, db_session: AsyncSession,
                                                                world: World, clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)
    response = await start(client, headers, world.fee, "100000.01")
    assert response.status_code == 422 and no_store(response)
    assert response.json()["error"]["details"] == [
        {"field": "amount", "message": "El monto no puede superar el saldo pendiente de la obligación ($ 100.000,00)."}]


async def test_only_an_own_payable_obligation_can_be_started(client: AsyncClient, db_session: AsyncSession,
                                                             world: World, clock: Clock) -> None:
    pedro_fee = await charge(db_session, world.pedro_unit, "90000", date(2026, 10, 5))
    paid = await charge(db_session, world.laura_unit, "1000", date(2026, 9, 5))
    await db_session.execute(text("INSERT INTO payments (resident_id, unit_id, registered_by_user_id, amount, method, "
                                  "concept, payment_date, registered_at, resident_name_snapshot, unit_label_snapshot) "
                                  "VALUES (:r, :u, :a, 1000, 'CASH', 'MONTHLY_FEE', '2026-09-05', UTC_TIMESTAMP(6), "
                                  "'Laura Gómez', 'B1-TO1-APTO-101')"),
                             {"r": world.laura, "u": world.laura_unit, "a": world.admin})
    await db_session.execute(text("INSERT INTO payment_allocations (payment_id, charge_id, amount) "
                                  "SELECT MAX(id), :c, 1000 FROM payments"), {"c": paid})
    headers = await as_user(client, db_session, world.laura)

    missing = await start(client, headers, 999_999_999, "1000")
    other = await start(client, headers, pedro_fee, "1000")  # Pedro's obligation looks exactly like a missing one
    assert missing.status_code == other.status_code == 404 and missing.json() == other.json() == {
        "error": {"code": "CHARGE_NOT_FOUND", "message": "La obligación no existe.", "details": []}}
    covered = await start(client, headers, paid, "1000")
    assert covered.status_code == 409 and covered.json()["error"]["code"] == "CHARGE_NOT_PAYABLE"
    assert all(no_store(r) for r in (missing, other, covered))
    assert await count(db_session, "demo_payment_transactions") == 0


async def test_a_resident_who_moved_out_or_was_disabled_cannot_start(client: AsyncClient, db_session: AsyncSession,
                                                                     world: World, clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)
    await db_session.execute(text("UPDATE resident_units SET ended_at = UTC_TIMESTAMP(6) WHERE user_id = :u"),
                             {"u": world.laura})
    moved = await start(client, headers, world.fee, "1000")  # the obligation is no longer of her apartment
    assert moved.status_code == 404 and moved.json()["error"]["code"] == "CHARGE_NOT_FOUND"

    pedro = await as_user(client, db_session, world.pedro)
    pedro_fee = await charge(db_session, world.pedro_unit, "90000", date(2026, 10, 5))
    await db_session.execute(text("UPDATE users SET status = 'DISABLED' WHERE id = :u"), {"u": world.pedro})
    disabled = await start(client, pedro, pedro_fee, "1000")  # a disabled account has no valid session
    assert disabled.status_code == 401 and disabled.json()["error"]["code"] == "NOT_AUTHENTICATED"
    assert await count(db_session, "demo_payment_transactions") == 0


# --- Reading and ownership ------------------------------------------------------------------------------------


async def test_the_resident_reads_their_own_transaction(client: AsyncClient, db_session: AsyncSession, world: World,
                                                        clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "1000")).json()

    response = await client.get(f"{BASE}/{started['id']}")

    assert response.status_code == 200 and no_store(response)
    assert response.json() == started


async def test_another_residents_transaction_does_not_exist(client: AsyncClient, db_session: AsyncSession,
                                                            world: World, clock: Clock) -> None:
    laura = await as_user(client, db_session, world.laura)
    started = (await start(client, laura, world.fee, "1000")).json()
    before = await statuses(db_session)
    pedro = await as_user(client, db_session, world.pedro)

    answers = []
    for transaction_id in (started["id"], 999_999_999):  # Pedro asks for Laura's, then for a missing one
        answers += [
            await client.get(f"{BASE}/{transaction_id}"),
            await client.post(f"{BASE}/{transaction_id}/complete", headers=pedro, json=APPROVE),
            await client.post(f"{BASE}/{transaction_id}/cancel", headers=pedro),
        ]

    assert [(r.status_code, r.json()) for r in answers] == [(404, NOT_FOUND)] * 6  # nothing tells them apart
    assert all(no_store(r) for r in answers)
    assert await statuses(db_session) == before and await count(db_session, "payments") == 0


@pytest.mark.parametrize("path", ["/0", "/-1", "/abc", f"/{2**64}"])
async def test_invalid_transaction_ids_are_rejected(client: AsyncClient, db_session: AsyncSession, world: World,
                                                    path: str) -> None:
    await as_user(client, db_session, world.laura)
    response = await client.get(f"{BASE}{path}")
    assert response.status_code == 422 and no_store(response)


# --- Who can call it ------------------------------------------------------------------------------------------


def requests_for(transaction_id: int, charge_id: int) -> list[tuple[str, str, Any]]:
    return [
        ("POST", BASE, {"charge_id": charge_id, "amount": "1000"}),
        ("GET", f"{BASE}/obligations", None),
        ("GET", f"{BASE}/{transaction_id}", None),
        ("GET", f"{BASE}/instruments", None),
        ("POST", f"{BASE}/{transaction_id}/complete", APPROVE),
        ("POST", f"{BASE}/{transaction_id}/cancel", None),
    ]


async def initiated(client: AsyncClient, db: AsyncSession, world: World) -> int:
    headers = await as_user(client, db, world.laura)
    transaction_id = int((await start(client, headers, world.fee, "1000")).json()["id"])
    client.cookies.clear()
    return transaction_id


async def test_anonymous_requests_are_rejected(client: AsyncClient, db_session: AsyncSession, world: World,
                                               clock: Clock) -> None:
    transaction_id = await initiated(client, db_session, world)
    before = await statuses(db_session)
    for method, path, body in requests_for(transaction_id, world.fee):
        response = await client.request(method, path, json=body, headers={"Origin": get_settings().app_origin})
        assert response.status_code == 401 and no_store(response), (method, path)
        assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"
    assert await statuses(db_session) == before


async def test_the_administrator_is_forbidden(client: AsyncClient, db_session: AsyncSession, world: World,
                                              clock: Clock) -> None:
    transaction_id = await initiated(client, db_session, world)
    before = await statuses(db_session)
    headers = await as_user(client, db_session, world.admin)
    for method, path, body in requests_for(transaction_id, world.fee):
        response = await client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403 and no_store(response), (method, path)
        assert response.json()["error"]["code"] == "FORBIDDEN"
    assert await statuses(db_session) == before and await count(db_session, "payments") == 0


@pytest.mark.parametrize("problem", ["no-csrf", "wrong-csrf", "no-origin", "wrong-origin"])
async def test_every_post_needs_origin_and_csrf(client: AsyncClient, db_session: AsyncSession, world: World,
                                                clock: Clock, problem: str) -> None:
    transaction_id = await initiated(client, db_session, world)
    headers = await as_user(client, db_session, world.laura)
    before = await statuses(db_session)
    match problem:
        case "no-csrf":
            del headers[CSRF_HEADER_NAME]
        case "wrong-csrf":
            headers[CSRF_HEADER_NAME] = "x" * 43
        case "no-origin":
            del headers["Origin"]
        case "wrong-origin":
            headers["Origin"] = "https://evil.example"
    for method, path, body in requests_for(transaction_id, world.fee):
        if method != "POST":
            continue
        response = await client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403 and no_store(response), (problem, path)
        assert response.json()["error"]["code"] == "CSRF_FAILED"
    assert await statuses(db_session) == before and await count(db_session, "payments") == 0


# --- The catalog of test instruments -------------------------------------------------------------------------


async def test_the_catalog_of_test_instruments(client: AsyncClient, db_session: AsyncSession, world: World) -> None:
    await as_user(client, db_session, world.laura)

    response = await client.get(f"{BASE}/instruments")

    assert response.status_code == 200 and no_store(response)
    assert response.json() == {"items": [
        {"id": "TEST_CARD_APPROVE", "label": "Tarjeta de prueba ficticia terminada en 0001", "kind": "CARD",
         "ending": "0001", "expected_result": "APPROVED",
         "description": "Instrumento ficticio de demostración: simula un pago aprobado. No es una tarjeta real."},
        {"id": "TEST_CARD_INSUFFICIENT_FUNDS", "label": "Tarjeta de prueba ficticia terminada en 0002", "kind": "CARD",
         "ending": "0002", "expected_result": "REJECTED",
         "description": "Instrumento ficticio de demostración: simula un rechazo por fondos insuficientes. "
                        "No es una tarjeta real."},
        {"id": "TEST_ACCOUNT_TECHNICAL_ERROR", "label": "Cuenta de prueba ficticia", "kind": "ACCOUNT",
         "ending": None, "expected_result": "FAILED",
         "description": "Instrumento ficticio de demostración: simula un error técnico de procesamiento. "
                        "No es una cuenta real."},
    ]}
    assert (await client.get(f"{BASE}/instruments")).json() == response.json()  # always the same order


@pytest.mark.parametrize("query", ["?resident_id=1", "?instrument_id=TEST_CARD_APPROVE", "?all=true"])
async def test_the_catalog_takes_no_query(client: AsyncClient, db_session: AsyncSession, world: World,
                                          query: str) -> None:
    await as_user(client, db_session, world.laura)
    response = await client.get(f"{BASE}/instruments{query}")
    assert response.status_code == 422 and no_store(response)


# --- Completing --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {"outcome": "APPROVED"}, {"outcome": "REJECTED"},  # the old contract: never again
    {"outcome": "APPROVED", "instrument_id": "TEST_CARD_APPROVE"},  # not even next to an instrument
    {"instrument_id": "TEST_CARD_APPROVE", "status": "APPROVED"},
    {"instrument_id": "APPROVED"}, {"instrument_id": "TEST_CARD_UNKNOWN"}, {"instrument_id": "test_card_approve"},
    {"instrument_id": " TEST_CARD_APPROVE"}, {"instrument_id": ""}, {"instrument_id": None}, {"instrument_id": 1},
    {"instrument_id": ["TEST_CARD_APPROVE"]}, {"instrument_id": "4242424242424242"}, {"instrument_id": "X" * 41}, {},
    {"instrument_id": "TEST_CARD_APPROVE", "amount": "1"},  # never a new amount, reference, charge or resident
    {"instrument_id": "TEST_CARD_APPROVE", "charge_id": 1}, {"instrument_id": "TEST_CARD_APPROVE", "reference": "DEMO-X"},
    {"instrument_id": "TEST_CARD_APPROVE", "resident_id": 1},
    {"instrument_id": "TEST_CARD_APPROVE", "card_number": "4242424242424242", "cvv": "123"},  # never card data
])
async def test_only_an_available_instrument_completes(client: AsyncClient, db_session: AsyncSession, world: World,
                                                      clock: Clock, body: dict[str, Any]) -> None:
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "1000")).json()
    before = await financial_state(db_session)

    response = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json=body)

    assert response.status_code == 422 and no_store(response), response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await rows(db_session, "SELECT status, payment_id, test_instrument FROM demo_payment_transactions") == [
        ("INITIATED", None, None)]
    assert await financial_state(db_session) == before and await count(db_session, "payments") == 0


async def test_the_old_outcome_contract_is_refused_field_by_field(client: AsyncClient, db_session: AsyncSession,
                                                                  world: World, clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "1000")).json()
    old = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json={"outcome": "APPROVED"})
    both = await client.post(f"{BASE}/{started['id']}/complete", headers=headers,
                             json={"outcome": "APPROVED"} | APPROVE)
    unknown = await client.post(f"{BASE}/{started['id']}/complete", headers=headers,
                                json={"instrument_id": "TEST_CARD_UNKNOWN"})

    assert sorted(map(str, old.json()["error"]["details"])) == sorted(map(str, [
        {"field": "instrument_id", "message": "Este campo es obligatorio."},
        {"field": "outcome", "message": "Este campo no está permitido."},
    ]))
    assert both.json()["error"]["details"] == [{"field": "outcome", "message": "Este campo no está permitido."}]
    assert unknown.json()["error"]["details"] == [
        {"field": "instrument_id", "message": "Selecciona un instrumento de prueba válido."}]  # the service's 422
    assert (old.status_code, both.status_code, unknown.status_code) == (422, 422, 422)
    assert await statuses(db_session) == [(started["id"], "INITIATED", None)]


async def test_approval_pays_the_selected_obligation_once(client: AsyncClient, db_session: AsyncSession,
                                                          world: World, clock: Clock) -> None:
    older = await charge(db_session, world.laura_unit, "80000", date(2026, 9, 5))  # FIFO would pay this one first
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "100000")).json()
    clock.now = NOW + timedelta(minutes=2)

    first = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json=APPROVE)
    again = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json=APPROVE)

    assert first.status_code == again.status_code == 200 and no_store(first)
    approved = first.json()
    assert again.json() == approved  # the same answer, no second payment
    assert approved["status"] == "APPROVED" and approved["finished_at"] == "2026-10-09T20:02:00Z"
    assert (approved["test_instrument"], approved["result_reason"]) == ("TEST_CARD_APPROVE", None)
    assert (await client.get(f"{BASE}/{started['id']}")).json() == approved  # the same on a reload
    assert approved["receipt_number"] == f"RC-{approved['payment_id']:08d}"
    assert await rows(db_session, "SELECT id, amount, method, source, registered_by_user_id, transaction_reference "
                                  "FROM payments") == [
        (approved["payment_id"], Decimal("100000.00"), "DEMO_GATEWAY", "DEMO_GATEWAY", world.laura, started["reference"])]
    assert await rows(db_session, "SELECT payment_id, charge_id, amount FROM payment_allocations") == [
        (approved["payment_id"], world.fee, Decimal("100000.00"))]
    assert (await outstanding(db_session, world.fee), await outstanding(db_session, older)) == (0, Decimal("80000.00"))
    assert len(await audits(db_session, "DEMO_PAYMENT_APPROVED")) == 1


@pytest.mark.parametrize(("instrument", "outcome", "reason"), [
    (FUNDS, "REJECTED", "INSUFFICIENT_FUNDS"),
    (TECHNICAL, "FAILED", "TECHNICAL_ERROR"),
    (None, "CANCELLED", None),  # cancelled: no instrument
])
async def test_an_unsuccessful_end_changes_nothing(client: AsyncClient, db_session: AsyncSession, world: World,
                                                   clock: Clock, instrument: dict[str, str] | None, outcome: str,
                                                   reason: str | None) -> None:
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "100000")).json()
    before = await financial_state(db_session)

    if instrument is None:
        ended = await client.post(f"{BASE}/{started['id']}/cancel", headers=headers)
    else:
        ended = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json=instrument)

    assert ended.status_code == 200 and no_store(ended)
    assert ended.json()["status"] == outcome and ended.json()["payment_id"] is None
    assert ended.json()["receipt_number"] is None
    used = instrument["instrument_id"] if instrument else None
    assert (ended.json()["test_instrument"], ended.json()["result_reason"]) == (used, reason)
    assert (await client.get(f"{BASE}/{started['id']}")).json() == ended.json()  # the same on a reload
    assert await rows(db_session, "SELECT status, test_instrument FROM demo_payment_transactions") == [(outcome, used)]
    assert await financial_state(db_session) == before
    assert await outstanding(db_session, world.fee) == Decimal("100000.00")
    # Finished: the same request answers the same; anything else is a 409 and nothing changes.
    same = (await client.post(f"{BASE}/{started['id']}/cancel", headers=headers) if instrument is None else
            await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json=instrument))
    assert same.status_code == 200 and same.json() == ended.json()
    late = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json=APPROVE)
    assert late.status_code == 409 and no_store(late)
    assert late.json()["error"] == {"code": "DEMO_TRANSACTION_FINISHED",
                                    "message": "La transacción ya terminó y no admite cambios.", "details": []}
    assert await financial_state(db_session) == before


async def test_an_expired_transaction_is_never_paid(client: AsyncClient, db_session: AsyncSession, world: World,
                                                    clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "100000")).json()
    clock.now = NOW + timedelta(minutes=15)  # exactly expires_at

    approve = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json=APPROVE)
    cancel = await client.post(f"{BASE}/{started['id']}/cancel", headers=headers)
    read = await client.get(f"{BASE}/{started['id']}")

    expired = {"code": "DEMO_TRANSACTION_EXPIRED",
               "message": "La transacción venció. El pago no fue aplicado; inicia uno nuevo si aún quieres pagar.",
               "details": []}
    assert (approve.status_code, approve.json()["error"]) == (409, expired)
    assert (cancel.status_code, cancel.json()["error"]) == (409, expired)
    assert read.status_code == 200 and read.json()["status"] == "EXPIRED"
    assert read.json()["finished_at"] == started["expires_at"]
    assert all(no_store(r) for r in (approve, cancel, read))
    assert await count(db_session, "payments") == 0
    # A new transaction can start now.
    assert (await start(client, headers, world.fee, "100000")).status_code == 201


async def test_an_approval_that_can_no_longer_be_applied_fails(client: AsyncClient, db_session: AsyncSession,
                                                               world: World, clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "100000")).json()
    await db_session.execute(text("UPDATE resident_units SET ended_at = UTC_TIMESTAMP(6) WHERE user_id = :u"),
                             {"u": world.laura})

    response = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json=APPROVE)

    assert response.status_code == 409 and no_store(response)
    assert response.json()["error"]["code"] == "DEMO_PAYMENT_NOT_APPLIED"  # the first answer: unchanged
    read = (await client.get(f"{BASE}/{started['id']}")).json()
    assert (read["status"], read["test_instrument"], read["result_reason"]) == ("FAILED", "TEST_CARD_APPROVE",
                                                                              "NOT_APPLIED")
    # The same instrument again: the stored result (200), equal to the reload; another one: 409.
    again = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json=APPROVE)
    other = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json=FUNDS)
    assert (again.status_code, again.json()) == (200, read)
    assert (other.status_code, other.json()["error"]["code"]) == (409, "DEMO_TRANSACTION_FINISHED")
    assert await count(db_session, "payments") == 0


async def test_a_transaction_finished_without_an_instrument_reads_as_it_was(
    client: AsyncClient, db_session: AsyncSession, world: World, clock: Clock
) -> None:
    # As one finished before instruments existed: test_instrument NULL, never an invented reason.
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "100000")).json()
    await complete_demo_payment(db_session, world.laura, started["id"], "REJECTED")  # the old service path

    read = await client.get(f"{BASE}/{started['id']}")
    late = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json=FUNDS)

    assert read.status_code == 200
    assert (read.json()["status"], read.json()["test_instrument"], read.json()["result_reason"]) == (
        "REJECTED", None, None)
    assert (late.status_code, late.json()["error"]["code"]) == (409, "DEMO_TRANSACTION_FINISHED")
    assert (await client.get(f"{BASE}/{started['id']}")).json() == read.json()  # never assigned afterwards


# --- HU-004 and HU-008 with the demo gateway enabled ---------------------------------------------------------


async def test_the_manual_route_still_rejects_the_demo_gateway(client: AsyncClient, db_session: AsyncSession,
                                                               world: World) -> None:
    headers = await as_user(client, db_session, world.admin)
    for method, expected in (("CASH", 201), ("DEMO_GATEWAY", 422)):
        response = await client.post(f"{API}/admin/payments", headers=headers, json={
            "resident_id": world.laura, "amount": "1000", "method": method, "concept": "MONTHLY_FEE",
            "payment_date": "2026-10-08"})
        assert response.status_code == expected, response.text
    assert await rows(db_session, "SELECT source, method FROM payments") == [("MANUAL", "CASH")]


async def test_the_approved_payment_is_in_the_residents_history(client: AsyncClient, db_session: AsyncSession,
                                                                world: World, clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "40000")).json()
    approved = (await client.post(f"{BASE}/{started['id']}/complete", headers=headers,
                                  json=APPROVE)).json()

    listed = (await client.get(f"{API}/resident/payments")).json()
    detail = (await client.get(f"{API}/resident/payments/{approved['payment_id']}")).json()

    [item] = listed["items"]
    assert (item["id"], item["receipt_number"], item["method"], item["status"], item["transaction_reference"],
            item["amount"]) == (approved["payment_id"], approved["receipt_number"], "DEMO_GATEWAY", "APPLIED",
                                started["reference"], "40000.00")
    assert [(a["charge_id"], a["allocated_amount"]) for a in detail["allocations"]] == [(world.fee, "40000.00")]
    pedro = await as_user(client, db_session, world.pedro)
    assert pedro and (await client.get(f"{API}/resident/payments")).json()["items"] == []


async def test_the_approved_payment_is_in_the_administrators_list(client: AsyncClient, db_session: AsyncSession,
                                                                  world: World, clock: Clock) -> None:
    # HU-010 criterion 6.1: the payment comes from the real flow (start, then APPROVED), never inserted with SQL.
    admin = await as_user(client, db_session, world.admin)
    manual = await client.post(f"{API}/admin/payments", headers=admin, json={  # another payment, not the target
        "resident_id": world.pedro, "amount": "1000", "method": "CASH", "concept": "MONTHLY_FEE",
        "payment_date": "2026-10-08"})
    assert manual.status_code == 201, manual.text

    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "40000.50")).json()
    approved = (await client.post(f"{BASE}/{started['id']}/complete", headers=headers,
                                  json=APPROVE)).json()
    assert (approved["status"], approved["amount"]) == ("APPROVED", "40000.50")

    await as_user(client, db_session, world.admin)
    listed = await client.get(f"{API}/admin/payments")

    assert listed.status_code == 200 and no_store(listed)
    matches = [item for item in listed.json()["items"] if item["id"] == approved["payment_id"]]
    assert len(matches) == 1  # exactly once, next to the manual payment
    [item] = matches
    assert item["registered_at"].endswith(("Z", "+00:00"))  # UTC: the controlled clock of the approval
    assert item["registered_at"].replace("Z", "+00:00").startswith(NOW.isoformat())
    assert item == {
        "id": approved["payment_id"],
        "receipt_number": approved["receipt_number"],
        "resident": {"id": world.laura, "full_name": "Laura Gómez"},
        "unit": {"id": world.laura_unit, "label": "B1-TO1-APTO-101"},
        "amount": "40000.50",
        "method": "DEMO_GATEWAY",
        "concept": "MONTHLY_FEE",  # the selected obligation's
        "concept_description": None,
        "payment_date": NOW.date().isoformat(),  # 15:00 in Bogotá, the same day
        "registered_at": item["registered_at"],
        "transaction_reference": started["reference"],
        "notes": None,
        "applied_amount": "40000.50",  # applied to the selected obligation only
        "credit_amount": "0.00",
        "status": "APPLIED",
        "receipt_email_status": "PENDING",  # HU-010 never emails the receipt
    }
    # The administrator's filters keep it: the resident's payments are exactly this one.
    by_resident = (await client.get(f"{API}/admin/payments", params={"resident_id": world.laura})).json()
    assert [(i["id"], i["method"]) for i in by_resident["items"]] == [(approved["payment_id"], "DEMO_GATEWAY")]
    assert by_resident["total"] == 1
    by_name = (await client.get(f"{API}/admin/payments", params={"search": "Laura"})).json()
    assert [i["id"] for i in by_name["items"]] == [approved["payment_id"]]


# --- Publication: only with DEMO_PAYMENTS_ENABLED, and only these operations -----------------------------------


def demo_operations(target: Any) -> set[tuple[str, str]]:
    return {(method, path) for path, operations in target.openapi()["paths"].items() if "demo" in path
            for method in operations}


@pytest.fixture
async def disabled_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """The same API built with the demo gateway disabled, on the test transaction."""
    disabled = create_app(get_settings().model_copy(update={"demo_payments_enabled": False}))

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    disabled.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=disabled), base_url="http://testserver") as http_client:
        yield http_client


async def test_disabled_the_routes_do_not_exist(disabled_client: AsyncClient, db_session: AsyncSession,
                                                world: World, clock: Clock) -> None:
    assert demo_operations(disabled_client._transport.app) == set()  # type: ignore[attr-defined]
    headers = await as_user(disabled_client, db_session, world.laura)
    for method, path, body in requests_for(1, world.fee):
        response = await disabled_client.request(method, path, json=body, headers=headers)
        assert response.status_code == 404, (method, path)
        assert response.json()["error"]["code"] == "NOT_FOUND"
    assert await count(db_session, "demo_payment_transactions") == 0
    # The manual payments of HU-004 are still there and still manual.
    manual = await as_user(disabled_client, db_session, world.admin)
    response = await disabled_client.post(f"{API}/admin/payments", headers=manual, json={
        "resident_id": world.laura, "amount": "1000", "method": "DEMO_GATEWAY", "concept": "MONTHLY_FEE",
        "payment_date": "2026-10-08"})
    assert response.status_code == 422


def test_enabled_exactly_the_six_operations_are_published() -> None:
    enabled = create_app(get_settings().model_copy(update={"demo_payments_enabled": True}))
    assert demo_operations(enabled) == OPERATIONS
    assert demo_operations(app) == OPERATIONS  # the application under test (enabled by conftest)


def test_the_openapi_contract() -> None:
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    paths = {path: operations for path, operations in spec["paths"].items() if "demo" in path}

    def resolve(schema: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in schema:
            schema = schemas[schema["$ref"].removeprefix("#/components/schemas/")]
        return schema

    def body_of(operation: dict[str, Any]) -> dict[str, Any]:
        return resolve(operation["requestBody"]["content"]["application/json"]["schema"])

    start_op = paths[f"{API}/resident/demo-payments"]["post"]
    start_body = body_of(start_op)
    assert set(start_body["properties"]) == {"charge_id", "amount"} and start_body["additionalProperties"] is False
    assert start_body["properties"]["amount"]["anyOf"][1] == {"type": "integer"}  # never "number"
    assert "number" not in str(start_body["properties"]["amount"])

    complete_op = paths[f"{API}/resident/demo-payments/{{transaction_id}}/complete"]["post"]
    complete_body = body_of(complete_op)
    assert set(complete_body["properties"]) == {"instrument_id"} and complete_body["additionalProperties"] is False
    assert complete_body["required"] == ["instrument_id"]
    assert complete_body["properties"]["instrument_id"]["type"] == "string"
    assert "outcome" not in complete_body["properties"] and "outcome" not in complete_body["required"]  # gone

    instruments_op = paths[f"{API}/resident/demo-payments/instruments"]["get"]
    assert "requestBody" not in instruments_op and "parameters" not in instruments_op
    item = resolve(resolve(instruments_op["responses"]["200"]["content"]["application/json"]["schema"])
                   ["properties"]["items"]["items"])
    assert set(item["properties"]) == {"id", "label", "kind", "ending", "expected_result", "description"}
    assert item["properties"]["expected_result"]["enum"] == ["APPROVED", "REJECTED", "FAILED"]

    # GET and cancel take no body and no query parameter: only the transaction id in the path.
    for path, method in ((f"{API}/resident/demo-payments/{{transaction_id}}", "get"),
                         (f"{API}/resident/demo-payments/{{transaction_id}}/cancel", "post")):
        operation = paths[path][method]
        assert "requestBody" not in operation
        assert [(p["name"], p["in"]) for p in operation["parameters"]] == [("transaction_id", "path")]

    answers = [start_op["responses"]["201"], complete_op["responses"]["200"],
               paths[f"{API}/resident/demo-payments/{{transaction_id}}"]["get"]["responses"]["200"],
               paths[f"{API}/resident/demo-payments/{{transaction_id}}/cancel"]["post"]["responses"]["200"]]
    for answer in answers:
        read = resolve(answer["content"]["application/json"]["schema"])
        assert set(read["properties"]) == READ_FIELDS
        assert resolve(read["properties"]["status"])["enum"] == [
            "INITIATED", "APPROVED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED"]
        reason = read["properties"]["result_reason"]["anyOf"]
        assert resolve(reason[0])["enum"] == ["INSUFFICIENT_FUNDS", "TECHNICAL_ERROR", "NOT_APPLIED"]
        assert reason[1] == {"type": "null"}
    # The manual payments keep documenting their three methods (HU-004).
    manual = body_of(spec["paths"][f"{API}/admin/payments"]["post"])
    assert resolve(manual["properties"]["method"])["enum"] == ["CASH", "CARD", "TRANSFER"]
