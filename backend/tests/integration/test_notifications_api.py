"""HU-017 (RF-017, CU-017): the notifications of the resident on the MySQL _test database.

The flows that notify (HU-004 payment registered, HU-005 feedback responded / resolved) create the
notification with the event, send the email after the COMMIT and record its result (SENT, FAILED,
NO_CONTACT); a failure never undoes the event. An approved and applied demo payment (HU-010) is
notified in the panel only (NOT_REQUIRED, never an email); any other demo outcome notifies nothing. The resident
reads only their own notifications at GET /api/v1/resident/notifications. No real email is sent:
the FakeMailSender captures them.
"""

import smtplib
from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FieldValidationError
from app.models import AuditAction, AuditLog, Notification, NotificationEvent, Payment, ReceiptEmailStatus, User
from app.schemas.feedback import FeedbackResolution, FeedbackResponse, NotificationStatus
from app.schemas.payment import PaymentCreate
from app.services import feedback as feedback_service
from app.services import notifications as notification_service
from app.services import payments as payment_service
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender, sign_in
from tests.integration.test_feedback_api import new_feedback
from tests.integration.test_payments_api import charge
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

API = "/api/v1/resident/notifications"
ITEM_KEYS = {"id", "event_type", "entity_type", "entity_id", "title", "message", "email_status", "created_at",
             "sent_at"}


@pytest.fixture
async def admin(make_user: Any) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
def actor(admin: User) -> Actor:
    return Actor(user_id=admin.id, ip_address=None)


@pytest.fixture
async def community(db_session: AsyncSession, admin: User) -> Community:
    return Community(db_session, admin.id)


@pytest.fixture
async def laura(community: Community) -> int:
    return await community.resident("Laura Gómez", "1012345678", (1, 1, 101), email="laura@example.com")


@pytest.fixture
async def pedro(community: Community) -> int:
    return await community.resident("Pedro Ruiz", "10000002", (1, 1, 102), email="pedro@example.com")


async def pay(db: AsyncSession, resident_id: int, actor: Actor, mailer: FakeMailSender, amount: str = "150000") -> Any:
    return await payment_service.register_payment(db, PaymentCreate(
        resident_id=resident_id, amount=amount, method="CASH", concept="MONTHLY_FEE", payment_date="2026-10-05",
    ), actor, mailer=mailer)


async def notifications_of(db: AsyncSession, resident_id: int) -> list[Notification]:
    db.expire_all()
    return list((await db.scalars(
        select(Notification).where(Notification.resident_id == resident_id).order_by(Notification.id))).all())


async def total(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count(Notification.id))) or 0)


# --- HU-017 criteria 2 and 3: payment registered, receipt (PDF) emailed -------------------------------------


async def test_a_registered_payment_notifies_its_resident_and_sends_the_pdf_receipt(
    db_session: AsyncSession, laura: int, actor: Actor, mailer: FakeMailSender
) -> None:
    payment = await pay(db_session, laura, actor, mailer)

    assert payment.receipt_email_status == ReceiptEmailStatus.SENT
    [notification] = await notifications_of(db_session, laura)
    assert (notification.event_type, notification.entity_type, notification.entity_id) == (
        NotificationEvent.PAYMENT_REGISTERED, "payment", payment.id)  # criterion 4.1: the event and its record
    assert notification.event_key == f"payment:{payment.id}:registered"
    assert notification.title == "Pago registrado"
    assert notification.message == (
        f"Se registró tu pago por $ 150.000,00 del 5 de octubre de 2026 con el recibo {payment.receipt_number}. "
        "El recibo en PDF se envía a tu correo electrónico registrado.")
    assert (notification.email_status, notification.email_error) == ("SENT", None)
    assert notification.sent_at is not None
    # Criterion 3: the receipt in PDF, to the resident's registered email (the email of HU-004).
    [email] = mailer.sent
    assert email.to == "laura@example.com" and payment.receipt_number in email.subject
    [attachment] = email.attachments
    assert attachment.content_type == "application/pdf" and attachment.data.startswith(b"%PDF-")


