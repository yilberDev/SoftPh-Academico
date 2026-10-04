"""HU-004: POST/GET /api/v1/admin/payments on the MySQL _test database."""

import re
import smtplib
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import bogota_today, utc_now
from app.core.sessions import CSRF_HEADER_NAME, derive_csrf_token, generate_session_token
from app.models import AuditAction, AuditLog, Charge, Payment, PaymentAllocation, ResidentUnit, Unit, User, UserRole, UserStatus
from app.services import payments as payment_service
from app.services import receipts
from tests.integration.conftest import FakeMailSender, sign_in
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]
URL = "/api/v1/admin/payments"
TODAY = bogota_today()


@pytest.fixture
async def admin(make_user: MakeUser) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
async def community(db_session: AsyncSession, admin: User) -> Community:
    return Community(db_session, admin.id)


@pytest.fixture
async def headers(client: AsyncClient, db_session: AsyncSession, admin: User) -> dict[str, str]:
    return await sign_in(client, db_session, admin)


@pytest.fixture
async def laura(community: Community) -> int:
    return await community.resident("Laura Gómez", "1012345678", (1, 2, 104), email="laura@example.com")


async def unit_of(db: AsyncSession, user_id: int) -> int:
    return int(await db.scalar(select(ResidentUnit.unit_id).where(ResidentUnit.user_id == user_id, ResidentUnit.ended_at.is_(None))) or 0)


async def charge(db: AsyncSession, unit_id: int, amount: str, due: date, concept: str = "MONTHLY_FEE",
                 description: str | None = None, created_at: datetime | None = None, period: date | None = None) -> int:
    # Obligations extension: a monthly fee has a billing period (by default the month of its due date) and
    # every charge has the administrator who created it.
    author = await db.scalar(select(User.id).where(User.role == UserRole.ADMINISTRADOR).limit(1))
    billing_period = (period or due.replace(day=1)) if concept == "MONTHLY_FEE" else None
    row = Charge(unit_id=unit_id, concept=concept, description=description, amount=Decimal(amount), due_date=due,
                 billing_period=billing_period, created_by_user_id=author,
                 **({"created_at": created_at} if created_at else {}))
    db.add(row)
    await db.flush()
    return row.id


async def pay(client: AsyncClient, headers: dict[str, str], resident_id: int, amount: Any = "150000", **fields: Any) -> Response:
    body = {"resident_id": resident_id, "amount": amount, "method": "TRANSFER", "concept": "MONTHLY_FEE",
            "concept_description": None, "payment_date": TODAY.isoformat(), **fields}
    return await client.post(URL, json=body, headers=headers)


async def allocations(db: AsyncSession, payment_id: int) -> list[tuple[int, Decimal]]:
    rows = await db.execute(select(PaymentAllocation.charge_id, PaymentAllocation.amount)
                            .where(PaymentAllocation.payment_id == payment_id).order_by(PaymentAllocation.id))
    return [(row.charge_id, row.amount) for row in rows]


async def audits(db: AsyncSession, action: AuditAction) -> list[AuditLog]:
    return list((await db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))).all())


def pdf_text(pdf: bytes) -> str:
    """Text drawn in the receipt (uncompressed WinAnsi strings with octal escapes)."""
    parts = re.findall(rb"\((.*?)(?<!\\)\) Tj", pdf)
    decode = lambda raw: re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m.group(1), 8)]), raw).replace(b"\\(", b"(").replace(b"\\)", b")")  # noqa: E731
    return "\n".join(decode(part).decode("latin-1") for part in parts)


# --- 1-3. Access ---------------------------------------------------------------------------------------


async def test_administrator_registers_a_payment(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession, mailer: FakeMailSender
) -> None:
    unit_id = await unit_of(db_session, laura)

    response = await pay(client, headers, laura, "150000")

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    payment_id = body["id"]
    assert body == {
        "id": payment_id,
        "receipt_number": f"RC-{payment_id:08d}",
        "resident": {"id": laura, "full_name": "Laura Gómez"},
        "unit": {"id": unit_id, "label": "B1-TO2-APTO-104"},
        "amount": "150000.00",
        "method": "TRANSFER",
        "concept": "MONTHLY_FEE",
        "concept_description": None,
        "payment_date": TODAY.isoformat(),
        "registered_at": body["registered_at"],
        "applied_amount": "0.00",
        "credit_amount": "150000.00",
        "status": "CREDIT",
        "receipt_email_status": "SENT",
        "allocations": [],
        "account": {"pending_balance": "0.00", "credit_balance": "150000.00"},
    }
    assert len(mailer.sent) == 1


