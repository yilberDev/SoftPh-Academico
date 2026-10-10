"""HU-008: the signed-in resident's own payments (GET /resident/payments and /{payment_id}).

The query string only filters: the resident is always the session's, never a
parameter (resident_id, user_id, ... are unknown parameters, rejected with 422:
extra="forbid"). Only the format is validated here; the search is read by
parse_search of services/resident_payments.py (its only implementation), and
the service validates the whole query again. The responses carry exactly what
the service returns: no resident name, email or document, no administrator,
no receipt-email data. Periods travel as "YYYY-MM"; instants are UTC.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.core.exceptions import FieldValidationError
from app.core.validation import invalid
from app.models.finance import FinancialConcept, PaymentMethod
from app.schemas.charge import period_text
from app.schemas.payment import PAGE_MAX, PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, UnitRef, clean_date
from app.services.allocation import PaymentStatus
from app.services.resident_payments import ResidentPaymentDetail, ResidentPaymentSummary, parse_search
from app.services.resident_payments import ResidentPaymentPage as ResidentPaymentResult


def _utc(moment: datetime) -> datetime:
    # Stored as naive UTC; the API always says it is UTC (same convention as HU-004).
    return moment.replace(tzinfo=UTC)


class ResidentReceiptQuery(BaseModel):
    """Query string of GET /resident/payments/{payment_id}/receipt: it takes none (HU-010).

    Unknown parameters are rejected with 422, as in the list (extra="forbid").
    """

    model_config = ConfigDict(extra="forbid")


class ResidentPaymentListQuery(BaseModel):
    """Query string of GET /resident/payments. Unknown parameters are rejected."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=PAGE_MAX)
    page_size: int = Field(default=PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX)
    status: PaymentStatus | None = None
    date_from: date | None = None
    date_to: date | None = None
    search: str | None = None  # a receipt number (RC-00000012 or 12) or a date (YYYY-MM-DD)

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: Any) -> Any:
        if value in (None, ""):
            return None
        if not isinstance(value, str) or value not in {s.value for s in PaymentStatus}:
            raise invalid("status_invalid", "Selecciona un estado válido.")
        return value

    @field_validator("date_from", mode="before")
    @classmethod
    def validate_date_from(cls, value: Any) -> Any:
        return None if value in (None, "") else clean_date(value, "La fecha inicial")

    @field_validator("date_to", mode="before")
    @classmethod
    def validate_date_to(cls, value: Any, info: ValidationInfo) -> Any:
        date_to = None if value in (None, "") else clean_date(value, "La fecha final")
        date_from = info.data.get("date_from")
        if date_to is not None and date_from is not None and date_from > date_to:
            raise invalid("date_range", "La fecha final no puede ser anterior a la inicial.")
        return date_to

    @field_validator("search", mode="before")
    @classmethod
    def validate_search(cls, value: Any) -> Any:
        try:
            parsed = parse_search(value)  # the service's rules, reported as a field of the query
        except FieldValidationError as exc:
            raise invalid("search_invalid", exc.details[0]["message"]) from None  # type: ignore[index]
        return None if parsed is None else value.strip()


# --- Responses --------------------------------------------------------------------------------------


class ResidentPaymentRead(BaseModel):
    """One row of the resident's table; the notes are in the detail only."""

    id: int
    receipt_number: str
    payment_date: date
    concept: FinancialConcept
    concept_description: str | None  # "Otro" only
    amount: Decimal
    method: PaymentMethod
    status: PaymentStatus
    transaction_reference: str | None

    @classmethod
    def from_summary(cls, item: ResidentPaymentSummary) -> Self:
        return cls(
            id=item.id, receipt_number=item.receipt_number, payment_date=item.payment_date, concept=item.concept,
            concept_description=item.concept_description, amount=item.amount, method=item.method,
            status=item.status, transaction_reference=item.transaction_reference,
        )


class ResidentPaymentPage(BaseModel):
    items: list[ResidentPaymentRead]
    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def from_page(cls, page: ResidentPaymentResult) -> Self:
        return cls(items=[ResidentPaymentRead.from_summary(i) for i in page.items], page=page.page,
                   page_size=page.page_size, total=page.total, total_pages=page.total_pages)


class PaymentAllocationRead(BaseModel):
    """An obligation covered by the payment."""

    charge_id: int
    concept: FinancialConcept
    description: str | None
    billing_period: str | None  # "YYYY-MM", monthly fees only
    due_date: date
    charge_amount: Decimal  # the obligation's original amount
    allocated_amount: Decimal  # what this payment covered of it
    allocated_at: datetime  # UTC


class ResidentPaymentDetailRead(ResidentPaymentRead):
    registered_at: datetime  # UTC
    notes: str | None  # written by the administrator, visible to the resident
    unit: UnitRef  # as on the receipt (snapshot at registration time)
    applied_amount: Decimal
    credit_amount: Decimal  # saldo a favor left by this payment
    allocations: list[PaymentAllocationRead]

    @classmethod
    def from_detail(cls, detail: ResidentPaymentDetail) -> Self:
        return cls(
            id=detail.id, receipt_number=detail.receipt_number, payment_date=detail.payment_date,
            concept=detail.concept, concept_description=detail.concept_description, amount=detail.amount,
            method=detail.method, status=detail.status, transaction_reference=detail.transaction_reference,
            registered_at=_utc(detail.registered_at), notes=detail.notes,
            unit=UnitRef(id=detail.unit.id, label=detail.unit.label), applied_amount=detail.applied_amount,
            credit_amount=detail.credit_amount,
            allocations=[
                PaymentAllocationRead(
                    charge_id=a.charge_id, concept=a.concept, description=a.description,
                    billing_period=period_text(a.billing_period), due_date=a.due_date,
                    charge_amount=a.charge_amount, allocated_amount=a.allocated_amount,
                    allocated_at=_utc(a.allocated_at),
                )
                for a in detail.allocations
            ],
        )