async def test_a_failed_email_keeps_the_payment_and_records_the_error(
    db_session: AsyncSession, laura: int, actor: Actor, mailer: FakeMailSender
) -> None:
    mailer.fail_with = smtplib.SMTPServerDisconnected("connection lost")

    payment = await pay(db_session, laura, actor, mailer)

    assert payment.receipt_email_status == ReceiptEmailStatus.FAILED
    assert await db_session.get(Payment, payment.id) is not None  # the payment stays registered
    [notification] = await notifications_of(db_session, laura)
    # CU-017 3a / 4a: the error type is recorded (never its message) and the event kept for follow-up.
    assert (notification.email_status, notification.email_error, notification.sent_at) == (
        "FAILED", "SMTPServerDisconnected", None)


async def test_a_receipt_that_cannot_be_generated_is_a_failed_delivery_with_its_own_error(
    db_session: AsyncSession, laura: int, actor: Actor, mailer: FakeMailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_data: Any) -> bytes:
        raise ValueError("pdf failure")

    monkeypatch.setattr(payment_service, "build_receipt_pdf", broken)

    payment = await pay(db_session, laura, actor, mailer)

    # HU-004 contract: receipt_email_status is the result of sending the receipt (PDF or SMTP failure: FAILED);
    # the cause is the error type, here the PDF's, never confused with an SMTP failure or a missing contact.
    assert mailer.sent == [] and payment.receipt_email_status == ReceiptEmailStatus.FAILED
    [notification] = await notifications_of(db_session, laura)
    assert (notification.email_status, notification.email_error) == ("FAILED", "ValueError")


async def test_without_a_valid_contact_nothing_is_sent_and_it_is_recorded(
    db_session: AsyncSession, laura: int, actor: Actor, mailer: FakeMailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CU-017 2a: an address the email cannot be sent to (the database accepts it: lowercase, ASCII, trimmed).
    await db_session.execute(update(User).where(User.id == laura).values(email="laura-sin-dominio"))
    built: list[Any] = []
    monkeypatch.setattr(payment_service, "build_receipt_pdf", lambda data: built.append(data) or b"%PDF-")

    payment = await pay(db_session, laura, actor, mailer)

    # Nothing to deliver it to: the PDF (never stored) is not even built, and nothing is sent.
    assert built == [] and mailer.sent == [] and payment.receipt_email_status == ReceiptEmailStatus.FAILED
    [notification] = await notifications_of(db_session, laura)
    assert (notification.email_status, notification.email_error) == ("NO_CONTACT", None)
    [audit] = (await db_session.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.RECEIPT_EMAIL_FAILED))).all()
    assert audit.context["error"] == "NoContact"


async def test_an_unrecorded_result_leaves_the_notification_pending_and_the_payment_registered(
    db_session: AsyncSession, laura: int, actor: Actor, mailer: FakeMailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: Any) -> bool:
        raise RuntimeError("database gone")

    monkeypatch.setattr(payment_service, "record_delivery", broken)

    payment = await pay(db_session, laura, actor, mailer)

    # The receipt result and the notification's go together: neither is recorded, the payment stays.
    assert payment.receipt_email_status == ReceiptEmailStatus.PENDING
    assert await db_session.get(Payment, payment.id) is not None
    [notification] = await notifications_of(db_session, laura)
    assert (notification.email_status, notification.sent_at) == ("PENDING", None)


async def test_a_payment_that_is_not_registered_notifies_nothing(
    db_session: AsyncSession, community: Community, actor: Actor, mailer: FakeMailSender
) -> None:
    without_residence = await community.resident("Ana Pérez", "10000003", None, email="ana@example.com")
    with pytest.raises(FieldValidationError):
        await pay(db_session, without_residence, actor, mailer)
    assert await total(db_session) == 0 and mailer.sent == []


# --- HU-017 criterion 1: feedback responded (and resolved) -------------------------------------------------