async def test_without_session_is_401(client: AsyncClient, origin_headers: dict[str, str], laura: int) -> None:
    response = await pay(client, origin_headers, laura)
    assert response.status_code == 401
    assert (await client.get(URL)).status_code == 401


async def test_resident_is_403(client: AsyncClient, db_session: AsyncSession, laura: int, mailer: FakeMailSender) -> None:
    resident = await db_session.get(User, laura)
    assert resident is not None
    resident_headers = await sign_in(client, db_session, resident)

    assert (await pay(client, resident_headers, laura)).status_code == 403
    assert (await client.get(URL)).status_code == 403
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 0
    assert mailer.sent == []


@pytest.mark.parametrize("csrf", ["missing", "wrong", "other-session"])
async def test_invalid_csrf_is_rejected(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession, csrf: str
) -> None:
    forged = {"Origin": headers["Origin"]}
    if csrf == "wrong":
        forged[CSRF_HEADER_NAME] = "wrong-token"
    elif csrf == "other-session":
        forged[CSRF_HEADER_NAME] = derive_csrf_token(generate_session_token())

    response = await pay(client, forged, laura)

    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_FAILED"
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 0


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://127.0.0.1:9999"])
async def test_invalid_origin_is_rejected(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession, origin: str | None
) -> None:
    forged = {CSRF_HEADER_NAME: headers[CSRF_HEADER_NAME], **({"Origin": origin} if origin else {})}

    response = await pay(client, forged, laura)

    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_FAILED"
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 0


# --- 6-8. Who can receive a payment ------------------------------------------------------------------------


NOT_PAYABLE = [{"field": "resident_id", "message": "Selecciona un residente registrado con una residencia vigente."}]


async def test_unknown_resident_is_rejected(client: AsyncClient, headers: dict[str, str]) -> None:
    response = await pay(client, headers, 999_999)
    assert response.status_code == 422 and response.json()["error"]["details"] == NOT_PAYABLE


@pytest.mark.parametrize(
    "case",
    ["pending-activation", "without-residence", "ended-residence", "deleted", "administrator"],
)
async def test_only_registered_residents_with_a_current_residence(
    client: AsyncClient, headers: dict[str, str], community: Community, admin: User,
    db_session: AsyncSession, mailer: FakeMailSender, case: str,
) -> None:
    match case:
        case "pending-activation":
            user_id = await community.resident("Pendiente", None, (1, 1, 101), status=UserStatus.PENDING_ACTIVATION)
        case "without-residence":
            user_id = await community.resident("Sin Residencia", "10000002", None)
        case "ended-residence":
            user_id = await community.resident("Ya Salió", "10000003", (1, 1, 102), ended=True)
        case "deleted":
            user_id = await community.resident("Eliminada", "10000004", (1, 1, 103), deleted=True)
        case _:
            user_id = admin.id
            await community.residence(admin.id, await community.unit(1, 1, 105))

    response = await pay(client, headers, user_id)

    assert response.status_code == 422 and response.json()["error"]["details"] == NOT_PAYABLE
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 0
    assert mailer.sent == []


async def test_disabled_resident_with_residence_can_receive_payments(
    client: AsyncClient, headers: dict[str, str], community: Community
) -> None:
    user_id = await community.resident("Inactiva", "10000009", (1, 1, 109), status=UserStatus.DISABLED)
    assert (await pay(client, headers, user_id)).status_code == 201


# --- 9-11. The backend resolves the apartment and keeps snapshots -------------------------------------------


async def test_unit_is_resolved_by_the_backend(
    client: AsyncClient, headers: dict[str, str], laura: int, community: Community, db_session: AsyncSession
) -> None:
    other_unit = await community.unit(9, 9, 909)

    forged = await pay(client, headers, laura, unit_id=other_unit)
    assert forged.status_code == 422  # unit_id is not even accepted
    for field, value in (("registered_at", "2020-01-01T00:00:00Z"), ("receipt_number", "RC-1"), ("status", "APPLIED"),
                         ("allocations", []), ("credit_amount", "0"), ("source", "MANUAL")):
        response = await pay(client, headers, laura, **{field: value})
        assert response.status_code == 422, field
        assert response.json()["error"]["details"] == [{"field": field, "message": "Este campo no está permitido."}]

    body = (await pay(client, headers, laura)).json()
    stored = await db_session.get(Payment, body["id"])
    assert stored is not None and stored.unit_id == await unit_of(db_session, laura) != other_unit


