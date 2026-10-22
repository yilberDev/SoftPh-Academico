"""HU-010: GET /resident/payments/{payment_id}/receipt, the PDF receipt of an approved demo payment.

Over real HTTP against MySQL (the test transaction). The receipt is composed only from the persisted
payment, its demo transaction and its single allocation, which must agree; it is downloaded by the
session's resident only, for a DEMO_GATEWAY payment only, and the download writes nothing: no
payment, allocation, audit entry, email or receipt_email_status change.
"""

import logging
import re
from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from tests.integration.conftest import FakeMailSender
from tests.integration.test_demo_payments_api import (
    BASE,
    NO_STORE,
    as_user,
    disabled_client,
    start,
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

API = "/api/v1"
LEGEND = ("Comprobante de una transacción de demostración académica. "
          "No corresponde a un cobro real ni a una entidad o pasarela financiera.")
NOT_FOUND = {"error": {"code": "PAYMENT_NOT_FOUND", "message": "El pago no existe.", "details": []}}
INTERNAL = {"error": {"code": "INTERNAL_ERROR", "message": "Ocurrió un error interno. Intenta de nuevo más tarde.",
                      "details": []}}


def receipt_url(payment_id: Any) -> str:
    return f"{API}/resident/payments/{payment_id}/receipt"


def pdf_text(pdf: bytes) -> str:
    """The text drawn in the PDF (uncompressed WinAnsi strings with octal escapes), lines joined by spaces."""
    parts = re.findall(rb"\((.*?)(?<!\\)\) Tj", pdf)
    return " ".join(re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m.group(1), 8)]), p).replace(b"\\(", b"(")
                    .replace(b"\\)", b")").decode("cp1252") for p in parts)


async def approve(client: AsyncClient, headers: dict[str, str], charge_id: int, amount: str) -> dict[str, Any]:
    started = await start(client, headers, charge_id, amount)
    assert started.status_code == 201, started.text
    approved = await client.post(f"{BASE}/{started.json()['id']}/complete", headers=headers, json={"instrument_id": "TEST_CARD_APPROVE"})
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED", approved.text
    return dict(approved.json())


async def everything(db: AsyncSession) -> list[Any]:
    """Every row a download must leave alone."""
    return [
        *await financial_state(db),
        await rows(db, "SELECT id, status, payment_id, reference FROM demo_payment_transactions ORDER BY id"),
        await rows(db, "SELECT id, receipt_email_status, receipt_sent_at, transaction_reference FROM payments ORDER BY id"),
        await rows(db, "SELECT id, action FROM audit_logs ORDER BY id"),
    ]


def is_json_error(response: Response) -> bool:
    return (response.headers["content-type"].startswith("application/json")
            and response.headers.get("cache-control") == NO_STORE and not response.content.startswith(b"%PDF"))


# --- The receipt ------------------------------------------------------------------------------------------------


async def test_the_resident_downloads_the_receipt_of_an_approved_demo_payment(
    client: AsyncClient, db_session: AsyncSession, world: World, clock: Clock, mailer: FakeMailSender
) -> None:
    headers = await as_user(client, db_session, world.laura)
    approved = await approve(client, headers, world.fee, "40000.50")
    number = approved["receipt_number"]

    response = await client.get(receipt_url(approved["payment_id"]))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == f'attachment; filename="comprobante-demo-{number}.pdf"'
    assert re.fullmatch(r"RC-\d{8}", number)
    assert response.headers["cache-control"] == NO_STORE
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content.startswith(b"%PDF-") and response.content.rstrip().endswith(b"%%EOF")

    # Exactly what the database holds for this payment, its transaction and its allocation.
    [(name, unit, reference, amount)] = await rows(
        db_session, "SELECT resident_name_snapshot, unit_label_snapshot, transaction_reference, amount FROM payments "
                    "WHERE id = :p", p=approved["payment_id"])
    [(allocated, charge_id)] = await rows(db_session, "SELECT amount, charge_id FROM payment_allocations "
                                                      "WHERE payment_id = :p", p=approved["payment_id"])
    assert (reference, str(amount), str(allocated), charge_id) == (approved["reference"], "40000.50", "40000.50", world.fee)
    content = pdf_text(response.content)
    for expected in (
        "Comprobante de pago (demostración)", number, name, unit, "Mensualidad", "Octubre de 2026",
        "5 de octubre de 2026", "Pasarela de demostración", "Aprobado — aplicado a la obligación", reference,
        "9 de octubre de 2026", "3:00 p. m.",  # payment date and registration (20:00 UTC, Bogotá)
    ):
        assert expected in content, expected
    assert content.count("$ 40.000,50") == 2  # the amount and the applied amount
    assert content.count(LEGEND) == 2  # in the body and in the footer
    assert mailer.sent == []  # never emailed