async def test_a_response_notifies_the_feedback_resident(
    db_session: AsyncSession, laura: int, actor: Actor, mailer: FakeMailSender
) -> None:
    feedback_id = await new_feedback(db_session, laura)

    result = await feedback_service.respond(
        db_session, feedback_id, FeedbackResponse(response="Revisaremos el caso.", version=1), actor, mailer=mailer)

    assert result.notification_status == NotificationStatus.SENT
    [notification] = await notifications_of(db_session, laura)
    assert (notification.event_type, notification.entity_type, notification.entity_id) == (
        NotificationEvent.FEEDBACK_RESPONDED, "feedback", feedback_id)
    assert notification.event_key == f"feedback:{feedback_id}:responded:v2"
    assert notification.message == "La administración revisó tu feedback «Ruido en la torre 2» y te respondió."
    assert notification.email_status == "SENT" and notification.sent_at is not None
    # Criterion 1.2: the email says it was reviewed and includes the response (HU-005).
    [email] = mailer.sent
    assert email.to == "laura@example.com" and "Revisaremos el caso." in email.body


async def test_each_response_and_the_resolution_is_its_own_notification_without_the_internal_note(
    db_session: AsyncSession, laura: int, actor: Actor, mailer: FakeMailSender
) -> None:
    feedback_id = await new_feedback(db_session, laura, subject=None)
    await feedback_service.respond(db_session, feedback_id, FeedbackResponse(response="Primera.", version=1), actor,
                                   mailer=mailer)
    await feedback_service.respond(db_session, feedback_id, FeedbackResponse(response="Segunda.", version=2), actor,
                                   mailer=mailer)
    await feedback_service.resolve(db_session, feedback_id,
                                   FeedbackResolution(resolution_note="Nota interna reservada.", version=3), actor,
                                   mailer=mailer)

    notifications = await notifications_of(db_session, laura)
    assert [(n.event_type, n.event_key) for n in notifications] == [
        (NotificationEvent.FEEDBACK_RESPONDED, f"feedback:{feedback_id}:responded:v2"),
        (NotificationEvent.FEEDBACK_RESPONDED, f"feedback:{feedback_id}:responded:v3"),
        (NotificationEvent.FEEDBACK_RESOLVED, f"feedback:{feedback_id}:resolved:v4"),
    ]
    assert notifications[2].message == "La administración revisó tu feedback «Sin asunto» y lo marcó como resuelto."
    assert all("Nota interna" not in n.message for n in notifications)
    assert all("Nota interna" not in email.body for email in mailer.sent)


async def test_a_failed_feedback_email_keeps_the_response_and_records_the_error(
    db_session: AsyncSession, laura: int, actor: Actor, mailer: FakeMailSender
) -> None:
    feedback_id = await new_feedback(db_session, laura)
    mailer.fail_with = smtplib.SMTPException("refused")

    result = await feedback_service.respond(
        db_session, feedback_id, FeedbackResponse(response="Revisaremos el caso.", version=1), actor, mailer=mailer)

    assert result.notification_status == NotificationStatus.FAILED
    assert result.feedback.admin_response == "Revisaremos el caso."
    [notification] = await notifications_of(db_session, laura)
    assert (notification.email_status, notification.email_error) == ("FAILED", "SMTPException")


async def test_a_feedback_resident_without_a_valid_contact_is_recorded(
    db_session: AsyncSession, laura: int, actor: Actor, mailer: FakeMailSender
) -> None:
    feedback_id = await new_feedback(db_session, laura)
    await db_session.execute(update(User).where(User.id == laura).values(email="laura-sin-dominio"))

    result = await feedback_service.respond(
        db_session, feedback_id, FeedbackResponse(response="Revisaremos el caso.", version=1), actor, mailer=mailer)

    assert result.notification_status == NotificationStatus.FAILED and mailer.sent == []
    [notification] = await notifications_of(db_session, laura)
    assert notification.email_status == "NO_CONTACT"


# --- Duplicates and retries --------------------------------------------------------------------------------


async def test_the_same_event_never_creates_a_second_notification_nor_a_second_result(
    db_session: AsyncSession, laura: int, actor: Actor, mailer: FakeMailSender
) -> None:
    payment = await pay(db_session, laura, actor, mailer)
    notice = notification_service.payment_notice(
        payment_id=payment.id, receipt_number=payment.receipt_number, amount=payment.amount,
        payment_date=date(2026, 10, 5))

    again = await notification_service.add_notification(db_session, laura, notice)
    assert await notification_service.record_delivery(db_session, notice.event_key, "SMTPException") is False
    await db_session.commit()

    [notification] = await notifications_of(db_session, laura)
    assert again.id == notification.id and notification.email_status == "SENT"  # the first result stays