async def test_snapshots_keep_the_receipt_as_issued(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession
) -> None:
    body = (await pay(client, headers, laura)).json()
    unit_id = body["unit"]["id"]

    # Later: the resident changes name and phone, and the apartment is renumbered.
    await db_session.execute(update(User).where(User.id == laura).values(full_name="Laura Gómez Ruiz", phone="3000000000"))
    await db_session.execute(update(Unit).where(Unit.id == unit_id).values(number=999))

    stored = await db_session.get(Payment, body["id"], populate_existing=True)
    assert stored is not None
    assert (stored.resident_name_snapshot, stored.unit_label_snapshot) == ("Laura Gómez", "B1-TO2-APTO-104")
    listed = (await client.get(URL)).json()["items"][0]
    assert (listed["resident"]["full_name"], listed["unit"]["label"]) == ("Laura Gómez", "B1-TO2-APTO-104")


# --- 12-21. FIFO, allocations, credit and status -------------------------------------------------------------------


async def test_fifo_example_of_the_design(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession
) -> None:
    unit_id = await unit_of(db_session, laura)
    january = await charge(db_session, unit_id, "100000", date(2026, 1, 5))
    late = await charge(db_session, unit_id, "20000", date(2026, 1, 20), "LATE_FEE")
    february = await charge(db_session, unit_id, "100000", date(2026, 2, 5))

    body = (await pay(client, headers, laura, "150000")).json()

    assert await allocations(db_session, body["id"]) == [
        (january, Decimal("100000.00")), (late, Decimal("20000.00")), (february, Decimal("30000.00"))
    ]
    assert (body["applied_amount"], body["credit_amount"], body["status"]) == ("150000.00", "0.00", "APPLIED")
    assert [(a["charge_id"], a["amount"], a["concept"]) for a in body["allocations"]] == [
        (january, "100000.00", "MONTHLY_FEE"), (late, "20000.00", "LATE_FEE"), (february, "30000.00", "MONTHLY_FEE")
    ]
    assert body["account"] == {"pending_balance": "70000.00", "credit_balance": "0.00"}


async def test_excess_is_kept_as_credit(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession
) -> None:
    unit_id = await unit_of(db_session, laura)
    for amount, due, concept in (("100000", date(2026, 1, 5), "MONTHLY_FEE"), ("20000", date(2026, 1, 20), "LATE_FEE"),
                                 ("100000", date(2026, 2, 5), "MONTHLY_FEE")):
        await charge(db_session, unit_id, amount, due, concept)

    body = (await pay(client, headers, laura, "250000")).json()

    assert (body["applied_amount"], body["credit_amount"], body["status"]) == ("220000.00", "30000.00", "PARTIALLY_APPLIED")
    assert body["account"] == {"pending_balance": "0.00", "credit_balance": "30000.00"}
    assert sum(amount for _, amount in await allocations(db_session, body["id"])) == Decimal("220000.00")


async def test_payment_without_pending_charges_is_credit(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession
) -> None:
    unit_id = await unit_of(db_session, laura)
    covered = await charge(db_session, unit_id, "50000", date(2026, 1, 5))
    await pay(client, headers, laura, "50000")  # covers it completely

    body = (await pay(client, headers, laura, "80000")).json()

    assert (body["status"], body["applied_amount"], body["credit_amount"]) == ("CREDIT", "0.00", "80000.00")
    assert await allocations(db_session, body["id"]) == []
    assert covered  # the covered charge received nothing more
    assert body["account"] == {"pending_balance": "0.00", "credit_balance": "80000.00"}