async def test_downloading_writes_nothing(client: AsyncClient, db_session: AsyncSession, world: World, clock: Clock,
                                          mailer: FakeMailSender) -> None:
    headers = await as_user(client, db_session, world.laura)
    approved = await approve(client, headers, world.fee, "100000")
    before = await everything(db_session)
    assert await rows(db_session, "SELECT receipt_email_status, receipt_sent_at FROM payments") == [("PENDING", None)]

    first = await client.get(receipt_url(approved["payment_id"]))
    second = await client.get(receipt_url(approved["payment_id"]))

    assert first.status_code == second.status_code == 200
    assert first.content == second.content  # the same persisted data, the same document
    assert await everything(db_session) == before  # no payment, allocation, audit entry or status change
    assert await rows(db_session, "SELECT receipt_email_status, receipt_sent_at FROM payments") == [("PENDING", None)]
    assert (await count(db_session, "payments"), await count(db_session, "payment_allocations")) == (1, 1)
    assert mailer.sent == []


# --- Who can download it --------------------------------------------------------------------------------------


async def test_without_a_session_it_is_401(client: AsyncClient, db_session: AsyncSession, world: World,
                                           clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)
    approved = await approve(client, headers, world.fee, "1000")
    client.cookies.clear()
    response = await client.get(receipt_url(approved["payment_id"]))
    assert response.status_code == 401 and response.json()["error"]["code"] == "NOT_AUTHENTICATED"
    assert is_json_error(response)


async def test_the_administrator_is_forbidden(client: AsyncClient, db_session: AsyncSession, world: World,
                                              clock: Clock) -> None:
    headers = await as_user(client, db_session, world.laura)
    approved = await approve(client, headers, world.fee, "1000")
    await as_user(client, db_session, world.admin)
    response = await client.get(receipt_url(approved["payment_id"]))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
    assert is_json_error(response)


async def test_another_residents_missing_or_manual_payment_is_not_found(
    client: AsyncClient, db_session: AsyncSession, world: World, clock: Clock
) -> None:
    pedro = await as_user(client, db_session, world.pedro)
    pedro_fee = await charge(db_session, world.pedro_unit, "90000", date(2026, 10, 5))
    pedros = await approve(client, pedro, pedro_fee, "1000")  # Pedro's approved demo payment
    manual = await manual_payment(db_session, world, "30000")  # Laura's own manual payment (HU-004)
    await as_user(client, db_session, world.laura)

    answers = [await client.get(receipt_url(payment_id))
               for payment_id in (pedros["payment_id"], 999_999_999, manual.id)]

    # Nothing tells them apart: same status, same body, no PDF.
    assert [(r.status_code, r.json()) for r in answers] == [(404, NOT_FOUND)] * 3
    assert all(is_json_error(r) for r in answers)
    # Laura's manual payment is still in her history (HU-008), only its receipt is not offered here.
    assert (await client.get(f"{API}/resident/payments/{manual.id}")).status_code == 200


