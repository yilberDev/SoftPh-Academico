from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditAction(StrEnum):
    ADMIN_CREATED = "ADMIN_CREATED"
    LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGIN_LOCKED = "LOGIN_LOCKED"
    LOGOUT = "LOGOUT"
    # HU-013: physical structure (Bloque → Torre → Apartamento).
    BLOCK_CREATED = "BLOCK_CREATED"
    BLOCK_UPDATED = "BLOCK_UPDATED"
    BLOCK_DELETED = "BLOCK_DELETED"
    TOWER_CREATED = "TOWER_CREATED"
    TOWER_UPDATED = "TOWER_UPDATED"
    TOWER_DELETED = "TOWER_DELETED"
    UNIT_CREATED = "UNIT_CREATED"
    UNIT_UPDATED = "UNIT_UPDATED"
    UNIT_DELETED = "UNIT_DELETED"
    # HU-003: resident invitations.
    RESIDENT_INVITED = "RESIDENT_INVITED"
    INVITATION_EMAIL_SENT = "INVITATION_EMAIL_SENT"
    INVITATION_EMAIL_FAILED = "INVITATION_EMAIL_FAILED"
    INVITATION_RESENT = "INVITATION_RESENT"
    # HU-019: account activation by the resident.
    RESIDENT_ACTIVATED = "RESIDENT_ACTIVATED"
    INVITATION_EXPIRED = "INVITATION_EXPIRED"
    INVITATION_REJECTED = "INVITATION_REJECTED"
    # HU-016: password reset of residents.
    PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED"
    PASSWORD_RESET_EMAIL_FAILED = "PASSWORD_RESET_EMAIL_FAILED"
    PASSWORD_RESET_REJECTED = "PASSWORD_RESET_REJECTED"
    PASSWORD_RESET_COMPLETED = "PASSWORD_RESET_COMPLETED"
    # HU-004: manual payments and their receipts.
    PAYMENT_REGISTERED = "PAYMENT_REGISTERED"
    RECEIPT_EMAIL_SENT = "RECEIPT_EMAIL_SENT"
    RECEIPT_EMAIL_FAILED = "RECEIPT_EMAIL_FAILED"


class AuditLog(Base):
    """Record of relevant actions. Never stores passwords, tokens or secrets."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # NULL when the action is performed by the system or a CLI operator.
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"), index=True
    )