async def test_several_payments_on_one_charge_never_exceed_it(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession
) -> None:
    unit_id = await unit_of(db_session, laura)
    only = await charge(db_session, unit_id, "100000", date(2026, 1, 5))

    first = (await pay(client, headers, laura, "40000")).json()
    second = (await pay(client, headers, laura, "40000")).json()
    third = (await pay(client, headers, laura, "40000")).json()

    assert [p["status"] for p in (first, second, third)] == ["APPLIED", "APPLIED", "PARTIALLY_APPLIED"]
    assert await allocations(db_session, third["id"]) == [(only, Decimal("20000.00"))]
    total = await db_session.scalar(select(func.sum(PaymentAllocation.amount)).where(PaymentAllocation.charge_id == only))
    assert total == Decimal("100000.00")
    assert third["account"] == {"pending_balance": "0.00", "credit_balance": "20000.00"}


async def test_fifo_ties_use_created_at_then_id(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession
) -> None:
    unit_id = await unit_of(db_session, laura)
    due = date(2026, 3, 5)
    # Same due date: distinct billing periods (one monthly fee per apartment and period).
    later = await charge(db_session, unit_id, "10000", due, created_at=datetime(2026, 3, 1, 12, 0, 1), period=date(2026, 3, 1))
    first_a = await charge(db_session, unit_id, "10000", due, created_at=datetime(2026, 3, 1, 12, 0, 0), period=date(2026, 4, 1))
    first_b = await charge(db_session, unit_id, "10000", due, created_at=datetime(2026, 3, 1, 12, 0, 0), period=date(2026, 5, 1))
    earlier_due = await charge(db_session, unit_id, "10000", date(2026, 2, 5), created_at=datetime(2026, 3, 2))

    body = (await pay(client, headers, laura, "35000")).json()

    assert await allocations(db_session, body["id"]) == [
        (earlier_due, Decimal("10000.00")), (first_a, Decimal("10000.00")),
        (first_b, Decimal("10000.00")), (later, Decimal("5000.00")),
    ]


async def test_charges_of_other_apartments_are_never_touched(
    client: AsyncClient, headers: dict[str, str], laura: int, community: Community, db_session: AsyncSession
) -> None:
    neighbor = await community.resident("Vecino", "10000077", (1, 2, 105))
    foreign = await charge(db_session, await unit_of(db_session, neighbor), "100000", date(2025, 1, 1))

    body = (await pay(client, headers, laura, "50000")).json()

    assert body["status"] == "CREDIT"
    assert await db_session.scalar(select(func.count()).select_from(PaymentAllocation).where(PaymentAllocation.charge_id == foreign)) == 0


async def test_invariant_guard_rolls_back_an_over_allocation(
    db_session: AsyncSession, laura: int, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.schemas.payment import PaymentCreate
    from app.services.allocation import Allocation, AllocationResult
    from app.services.structure import Actor

    unit_id = await unit_of(db_session, laura)
    only = await charge(db_session, unit_id, "10000", date(2026, 1, 5))
    admin_id = admin.id
    # A broken allocator that gives the charge more than it owes.
    monkeypatch.setattr(payment_service, "allocate_fifo", lambda amount, charges: AllocationResult(
        (Allocation(only, Decimal("15000.00")),), Decimal("15000.00"), Decimal("0.00")))
    data = PaymentCreate.model_validate({"resident_id": laura, "amount": "15000", "method": "CASH",
                                         "concept": "MONTHLY_FEE", "payment_date": TODAY.isoformat()})

    with pytest.raises(RuntimeError, match="invariant"):
        await payment_service.register_payment(db_session, data, Actor(admin_id, None), mailer=FakeMailSender())

    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 0
    assert await db_session.scalar(select(func.count()).select_from(PaymentAllocation)) == 0


# --- 22-24. Dates ---------------------------------------------------------------------------------------------


async def test_payment_date_and_registered_at_are_separate(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession
) -> None:
    before = utc_now()
    body = (await pay(client, headers, laura, payment_date=(TODAY - timedelta(days=10)).isoformat())).json()
    after = utc_now()

    stored = await db_session.get(Payment, body["id"])
    assert stored is not None
    assert stored.payment_date == TODAY - timedelta(days=10)
    assert before <= stored.registered_at <= after  # UTC (naive), set by the server
    assert body["registered_at"].endswith("Z") or body["registered_at"].endswith("+00:00")


async def test_future_payment_date_is_rejected(client: AsyncClient, headers: dict[str, str], laura: int) -> None:
    response = await pay(client, headers, laura, payment_date=(TODAY + timedelta(days=1)).isoformat())
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "payment_date", "message": "La fecha del pago no puede ser posterior a hoy."}
    ]


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"amount": None}, "amount"), ({"amount": "0"}, "amount"), ({"amount": "-5"}, "amount"),
        ({"amount": "12.345"}, "amount"), ({"amount": "1e3"}, "amount"), ({"amount": "150.000,00"}, "amount"),
        ({"amount": "99999999999"}, "amount"), ({"method": "CHECK"}, "method"), ({"method": None}, "method"),
        ({"concept": "RENT"}, "concept"), ({"concept": "OTHER"}, "concept_description"),
        ({"concept_description": "algo"}, "concept_description"), ({"payment_date": "02/10/2026"}, "payment_date"),
        ({"payment_date": None}, "payment_date"), ({"resident_id": "5"}, "resident_id"), ({"resident_id": 0}, "resident_id"),
    ],
)
async def test_invalid_fields_point_at_the_field(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession, fields: dict[str, Any], field: str
) -> None:
    fields = dict(fields)
    response = await pay(client, headers, fields.pop("resident_id", laura), **fields)

    assert response.status_code == 422
    assert [d["field"] for d in response.json()["error"]["details"]] == [field]
    assert await db_session.scalar(select(func.count()).select_from(Payment)) == 0


