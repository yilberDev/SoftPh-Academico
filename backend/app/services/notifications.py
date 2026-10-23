"""HU-017 (RF-017, CU-017): the notifications sent to a resident, and the result of each delivery.

The events that notify (CU-017 step 1) are those of the official fichas, in their own flows:

    payment registered (HU-004, criteria 2 and 3)   payments._register adds the notification in the
                                                    payment's transaction; the receipt (PDF) email is
                                                    sent after the COMMIT and _record_receipt records
                                                    its result here too.
    demo payment approved and applied (HU-010,      demo_payments._approve adds it in the approval's
    criterion 2 only)                               transaction, NOT_REQUIRED: no email, no receipt by
                                                    email (approved HU-010 decision); the resident
                                                    downloads the demo receipt from the payment.
    feedback responded / resolved (HU-005,          feedback.respond / resolve add it in the change's
    criterion 1)                                    transaction; feedback._notify records the result.

    add_notification   CU-017 2-3: who (the record's own resident, never a value from the client),
                       which event (event_type + the record it is about) and the content. Flushes in
                       the caller's transaction (no COMMIT): an event committed always has its
                       notification and a rolled back one never does. Idempotent by event_key.
    record_delivery    CU-017 6 / 3a / 4a: SENT, FAILED (the error type, never its message) or
                       NO_CONTACT (2a), in the caller's transaction. Only a PENDING notification is
                       updated: a result is recorded once.
    list_resident_notifications   the resident's own notifications, newest first (HU-017: receives).

A demo transaction rejected, failed, cancelled, expired or approved but no longer applicable creates
no payment, so it notifies nothing.
The content never carries a password, token, internal note (resolution_note) or another person's data.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.money import format_cop
from app.models import Notification, NotificationEmailStatus, NotificationEvent
from app.services.invitation_email import format_long_date

PAYMENT_ENTITY = "payment"
FEEDBACK_ENTITY = "feedback"
NO_CONTACT_ERROR = "NoContact"  # CU-017 2a, as the error of the existing receipt/feedback records
NO_SUBJECT = "Sin asunto"


def has_contact(email: str | None) -> bool:
    """CU-017 2a: a contact address the email can be sent to (format only, no DNS lookup)."""
    if not email:
        return False
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        return False
    return True


def payment_event_key(payment_id: int) -> str:
    return f"{PAYMENT_ENTITY}:{payment_id}:registered"


def feedback_event_key(feedback_id: int, event: NotificationEvent, version: int) -> str:
    # Every response (it may be replaced while PENDING) and the resolution is its own event.
    step = "resolved" if event == NotificationEvent.FEEDBACK_RESOLVED else "responded"
    return f"{FEEDBACK_ENTITY}:{feedback_id}:{step}:v{version}"


@dataclass(frozen=True)
class Notice:
    """What the resident reads (title ≤ 150, message ≤ 500 characters)."""

    event: NotificationEvent
    entity_type: str
    entity_id: int
    event_key: str
    title: str
    message: str
    email: bool = True  # False: the event has no email (NOT_REQUIRED)


def payment_notice(*, payment_id: int, receipt_number: str, amount: Decimal, payment_date: date) -> Notice:
    return Notice(
        event=NotificationEvent.PAYMENT_REGISTERED,
        entity_type=PAYMENT_ENTITY,
        entity_id=payment_id,
        event_key=payment_event_key(payment_id),
        title="Pago registrado",
        message=(
            f"Se registró tu pago por {format_cop(amount)} del {format_long_date(payment_date)} "
            f"con el recibo {receipt_number}. El recibo en PDF se envía a tu correo electrónico registrado."
        ),
    )


def demo_payment_notice(*, payment_id: int, receipt_number: str, amount: Decimal, payment_date: date) -> Notice:
    """HU-010: an approved demo payment, applied. No email is sent (approved decision)."""
    return Notice(
        event=NotificationEvent.PAYMENT_REGISTERED,
        entity_type=PAYMENT_ENTITY,
        entity_id=payment_id,
        event_key=payment_event_key(payment_id),
        title="Pago de demostración registrado",
        message=(
            f"Se registró tu pago de demostración por {format_cop(amount)} del {format_long_date(payment_date)} "
            f"con el comprobante {receipt_number}. Es una simulación sin dinero real y no se envía por correo: "
            "descarga el comprobante desde el detalle del pago."
        ),
        email=False,
    )


def feedback_notice(*, feedback_id: int, event: NotificationEvent, version: int, subject: str | None) -> Notice:
    # The subject (≤ 150 characters) identifies the feedback; the response is read in its detail and
    # sent in the email. The resolution note is internal and never appears here.
    shown = f"«{subject}»" if subject else f"«{NO_SUBJECT}»"
    if event == NotificationEvent.FEEDBACK_RESPONDED:
        title, message = "Respuesta a tu feedback", f"La administración revisó tu feedback {shown} y te respondió."
    elif event == NotificationEvent.FEEDBACK_RESOLVED:
        title, message = "Feedback resuelto", f"La administración revisó tu feedback {shown} y lo marcó como resuelto."
    else:
        raise ValueError("Not a feedback event.")
    return Notice(
        event=event,
        entity_type=FEEDBACK_ENTITY,
        entity_id=feedback_id,
        event_key=feedback_event_key(feedback_id, event, version),
        title=title,
        message=message,
    )


async def add_notification(db: AsyncSession, resident_id: int, notice: Notice) -> Notification:
    """The notification of one event, in the caller's transaction (flush, no COMMIT).

    The same event again (same event_key) returns the existing notification: never a duplicate.
    """
    existing = await db.scalar(select(Notification).where(Notification.event_key == notice.event_key))
    if existing is not None:
        return existing
    notification = Notification(
        resident_id=resident_id,
        event_type=notice.event,
        entity_type=notice.entity_type,
        entity_id=notice.entity_id,
        event_key=notice.event_key,
        title=notice.title,
        message=notice.message,
        email_status=NotificationEmailStatus.PENDING if notice.email else NotificationEmailStatus.NOT_REQUIRED,
    )
    db.add(notification)
    await db.flush()
    return notification


def delivery_status(error: str | None) -> NotificationEmailStatus:
    if error is None:
        return NotificationEmailStatus.SENT
    return NotificationEmailStatus.NO_CONTACT if error == NO_CONTACT_ERROR else NotificationEmailStatus.FAILED


async def record_delivery(db: AsyncSession, event_key: str, error: str | None) -> bool:
    """The result of the email (CU-017 6 / 3a / 4a), in the caller's transaction. False: nothing PENDING."""
    status = delivery_status(error)
    result = await db.execute(
        update(Notification)
        .where(Notification.event_key == event_key, Notification.email_status == NotificationEmailStatus.PENDING)
        .values(
            email_status=status,
            email_error=error if status == NotificationEmailStatus.FAILED else None,
            sent_at=utc_now() if status == NotificationEmailStatus.SENT else None,
        )
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


# --- The resident's notifications (read only) ---------------------------------------------------------


@dataclass(frozen=True)
class NotificationItem:
    id: int
    event_type: NotificationEvent
    entity_type: str
    entity_id: int
    title: str
    message: str
    email_status: NotificationEmailStatus
    created_at: datetime  # UTC (naive, as stored)
    sent_at: datetime | None


@dataclass(frozen=True)
class NotificationPage:
    items: tuple[NotificationItem, ...]
    page: int
    page_size: int
    total: int
    total_pages: int


async def list_resident_notifications(
    db: AsyncSession, resident_id: int, *, page: int, page_size: int
) -> NotificationPage:
    """The resident's own notifications, newest first (created_at, id); one COUNT and one page."""
    mine = Notification.resident_id == resident_id
    total = int(await db.scalar(select(func.count(Notification.id)).where(mine)) or 0)
    total_pages = math.ceil(total / page_size)
    items: tuple[NotificationItem, ...] = ()
    if page <= total_pages:
        rows = (
            await db.scalars(
                select(Notification)
                .where(mine)
                .order_by(Notification.created_at.desc(), Notification.id.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).all()
        items = tuple(
            NotificationItem(
                id=row.id,
                event_type=NotificationEvent(row.event_type),
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                title=row.title,
                message=row.message,
                email_status=NotificationEmailStatus(row.email_status),
                created_at=row.created_at,
                sent_at=row.sent_at,
            )
            for row in rows
        )
    return NotificationPage(items=items, page=page, page_size=page_size, total=total, total_pages=total_pages)
