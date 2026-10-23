"""HU-017: the notifications sent to a resident, and the result of each delivery (CU-017).

One row per event that requires telling the resident something (CU-017 steps 1-3): the record is
added in the same transaction as the event itself, so an event committed always has its
notification and a rolled back one never does. The email is sent after the COMMIT, with no
transaction or lock open, and its result is recorded afterwards (step 6; 3a/4a: the error is
recorded and the event kept for follow-up). The resident reads their own notifications.

- event_key: UNIQUE, built by the service from the event and its record (e.g. "payment:10:registered"):
  a repeated or retried event never creates a second notification.
- title / message: what the resident reads; never a password, token, internal note or another
  person's data.
- email_status: PENDING (not sent yet), SENT, FAILED (the email could not be sent: email_error has
  the type of error, never its message), NO_CONTACT (CU-017 2a: no valid email to send it to) or
  NOT_REQUIRED (the event has no email: an approved demo payment, HU-010; shown in the panel only).
- sent_at: present exactly when SENT.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotificationEvent(StrEnum):
    PAYMENT_REGISTERED = "PAYMENT_REGISTERED"  # HU-017 criteria 2 and 3 (the receipt, HU-004)
    FEEDBACK_RESPONDED = "FEEDBACK_RESPONDED"  # HU-017 criterion 1 (HU-005)
    FEEDBACK_RESOLVED = "FEEDBACK_RESOLVED"  # HU-005: the resolution is also told to the resident


class NotificationEmailStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    NO_CONTACT = "NO_CONTACT"
    NOT_REQUIRED = "NOT_REQUIRED"  # HU-010: demo payments never send email (approved decision)


def _in_list(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(_in_list("event_type", NotificationEvent), name="event_type_valid"),
        CheckConstraint(_in_list("email_status", NotificationEmailStatus), name="email_status_valid"),
        CheckConstraint("(email_status = 'SENT') = (sent_at IS NOT NULL)", name="sent_has_timestamp"),
        CheckConstraint("email_status = 'FAILED' OR email_error IS NULL", name="error_only_when_failed"),
        CheckConstraint("CHAR_LENGTH(TRIM(title)) > 0", name="title_not_blank"),
        # The resident's list, newest first (and the foreign key's index).
        Index("ix_notifications_resident_id_created_at", "resident_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    event_type: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(40))  # "payment" or "feedback": what it is about
    entity_id: Mapped[int] = mapped_column(BigInteger)
    event_key: Mapped[str] = mapped_column(String(120, collation="utf8mb4_0900_bin"), unique=True)
    title: Mapped[str] = mapped_column(String(150))
    message: Mapped[str] = mapped_column(String(500))
    email_status: Mapped[str] = mapped_column(String(20), server_default=text("'PENDING'"))
    email_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))
    sent_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