async def test_other_concept_with_description(
    client: AsyncClient, headers: dict[str, str], laura: int, mailer: FakeMailSender
) -> None:
    body = (await pay(client, headers, laura, concept="OTHER", concept_description="  Reparación   de puerta ")).json()
    assert (body["concept"], body["concept_description"]) == ("OTHER", "Reparación de puerta")
    assert "Otro: Reparación de puerta" in pdf_text(mailer.sent[0].attachments[0].data)


# --- 25-30. Receipt, email and audit -------------------------------------------------------------------------------


async def test_receipt_pdf_and_email(
    client: AsyncClient, headers: dict[str, str], laura: int, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    body = (await pay(client, headers, laura, "150000.50", method="CASH",
                      payment_date=date(2026, 10, 1).isoformat() if TODAY >= date(2026, 10, 1) else TODAY.isoformat())).json()

    [email] = mailer.sent
    number = body["receipt_number"]
    assert email.to == "laura@example.com"
    assert email.subject == f"Recibo de pago {number} — SoftPh-Académico"
    assert "150" not in email.subject  # no amounts in the subject
    assert number in email.body
    [attachment] = email.attachments
    assert (attachment.filename, attachment.content_type) == (f"recibo-{number}.pdf", "application/pdf")
    assert attachment.data.startswith(b"%PDF-")
    content = pdf_text(attachment.data)
    for expected in ("SoftPh-Académico", "Urbanización San Joaquín", number, "Laura Gómez", "B1-TO2-APTO-104",
                     "$ 150.000,50", "Efectivo", "Mensualidad", "hora de Colombia"):
        assert expected in content, expected
    stored = await db_session.get(Payment, body["id"])
    assert stored is not None and stored.receipt_email_status == "SENT" and stored.receipt_sent_at is not None


async def test_audit_of_a_registered_payment(
    client: AsyncClient, headers: dict[str, str], laura: int, admin: User, db_session: AsyncSession
) -> None:
    admin_id = admin.id
    body = (await pay(client, headers, laura, "150000")).json()

    [registered] = await audits(db_session, AuditAction.PAYMENT_REGISTERED)
    assert (registered.actor_user_id, registered.entity_type, registered.entity_id) == (admin_id, "payment", body["id"])
    assert registered.context == {
        "payment_id": body["id"], "amount": "150000.00", "method": "TRANSFER", "concept": "MONTHLY_FEE",
        "unit_id": body["unit"]["id"], "receipt_number": body["receipt_number"],
    }
    [sent] = await audits(db_session, AuditAction.RECEIPT_EMAIL_SENT)
    assert sent.context == {"payment_id": body["id"], "receipt_number": body["receipt_number"]}
    assert await audits(db_session, AuditAction.RECEIPT_EMAIL_FAILED) == []


@pytest.mark.parametrize("failure", ["smtp", "pdf"])
async def test_payment_persists_when_the_receipt_fails(
    client: AsyncClient, headers: dict[str, str], laura: int, mailer: FakeMailSender, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str,
) -> None:
    unit_id = await unit_of(db_session, laura)
    await charge(db_session, unit_id, "100000", date(2026, 1, 5))
    if failure == "smtp":
        mailer.fail_with = smtplib.SMTPServerDisconnected("connection lost")
    else:
        def broken(_data: Any) -> bytes:
            raise ValueError("pdf failure")
        monkeypatch.setattr(payment_service, "build_receipt_pdf", broken)

    response = await pay(client, headers, laura, "100000")

    assert response.status_code == 201  # the payment is NOT reported as failed
    body = response.json()
    assert (body["status"], body["receipt_email_status"]) == ("APPLIED", "FAILED")
    stored = await db_session.get(Payment, body["id"], populate_existing=True)
    assert stored is not None and stored.receipt_email_status == "FAILED" and stored.receipt_sent_at is None
    assert len(await allocations(db_session, body["id"])) == 1  # no financial rollback
    [failed] = await audits(db_session, AuditAction.RECEIPT_EMAIL_FAILED)
    assert failed.context == {"payment_id": body["id"], "receipt_number": body["receipt_number"],
                              "error": "SMTPServerDisconnected" if failure == "smtp" else "ValueError"}
    assert len(await audits(db_session, AuditAction.PAYMENT_REGISTERED)) == 1
    assert "Receipt email not sent" in caplog.text


async def test_nothing_sensitive_in_responses_audit_or_logs(
    client: AsyncClient, headers: dict[str, str], laura: int, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    response = await pay(client, headers, laura)
    listed = await client.get(URL)
    audit_dump = str((await db_session.execute(text("SELECT context FROM audit_logs"))).all())

    for leak in ("password_hash", "not-a-real-hash", "token", "laura@example.com", "1012345678"):
        assert leak not in response.text + listed.text, leak
        assert leak not in audit_dump, leak
    assert "laura@example.com" not in caplog.text


# --- 31-38. Summary table -------------------------------------------------------------------------------------------


@pytest.fixture
async def history(client: AsyncClient, headers: dict[str, str], community: Community, db_session: AsyncSession) -> dict[str, int]:
    """Six payments of three residents on different dates, with different statuses."""
    ana = await community.resident("Ana Pérez", "20000001", (1, 1, 101))
    bruno = await community.resident("Bruno Díaz", "20000002", (1, 1, 102))
    carla = await community.resident("Carla Ruiz", "30000003", (1, 1, 103))
    await charge(db_session, await unit_of(db_session, bruno), "50000", date(2026, 1, 5))
    plan = [
        (ana, "100000", "CASH", "MONTHLY_FEE", 30), (bruno, "50000", "TRANSFER", "MONTHLY_FEE", 20),
        (carla, "75000", "CARD", "PENALTY", 10), (ana, "20000", "TRANSFER", "LATE_FEE", 5),
        (bruno, "80000", "CASH", "MONTHLY_FEE", 3), (carla, "10000", "CARD", "LATE_FEE", 1),
    ]
    for resident, amount, method, concept, days_ago in plan:
        response = await pay(client, headers, resident, amount, method=method, concept=concept,
                             payment_date=(TODAY - timedelta(days=days_ago)).isoformat())
        assert response.status_code == 201, response.text
    return {"ana": ana, "bruno": bruno, "carla": carla}


async def page(client: AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get(URL, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return dict(response.json())


def amounts(result: dict[str, Any]) -> list[str]:
    return [item["amount"] for item in result["items"]]


async def test_summary_columns_and_default_order(client: AsyncClient, history: dict[str, int]) -> None:
    result = await page(client)

    assert result["total"] == 6 and result["total_pages"] == 1
    assert amounts(result) == ["10000.00", "80000.00", "20000.00", "75000.00", "50000.00", "100000.00"]  # newest first
    item = result["items"][-1]
    assert set(item) == {"id", "receipt_number", "resident", "unit", "amount", "method", "concept", "concept_description",
                         "payment_date", "registered_at", "applied_amount", "credit_amount", "status", "receipt_email_status"}
    assert (item["resident"]["full_name"], item["concept"], item["method"], item["status"]) == ("Ana Pérez", "MONTHLY_FEE", "CASH", "CREDIT")
    bruno_first = next(i for i in result["items"] if i["amount"] == "50000.00")
    assert bruno_first["status"] == "APPLIED"


async def test_filter_by_resident(client: AsyncClient, history: dict[str, int]) -> None:
    assert amounts(await page(client, resident_id=history["ana"])) == ["20000.00", "100000.00"]
    assert amounts(await page(client, search="bruno")) == ["80000.00", "50000.00"]
    assert amounts(await page(client, search="30000003")) == ["10000.00", "75000.00"]  # document
    assert (await page(client, search="_"))["total"] == 0  # literal, not a wildcard


async def test_filter_by_date(client: AsyncClient, history: dict[str, int]) -> None:
    since = (TODAY - timedelta(days=5)).isoformat()
    until = (TODAY - timedelta(days=3)).isoformat()
    assert amounts(await page(client, date_from=since)) == ["10000.00", "80000.00", "20000.00"]
    assert amounts(await page(client, date_to=until)) == ["80000.00", "20000.00", "75000.00", "50000.00", "100000.00"]
    assert amounts(await page(client, date_from=since, date_to=until)) == ["80000.00", "20000.00"]
    assert amounts(await page(client, date_from=since, date_to=until, resident_id=history["ana"])) == ["20000.00"]


@pytest.mark.parametrize(
    ("sort_by", "ascending"),
    [
        ("date", ["100000.00", "50000.00", "75000.00", "20000.00", "80000.00", "10000.00"]),
        ("amount", ["10000.00", "20000.00", "50000.00", "75000.00", "80000.00", "100000.00"]),
        ("resident", ["100000.00", "20000.00", "50000.00", "80000.00", "75000.00", "10000.00"]),
        # Mensualidad, Mora, Penalización (Spanish order of the labels).
        ("concept", ["100000.00", "50000.00", "80000.00", "20000.00", "10000.00", "75000.00"]),
        # Efectivo, Tarjeta, Transferencia.
        ("method", ["100000.00", "80000.00", "75000.00", "10000.00", "50000.00", "20000.00"]),
        # Aplicado (Bruno's first), then Saldo a favor (the rest).
        ("status", ["50000.00", "100000.00", "75000.00", "20000.00", "80000.00", "10000.00"]),
    ],
)
async def test_sort_asc_and_desc(client: AsyncClient, history: dict[str, int], sort_by: str, ascending: list[str]) -> None:
    assert amounts(await page(client, sort_by=sort_by, sort_order="asc")) == ascending
    descending = amounts(await page(client, sort_by=sort_by, sort_order="desc"))
    if sort_by in ("date", "amount"):
        assert descending == ascending[::-1]
    else:  # equal values keep the id order reversed
        assert sorted(descending) == sorted(ascending) and descending != ascending


async def test_pagination(client: AsyncClient, history: dict[str, int]) -> None:
    pages = [await page(client, page=n, page_size=4, sort_by="amount", sort_order="asc") for n in (1, 2, 3)]
    assert [amounts(p) for p in pages] == [
        ["10000.00", "20000.00", "50000.00", "75000.00"], ["80000.00", "100000.00"], [],  # page 3: beyond the last
    ]
    assert [(p["total"], p["total_pages"]) for p in pages] == [(6, 2)] * 3


async def test_no_payments(client: AsyncClient, headers: dict[str, str]) -> None:
    assert await page(client) == {"items": [], "page": 1, "page_size": 10, "total": 0, "total_pages": 0}


@pytest.mark.parametrize(
    "params",
    [
        {"sort_by": "registered_by_user_id"}, {"sort_by": "amount; DROP TABLE payments"}, {"sort_by": "DATE"},
        {"sort_order": "ascending"}, {"sort_order": "DESC"},
        {"page": 0}, {"page_size": 0}, {"page_size": 101}, {"resident_id": "abc"}, {"resident_id": 0},
        {"date_from": "2026/01/01"}, {"date_to": "ayer"}, {"date_from": "2026-02-01", "date_to": "2026-01-01"},
        {"search": "x" * 101}, {"unit_id": 1}, {"status": "APPLIED"}, {"role": "ADMINISTRADOR"},
    ],
    ids=lambda params: "&".join(f"{k}={str(v)[:16]}" for k, v in params.items()),
)
async def test_invalid_list_query_is_422(client: AsyncClient, headers: dict[str, str], params: dict[str, Any]) -> None:
    response = await client.get(URL, params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