# --- HU-010: an approved and applied demo payment notifies in the panel only, never by email ------------------

DEMO = "/api/v1/resident/demo-payments"


async def demo_setup(client: AsyncClient, db: AsyncSession, resident_id: int, amount: str = "100000") -> tuple[dict[str, str], int]:
    """The resident's session and an obligation of their apartment."""
    resident = await db.get(User, resident_id)
    assert resident is not None
    unit_id = await db.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": resident_id})
    fee = await charge(db, int(unit_id), amount, date(2026, 10, 5))
    return await sign_in(client, db, resident), fee


async def start_demo(client: AsyncClient, headers: dict[str, str], fee: int, amount: str = "100000") -> int:
    started = await client.post(DEMO, headers=headers, json={"charge_id": fee, "amount": amount})
    assert started.status_code == 201, started.text
    return int(started.json()["id"])


async def complete(client: AsyncClient, headers: dict[str, str], transaction: int, instrument: str) -> Any:
    return await client.post(f"{DEMO}/{transaction}/complete", headers=headers, json={"instrument_id": instrument})


async def test_an_approved_demo_payment_is_notified_in_the_panel_without_email(
    client: AsyncClient, db_session: AsyncSession, laura: int, mailer: FakeMailSender
) -> None:
    headers, fee = await demo_setup(client, db_session, laura)
    completed = await complete(client, headers, await start_demo(client, headers, fee), "TEST_CARD_APPROVE")
    assert completed.status_code == 200, completed.text
    payment_id, number = completed.json()["payment_id"], completed.json()["receipt_number"]

    [notification] = await notifications_of(db_session, laura)
    assert (notification.event_type, notification.entity_type, notification.entity_id) == (
        NotificationEvent.PAYMENT_REGISTERED, "payment", payment_id)
    assert notification.event_key == f"payment:{payment_id}:registered"
    # HU-010: no email and no receipt by email; the notification says so and is never "pending".
    assert (notification.email_status, notification.email_error, notification.sent_at) == ("NOT_REQUIRED", None, None)
    assert notification.title == "Pago de demostración registrado"
    assert number in notification.message and "no se envía por correo" in notification.message
    assert mailer.sent == []
    stored = await db_session.get(Payment, payment_id, populate_existing=True)
    assert stored is not None and stored.receipt_email_status == "PENDING"  # HU-010 unchanged: never emailed
    listed = (await client.get(API, headers=headers)).json()["items"]
    assert [(i["event_type"], i["entity_id"], i["email_status"]) for i in listed] == [
        ("PAYMENT_REGISTERED", payment_id, "NOT_REQUIRED")]


@pytest.mark.parametrize("instrument", ["TEST_CARD_INSUFFICIENT_FUNDS", "TEST_ACCOUNT_TECHNICAL_ERROR"])
async def test_a_rejected_or_failed_demo_payment_notifies_nothing(
    client: AsyncClient, db_session: AsyncSession, laura: int, mailer: FakeMailSender, instrument: str
) -> None:
    headers, fee = await demo_setup(client, db_session, laura)
    completed = await complete(client, headers, await start_demo(client, headers, fee), instrument)
    assert completed.status_code == 200 and completed.json()["status"] in {"REJECTED", "FAILED"}, completed.text
    assert completed.json()["payment_id"] is None
    assert await total(db_session) == 0 and mailer.sent == []


