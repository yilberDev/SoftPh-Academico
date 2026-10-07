"""SQLAlchemy models.

Every model module is imported here so Alembic autogenerate sees it through
Base.metadata. Each model is added with the HU that needs it.
"""

from app.db.base import Base
from app.models.audit_log import AuditAction, AuditLog
from app.models.feedback import Feedback, FeedbackCategory, FeedbackStatus, FeedbackUrgency
from app.models.finance import (
    Charge,
    FinancialConcept,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    PaymentSource,
    ReceiptEmailStatus,
)
from app.models.invitation import DeliveryStatus, InvitationStatus, ResidentInvitation, ResidentUnit
from app.models.login_throttle import LoginThrottle
from app.models.password_reset import PasswordResetToken
from app.models.session import UserSession
from app.models.structure import Block, Tower, Unit
from app.models.user import User, UserRole, UserStatus

__all__ = [
    "AuditAction",
    "AuditLog",
    "Base",
    "Block",
    "Charge",
    "DeliveryStatus",
    "Feedback",
    "FeedbackCategory",
    "FeedbackStatus",
    "FeedbackUrgency",
    "FinancialConcept",
    "InvitationStatus",
    "LoginThrottle",
    "PasswordResetToken",
    "Payment",
    "PaymentAllocation",
    "PaymentMethod",
    "PaymentSource",
    "ReceiptEmailStatus",
    "ResidentInvitation",
    "ResidentUnit",
    "Tower",
    "Unit",
    "User",
    "UserRole",
    "UserSession",
    "UserStatus",
]
