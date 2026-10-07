"""Financial model (HU-004): charges, payments and their allocations.

charges, payments and payment_allocations are the source of truth. Balances
are never stored: an apartment's pending balance is its charges minus what was
allocated to them, and its credit (saldo a favor) is its payments minus what
was allocated from them. Money is DECIMAL(12,2) and Python Decimal, never float.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import DATETIME, TINYINT
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.naming import conv

from app.db.base import Base

MONEY = Numeric(12, 2)


class FinancialConcept(StrEnum):
    """Concept of a charge or a payment (HU-004 criterion 3.1)."""

    MONTHLY_FEE = "MONTHLY_FEE"
    LATE_FEE = "LATE_FEE"
    PENALTY = "PENALTY"
    OTHER = "OTHER"


class PaymentMethod(StrEnum):
    CASH = "CASH"
    CARD = "CARD"
    TRANSFER = "TRANSFER"


TRANSACTION_REFERENCE_MAX_LENGTH = 100
PAYMENT_NOTES_MAX_LENGTH = 500


class PaymentSource(StrEnum):
    # The electronic payment simulated internally (HU-010) will add its own value.
    MANUAL = "MANUAL"


class ReceiptEmailStatus(StrEnum):
    # Result of sending the receipt (E-1: recorded after the payment is committed).
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


def _in_list(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class Charge(Base):
    """An obligation of an apartment (never of a user).

    Its status (paid, partial, pending, overdue), applied amount and balance are
    never stored: they are derived from payment_allocations.

    - billing_period: first day of the month; only (and always) MONTHLY_FEE,
      one per apartment and period.
    - source_charge_id: the obligation that originated a LATE_FEE (optional:
      historical late fees have none); at most one late fee per origin. The
      composite foreign key with the generated markers keeps the origin in the
      same apartment and never a LATE_FEE.
    - created_by_user_id: the administrator, taken from the session.
    """

    __tablename__ = "charges"
    __table_args__ = (
        CheckConstraint(_in_list("concept", FinancialConcept), name="concept_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("concept NOT IN ('PENALTY', 'OTHER') OR description IS NOT NULL", name="description_required"),
        CheckConstraint("concept <> 'MONTHLY_FEE' OR description IS NULL", name="monthly_fee_without_description"),
        CheckConstraint("(concept = 'MONTHLY_FEE') = (billing_period IS NOT NULL)", name="period_only_for_monthly_fee"),
        CheckConstraint("billing_period IS NULL OR DAYOFMONTH(billing_period) = 1", name="period_first_day"),
        CheckConstraint("source_charge_id IS NULL OR concept = 'LATE_FEE'", name="source_only_for_late_fee"),
        # One monthly fee per apartment and period (NULL for the other concepts never collides).
        UniqueConstraint("unit_id", "billing_period", name=conv("uq_charges_unit_id_billing_period")),
        # At most one late fee per origin (historical late fees are NULL).
        UniqueConstraint("source_charge_id", name=conv("uq_charges_source_charge_id")),
        # Parent key of the origin foreign key.
        UniqueConstraint("unit_id", "id", "can_originate_late_fee", name=conv("uq_charges_unit_id_id_can_originate_late_fee")),
        ForeignKeyConstraint(
            ["unit_id", "source_charge_id", "late_fee_source_link"],
            ["charges.unit_id", "charges.id", "charges.can_originate_late_fee"],
            name=conv("fk_charges_source_charge_id_charges"),
            ondelete="RESTRICT",
        ),
        # FIFO order within an apartment (id is implicit in the index) and the FK.
        Index("ix_charges_unit_id_due_date_created_at", "unit_id", "due_date", "created_at"),
        Index("ix_charges_unit_id_source_charge_id", "unit_id", "source_charge_id", "late_fee_source_link"),
        Index("ix_charges_created_by_user_id", "created_by_user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    unit_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("units.id", ondelete="RESTRICT"))
    concept: Mapped[str] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    due_date: Mapped[date] = mapped_column(Date)
    billing_period: Mapped[date | None] = mapped_column(Date)
    source_charge_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))
    # Generated markers of the origin foreign key (never written by the application).
    can_originate_late_fee: Mapped[int] = mapped_column(
        TINYINT, Computed("CASE WHEN concept <> 'LATE_FEE' THEN 1 ELSE 0 END", persisted=True)
    )
    late_fee_source_link: Mapped[int | None] = mapped_column(
        TINYINT, Computed("CASE WHEN source_charge_id IS NOT NULL THEN 1 END", persisted=True)
    )


class Payment(Base):
    """A payment registered by the administrator.

    The resident and the apartment are resolved by the backend. The snapshots
    keep the receipt as it was issued, even if the resident or the apartment
    changes later. The visible receipt number is derived from id (RC-00000001).
    The payment status (applied, partially applied, credit) is derived from its
    allocations and never stored.
    """

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(_in_list("method", PaymentMethod), name="method_valid"),
        CheckConstraint(_in_list("concept", FinancialConcept), name="concept_valid"),
        CheckConstraint("(concept = 'OTHER') = (concept_description IS NOT NULL)", name="description_only_for_other"),
        CheckConstraint(_in_list("source", PaymentSource), name="source_valid"),
        CheckConstraint(_in_list("receipt_email_status", ReceiptEmailStatus), name="receipt_email_status_valid"),
        CheckConstraint("receipt_email_status <> 'SENT' OR receipt_sent_at IS NOT NULL", name="sent_has_timestamp"),
        # HU-008: optional, never blank ("nothing" is NULL).
        CheckConstraint(
            "transaction_reference IS NULL OR CHAR_LENGTH(TRIM(transaction_reference)) > 0",
            name="transaction_reference_not_blank",
        ),
        CheckConstraint("notes IS NULL OR CHAR_LENGTH(TRIM(notes)) > 0", name="notes_not_blank"),
        Index("ix_payments_resident_id_payment_date", "resident_id", "payment_date"),
        Index("ix_payments_unit_id", "unit_id"),
        Index("ix_payments_payment_date", "payment_date"),
        Index("ix_payments_registered_by_user_id", "registered_by_user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    unit_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("units.id", ondelete="RESTRICT"))
    registered_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    method: Mapped[str] = mapped_column(String(20))
    concept: Mapped[str] = mapped_column(String(20))
    concept_description: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20), server_default=text("'MANUAL'"))
    # HU-008: external reference of the transaction (transfer, card voucher; HU-010 will reuse it).
    # NULL when there is none: never invented, never the receipt number.
    transaction_reference: Mapped[str | None] = mapped_column(String(TRANSACTION_REFERENCE_MAX_LENGTH))
    # HU-008: the administrator's notes on the payment.
    notes: Mapped[str | None] = mapped_column(String(PAYMENT_NOTES_MAX_LENGTH))
    # The day the resident paid (Bogotá calendar date, never in the future).
    payment_date: Mapped[date] = mapped_column(Date)
    # When it was registered in the system (UTC).
    registered_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    resident_name_snapshot: Mapped[str] = mapped_column(String(150))
    unit_label_snapshot: Mapped[str] = mapped_column(String(40))
    receipt_email_status: Mapped[str] = mapped_column(String(20), server_default=text("'PENDING'"))
    receipt_sent_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))


class PaymentAllocation(Base):
    """Part of a payment applied to a charge. sum(per payment) <= payment.amount and
    sum(per charge) <= charge.amount are guaranteed by the service under locks."""

    __tablename__ = "payment_allocations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        # One allocation per (payment, charge); also the FK index for payment_id.
        UniqueConstraint("payment_id", "charge_id"),
        Index("ix_payment_allocations_charge_id", "charge_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payments.id", ondelete="RESTRICT"))
    charge_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("charges.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"))