@pytest.mark.parametrize("path", ["0", "-1", "abc", str(2**64), "1.5"])
async def test_invalid_payment_ids_are_rejected(client: AsyncClient, db_session: AsyncSession, world: World,
                                                path: str) -> None:
    await as_user(client, db_session, world.laura)
    response = await client.get(receipt_url(path))
    assert response.status_code == 422 and is_json_error(response)


@pytest.mark.parametrize("query", ["resident_id=1", "user_id=1", "payment_id=1", "format=html", "download=0"])
async def test_unknown_query_parameters_are_rejected(client: AsyncClient, db_session: AsyncSession, world: World,
                                                     clock: Clock, query: str) -> None:
    # As HU-008: the route takes no query parameter (resident_id never selects anything).
    headers = await as_user(client, db_session, world.laura)
    approved = await approve(client, headers, world.fee, "1000")
    response = await client.get(f"{receipt_url(approved['payment_id'])}?{query}")
    assert response.status_code == 422 and is_json_error(response)
    assert response.json()["error"]["details"] == [
        {"field": f"query.{query.split('=')[0]}", "message": "Este campo no está permitido."}]


# --- Only an approved demo payment has a receipt --------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["REJECTED", "FAILED", "CANCELLED", "EXPIRED"])
async def test_a_transaction_that_was_not_approved_has_no_receipt(
    client: AsyncClient, db_session: AsyncSession, world: World, clock: Clock, outcome: str
) -> None:
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "1000")).json()
    if outcome == "CANCELLED":
        await client.post(f"{BASE}/{started['id']}/cancel", headers=headers)
    elif outcome == "EXPIRED":
        clock.now = NOW + TTL
        await client.get(f"{BASE}/{started['id']}")
    else:
        instrument = {"REJECTED": "TEST_CARD_INSUFFICIENT_FUNDS", "FAILED": "TEST_ACCOUNT_TECHNICAL_ERROR"}[outcome]  # the test instrument that simulates it
        await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json={"instrument_id": instrument})
    assert await rows(db_session, "SELECT status, payment_id FROM demo_payment_transactions") == [(outcome, None)]

    # No payment exists, so no receipt can be asked for: neither the transaction id nor a guessed one.
    assert await count(db_session, "payments") == 0
    for payment_id in (started["id"], 1, 999_999_999):
        response = await client.get(receipt_url(payment_id))
        assert (response.status_code, response.json()) == (404, NOT_FOUND)


