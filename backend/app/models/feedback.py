"""Feedback of residents (HU-005, created by HU-009).

The original message (subject, message) is immutable. admin_response is what
the resident receives; resolution_note is the administrator's internal note,
required to resolve. Only PENDING → RESOLVED (no reopening). version is the
optimistic lock of every change. The history of actions is audit_logs.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.mysql import DATETIME, INTEGER
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SUBJECT_MAX_LENGTH = 150
TEXT_MAX_LENGTH = 2000  # message, admin_response and resolution_note


class FeedbackStatus(StrEnum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'RESOLVED')", name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("CHAR_LENGTH(TRIM(message)) > 0", name="message_not_blank"),
        CheckConstraint("subject IS NULL OR CHAR_LENGTH(TRIM(subject)) > 0", name="subject_not_blank"),
        CheckConstraint("(status = 'RESOLVED') = (resolved_at IS NOT NULL)", name="resolved_has_timestamp"),
        CheckConstraint("status <> 'RESOLVED' OR resolution_note IS NOT NULL", name="resolved_has_note"),
        CheckConstraint("resolved_at IS NULL OR resolved_at >= created_at", name="resolved_after_creation"),
        Index("ix_feedback_resident_id", "resident_id"),
        Index("ix_feedback_status_created_at", "status", "created_at"),
        Index("ix_feedback_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    subject: Mapped[str | None] = mapped_column(String(SUBJECT_MAX_LENGTH))
    message: Mapped[str] = mapped_column(String(TEXT_MAX_LENGTH))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'PENDING'"))
    admin_response: Mapped[str | None] = mapped_column(String(TEXT_MAX_LENGTH))
    resolution_note: Mapped[str | None] = mapped_column(String(TEXT_MAX_LENGTH))
    version: Mapped[int] = mapped_column(INTEGER(unsigned=True), server_default=text("'1'"))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