async def test_a_cancelled_or_expired_demo_payment_notifies_nothing(
    client: AsyncClient, db_session: AsyncSession, laura: int, mailer: FakeMailSender
) -> None:
    headers, fee = await demo_setup(client, db_session, laura)
    cancelled = await client.post(f"{DEMO}/{await start_demo(client, headers, fee)}/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED", cancelled.text
    expiring = await start_demo(client, headers, fee)
    await db_session.execute(text(
        "UPDATE demo_payment_transactions SET created_at = UTC_TIMESTAMP(6) - INTERVAL 30 MINUTE, "
        "expires_at = UTC_TIMESTAMP(6) - INTERVAL 15 MINUTE WHERE id = :t"), {"t": expiring})
    await db_session.commit()
    expired = await complete(client, headers, expiring, "TEST_CARD_APPROVE")
    assert expired.status_code == 409, expired.text
    assert await total(db_session) == 0 and mailer.sent == []


async def test_an_approval_that_can_no_longer_be_applied_notifies_nothing(
    client: AsyncClient, db_session: AsyncSession, laura: int, actor: Actor, mailer: FakeMailSender
) -> None:
    headers, fee = await demo_setup(client, db_session, laura)
    transaction = await start_demo(client, headers, fee)
    manual = await pay(db_session, laura, actor, mailer, amount="100000")  # the obligation is paid meanwhile

    not_applied = await complete(client, headers, transaction, "TEST_CARD_APPROVE")

    assert not_applied.status_code == 409, not_applied.text
    # Only the manual payment's notification: the demo approval created no payment and no notification.
    assert [(n.event_type, n.entity_id) for n in await notifications_of(db_session, laura)] == [
        (NotificationEvent.PAYMENT_REGISTERED, manual.id)]


# --- The resident reads their own notifications -------------------------------------------------------------


async def test_the_resident_reads_only_their_own_notifications_newest_first(
    client: AsyncClient, db_session: AsyncSession, laura: int, pedro: int, actor: Actor, mailer: FakeMailSender
) -> None:
    first = await pay(db_session, laura, actor, mailer)
    feedback_id = await new_feedback(db_session, laura)
    await feedback_service.respond(db_session, feedback_id, FeedbackResponse(response="Listo.", version=1), actor,
                                   mailer=mailer)
    mailer.fail_with = smtplib.SMTPException("refused")
    await pay(db_session, pedro, actor, mailer)  # another resident's
    resident = await db_session.get(User, laura)
    assert resident is not None
    await sign_in(client, db_session, resident)

    response = await client.get(API)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["page"], body["page_size"], body["total"], body["total_pages"]) == (1, 10, 2, 1)
    assert all(set(item) == ITEM_KEYS for item in body["items"])
    assert [(i["event_type"], i["entity_id"], i["email_status"]) for i in body["items"]] == [
        ("FEEDBACK_RESPONDED", feedback_id, "SENT"), ("PAYMENT_REGISTERED", first.id, "SENT")]
    assert body["items"][0]["created_at"].endswith("Z") or body["items"][0]["created_at"].endswith("+00:00")
    # Never the email, the event key, the error or another person's data.
    for text_ in ("laura@example.com", "event_key", "email_error", "Pedro", "pedro@example.com"):
        assert text_ not in response.text


async def test_pagination_and_only_page_and_page_size_are_accepted(
    client: AsyncClient, db_session: AsyncSession, laura: int, pedro: int, actor: Actor, mailer: FakeMailSender
) -> None:
    for _ in range(3):
        await pay(db_session, laura, actor, mailer, amount="1000")
    resident = await db_session.get(User, laura)
    assert resident is not None
    await sign_in(client, db_session, resident)

    second = (await client.get(f"{API}?page=2&page_size=2")).json()
    assert (second["total"], second["total_pages"], len(second["items"])) == (3, 2, 1)
    beyond = (await client.get(f"{API}?page=5&page_size=2")).json()
    assert beyond["items"] == [] and beyond["total"] == 3
    for query in (f"resident_id={pedro}", f"user_id={pedro}", "page=0", "page_size=101", "page=abc"):
        response = await client.get(f"{API}?{query}")
        assert response.status_code == 422, (query, response.text)
        assert response.headers["cache-control"] == "no-store"


async def test_without_session_is_401_and_the_administrator_is_403(
    client: AsyncClient, db_session: AsyncSession, admin: User
) -> None:
    assert (await client.get(API)).status_code == 401
    await sign_in(client, db_session, admin)
    assert (await client.get(API)).status_code == 403