async def test_an_approval_that_could_not_be_applied_has_no_receipt(
    client: AsyncClient, db_session: AsyncSession, world: World, clock: Clock
) -> None:
    headers = await as_user(client, db_session, world.laura)
    started = (await start(client, headers, world.fee, "1000")).json()
    await db_session.execute(text("UPDATE resident_units SET ended_at = UTC_TIMESTAMP(6) WHERE user_id = :u"),
                             {"u": world.laura})
    response = await client.post(f"{BASE}/{started['id']}/complete", headers=headers, json={"instrument_id": "TEST_CARD_APPROVE"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "DEMO_PAYMENT_NOT_APPLIED"
    assert await count(db_session, "payments") == 0
    assert (await client.get(receipt_url(1))).status_code == 404


# --- Inconsistent records: a controlled 500 and no PDF ---------------------------------------------------------


BREAKAGES = {
    "reference": "UPDATE demo_payment_transactions SET reference = 'DEMO-FFFFFFFFFFFFFFFF' WHERE payment_id = :p",
    "payment-reference": "UPDATE payments SET transaction_reference = NULL WHERE id = :p",
    "allocation_amount": "UPDATE payment_allocations SET amount = amount - 0.01 WHERE payment_id = :p",
    "allocation_count": "INSERT INTO payment_allocations (payment_id, charge_id, amount) VALUES (:p, :other, 1)",
    "transaction_count": "UPDATE demo_payment_transactions SET status = 'FAILED', payment_id = NULL WHERE payment_id = :p",
    "allocation_charge": "UPDATE demo_payment_transactions SET charge_id = :other WHERE payment_id = :p",
}
CHECKS = {"payment-reference": "reference"}  # the check that catches each breakage, when its name differs


@pytest.mark.parametrize("breakage", list(BREAKAGES))
async def test_inconsistent_records_are_a_controlled_error_without_a_pdf(
    client: AsyncClient, db_session: AsyncSession, world: World, clock: Clock, caplog: pytest.LogCaptureFixture,
    breakage: str,
) -> None:
    headers = await as_user(client, db_session, world.laura)
    other = await charge(db_session, world.laura_unit, "5000", date(2026, 11, 5))
    approved = await approve(client, headers, world.fee, "40000")
    await db_session.execute(text(BREAKAGES[breakage]), {"p": approved["payment_id"], "other": other})
    before = await everything(db_session)

    with caplog.at_level(logging.ERROR, logger="app.services.resident_payments"):
        response = await client.get(receipt_url(approved["payment_id"]))

    assert response.status_code == 500 and response.json() == INTERNAL  # nothing internal reaches the client
    assert is_json_error(response) and "content-disposition" not in response.headers
    [record] = [r for r in caplog.records if r.name == "app.services.resident_payments"]
    assert f"payment_id={approved['payment_id']}" in record.getMessage()
    assert f"check={CHECKS.get(breakage, breakage)}" in record.getMessage()
    assert "Laura" not in record.getMessage() and approved["reference"] not in record.getMessage()
    assert await everything(db_session) == before  # and nothing was repaired or written


# --- Independent of the simulator's flag -----------------------------------------------------------------------


async def test_the_receipt_is_downloaded_with_the_simulator_disabled(
    client: AsyncClient, disabled_client: AsyncClient, db_session: AsyncSession, world: World, clock: Clock
) -> None:
    headers = await as_user(client, db_session, world.laura)
    approved = await approve(client, headers, world.fee, "40000")  # approved while it was enabled
    await as_user(disabled_client, db_session, world.laura)

    response = await disabled_client.get(receipt_url(approved["payment_id"]))
    simulator = await disabled_client.get(f"{BASE}/{approved['id']}")

    assert response.status_code == 200 and response.content.startswith(b"%PDF-")
    assert response.headers["cache-control"] == NO_STORE
    assert simulator.status_code == 404  # the simulator itself stays unpublished
    paths = disabled_client._transport.app.openapi()["paths"]  # type: ignore[attr-defined]
    assert f"{API}/resident/payments/{{payment_id}}/receipt" in paths and not [p for p in paths if "demo" in p]


def test_the_openapi_contract() -> None:
    operation = app.openapi()["paths"][f"{API}/resident/payments/{{payment_id}}/receipt"]
    assert set(operation) == {"get"}
    get = operation["get"]
    assert [(p["name"], p["in"]) for p in get["parameters"]] == [("payment_id", "path")]
    assert "application/pdf" in get["responses"]["200"]["content"]
    assert "requestBody" not in get


async def test_a_receipt_of_another_period_and_a_partial_payment(
    client: AsyncClient, db_session: AsyncSession, world: World, clock: Clock
) -> None:
    # A penalty (no period) paid in part: the receipt shows its own obligation and the applied amount.
    penalty = await charge(db_session, world.laura_unit, "50000", date(2026, 10, 20), "PENALTY", "Ruido nocturno")
    headers = await as_user(client, db_session, world.laura)
    approved = await approve(client, headers, penalty, "12345.67")
    content = pdf_text((await client.get(receipt_url(approved["payment_id"]))).content)
    assert "Penalización: Ruido nocturno" in content and "20 de octubre de 2026" in content
    assert "Periodo" not in content and content.count("$ 12.345,67") == 2
    assert "$ 50.000,00" not in content  # never the obligation's amount as if it were paid
