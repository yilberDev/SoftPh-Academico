"""Input and output of the obligations (charges) of the administration API.

Only the format is validated here (amounts, dates, periods, ids, allowed
values); the financial rules (description and period by concept, origin of a
late fee, duplicates, due date of a monthly fee) belong to services/charges.py
and answer with the same 422/409 format. The client never sends the author,
the status, the applied or outstanding amounts or any credit: unknown fields
are rejected (extra="forbid"). Periods travel as "YYYY-MM".
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, ValidationInfo, field_validator

from app.core.validation import invalid
from app.models.finance import FinancialConcept, PaymentMethod
from app.schemas.invitation import ID_MAX
from app.schemas.payment import AccountBalance, SortOrder, clean_amount, clean_date
from app.services.allocation import ChargeStatus, parse_period
from app.services.charges import (
    LIST_PAGE_MAX,
    LIST_PAGE_SIZE_DEFAULT,
    LIST_PAGE_SIZE_MAX,
    ChargeDetail,
    ChargeSortField,
    ChargeSummary,
    CreatedCharge,
    MonthlyGenerationResult,
    MonthlyPreview,
)

PERIOD_MESSAGE = "El periodo debe tener el formato AAAA-MM."
FINGERPRINT_LENGTH = 64  # SHA-256 in hexadecimal (services/monthly_rules.py)


def clean_period(value: Any) -> Any:
    """'YYYY-MM' → first day of that month (parse_period); any month, past or future."""
    if isinstance(value, date) and not isinstance(value, datetime) and value.day == 1:
        return value
    try:
        return parse_period(value)
    except ValueError:
        raise invalid("period_format", PERIOD_MESSAGE) from None


def _valid_id(value: int) -> int:
    if not 1 <= value <= ID_MAX:
        raise invalid("id_invalid", "El identificador no es válido.")
    return value


Id = Annotated[StrictInt, AfterValidator(_valid_id)]


def period_text(value: date | None) -> str | None:
    return None if value is None else f"{value:%Y-%m}"


def _utc(moment: datetime) -> datetime:
    # Stored as naive UTC; the API always says it is UTC (same convention as HU-004).
    return moment.replace(tzinfo=UTC)


# --- Input ---------------------------------------------------------------------------------------------


class ChargeCreate(BaseModel):
    """POST /admin/charges: one obligation of an apartment."""

    model_config = ConfigDict(extra="forbid")

    unit_id: Id
    concept: FinancialConcept
    amount: Decimal
    due_date: date
    description: str | None = None
    billing_period: date | None = None  # "YYYY-MM"; MONTHLY_FEE only (service rule)
    source_charge_id: Id | None = None  # LATE_FEE only (service rule)

    @field_validator("concept", mode="before")
    @classmethod
    def validate_concept(cls, value: Any) -> Any:
        if not isinstance(value, str) or value not in {c.value for c in FinancialConcept}:
            raise invalid("concept_invalid", "Selecciona un concepto: mensualidad, mora, penalización u otro.")
        return value

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: Any) -> Any:
        return clean_amount(value)

    @field_validator("due_date", mode="before")
    @classmethod
    def validate_due_date(cls, value: Any) -> Any:
        return clean_date(value, "La fecha de vencimiento")

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, value: Any) -> Any:
        if value is not None and not isinstance(value, str):
            raise invalid("description_format", "La descripción debe ser un texto.")
        return value

    @field_validator("billing_period", mode="before")
    @classmethod
    def validate_period(cls, value: Any) -> Any:
        return None if value is None else clean_period(value)


class MonthlyPreviewQuery(BaseModel):
    """GET /admin/charges/monthly/preview."""

    model_config = ConfigDict(extra="forbid")

    billing_period: date
    amount: Decimal
    due_date: date

    @field_validator("billing_period", mode="before")
    @classmethod
    def validate_period(cls, value: Any) -> Any:
        return clean_period(value)

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: Any) -> Any:
        return clean_amount(value)

    @field_validator("due_date", mode="before")
    @classmethod
    def validate_due_date(cls, value: Any) -> Any:
        return clean_date(value, "La fecha de vencimiento")


class MonthlyGenerationCreate(MonthlyPreviewQuery):
    """POST /admin/charges/monthly: confirms exactly what the preview showed."""

    expected_to_create: StrictInt = Field(ge=0, le=ID_MAX)
    preview_fingerprint: str = Field(min_length=FINGERPRINT_LENGTH, max_length=FINGERPRINT_LENGTH,
                                     pattern=r"^[0-9a-f]+$")


class ChargeListParams(BaseModel):
    """Query string of GET /admin/charges. Unknown parameters are rejected."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=LIST_PAGE_MAX)
    page_size: int = Field(default=LIST_PAGE_SIZE_DEFAULT, ge=1, le=LIST_PAGE_SIZE_MAX)
    unit_id: int | None = Field(default=None, ge=1, le=ID_MAX)
    block_id: int | None = Field(default=None, ge=1, le=ID_MAX)
    tower_id: int | None = Field(default=None, ge=1, le=ID_MAX)
    concept: FinancialConcept | None = None
    status: ChargeStatus | None = None
    billing_period: date | None = None  # "YYYY-MM"
    due_from: date | None = None
    due_to: date | None = None
    sort_by: ChargeSortField = ChargeSortField.DUE_DATE
    sort_order: SortOrder = SortOrder.ASC

    @field_validator("billing_period", mode="before")
    @classmethod
    def validate_period(cls, value: Any) -> Any:
        return None if value in (None, "") else clean_period(value)

    @field_validator("due_from", mode="before")
    @classmethod
    def validate_due_from(cls, value: Any) -> Any:
        return None if value in (None, "") else clean_date(value, "La fecha inicial")

    @field_validator("due_to", mode="before")
    @classmethod
    def validate_due_to(cls, value: Any, info: ValidationInfo) -> Any:
        due_to = None if value in (None, "") else clean_date(value, "La fecha final")
        due_from = info.data.get("due_from")
        if due_to is not None and due_from is not None and due_from > due_to:
            raise invalid("date_range", "La fecha final no puede ser anterior a la inicial.")
        return due_to


# --- Output --------------------------------------------------------------------------------------------


class ChargeRead(BaseModel):
    id: int
    unit_id: int
    unit_label: str
    unit_deleted: bool
    concept: FinancialConcept
    description: str | None
    billing_period: str | None  # "YYYY-MM"
    due_date: date
    amount: Decimal
    applied: Decimal
    outstanding: Decimal
    status: ChargeStatus
    source_charge_id: int | None
    created_at: datetime

    @classmethod
    def from_summary(cls, item: ChargeSummary) -> Self:
        return cls(
            id=item.id, unit_id=item.unit.id, unit_label=item.unit.label, unit_deleted=item.unit.deleted,
            concept=item.concept, description=item.description, billing_period=period_text(item.billing_period),
            due_date=item.due_date, amount=item.amount, applied=item.applied_amount,
            outstanding=item.outstanding_amount, status=item.status, source_charge_id=item.source_charge_id,
            created_at=_utc(item.created_at),
        )


class ChargePage(BaseModel):
    items: list[ChargeRead]
    page: int
    page_size: int
    total: int
    total_pages: int


class ChargeAllocationRead(BaseModel):
    payment_id: int
    receipt_number: str
    payment_date: date
    payment_method: PaymentMethod
    applied_amount: Decimal
    allocation_date: datetime


class ChargeDetailRead(ChargeRead):
    created_by_user_id: int
    source: ChargeRead | None
    late_fees: list[ChargeRead]
    allocations: list[ChargeAllocationRead]

    @classmethod
    def from_detail(cls, detail: ChargeDetail) -> Self:
        return cls(
            **ChargeRead.from_summary(detail.charge).model_dump(),
            created_by_user_id=detail.created_by_user_id,
            source=None if detail.source is None else ChargeRead.from_summary(detail.source),
            late_fees=[ChargeRead.from_summary(f) for f in detail.late_fees],
            allocations=[
                ChargeAllocationRead(payment_id=a.payment_id, receipt_number=a.receipt_number,
                                     payment_date=a.payment_date, payment_method=a.payment_method,
                                     applied_amount=a.applied_amount, allocation_date=_utc(a.allocation_date))
                for a in detail.allocations
            ],
        )


class CreditAppliedRead(BaseModel):
    payment_id: int
    receipt_number: str
    charge_id: int  # FIFO may also cover older pending charges of the apartment
    amount: Decimal


class ChargeCreated(BaseModel):
    id: int
    unit_id: int
    unit_label: str
    concept: FinancialConcept
    description: str | None
    billing_period: str | None
    due_date: date
    amount: Decimal
    applied: Decimal
    outstanding: Decimal
    status: ChargeStatus
    source_charge_id: int | None
    created_by_user_id: int
    created_at: datetime
    credit_applied: list[CreditAppliedRead]
    account: AccountBalance

    @classmethod
    def from_created(cls, created: CreatedCharge) -> Self:
        return cls(
            id=created.id, unit_id=created.unit_id, unit_label=created.unit_label, concept=created.concept,
            description=created.description, billing_period=period_text(created.billing_period),
            due_date=created.due_date, amount=created.amount, applied=created.applied_amount,
            outstanding=created.outstanding_amount, status=created.status, source_charge_id=created.source_charge_id,
            created_by_user_id=created.created_by_user_id, created_at=_utc(created.created_at),
            credit_applied=[CreditAppliedRead(payment_id=c.payment_id, receipt_number=c.receipt_number,
                                              charge_id=c.charge_id, amount=c.amount) for c in created.credit_applied],
            account=created.account,
        )


class MonthlyPreviewRead(BaseModel):
    billing_period: str
    amount: Decimal
    due_date: date
    active_units: int
    existing: int
    missing: int
    to_create: int
    total_amount: Decimal
    existing_with_other_amount: int
    preview_fingerprint: str  # produced by the service; sent back to confirm

    @classmethod
    def from_preview(cls, preview: MonthlyPreview) -> Self:
        return cls(
            billing_period=f"{preview.billing_period:%Y-%m}", amount=preview.amount, due_date=preview.due_date,
            active_units=preview.active_units, existing=preview.existing, missing=preview.missing,
            to_create=preview.to_create, total_amount=preview.total_amount,
            existing_with_other_amount=preview.existing_with_other_amount,
            preview_fingerprint=preview.preview_fingerprint,
        )


class UnitIssueRead(BaseModel):
    unit_id: int
    label: str
    code: str
    message: str


class MonthlyGenerationRead(BaseModel):
    """existing + created + omitted + failed = units_evaluated. The created fees are not listed:
    GET /admin/charges?billing_period=YYYY-MM shows them."""

    billing_period: str
    amount: Decimal
    due_date: date
    units_evaluated: int
    created: int
    existing: int
    omitted: int
    failed: int
    generated_total: Decimal
    credit_applied_total: Decimal
    credit_applied_to_new_charges: Decimal
    units_with_credit_applied: int
    outstanding_total: Decimal
    omitted_units: list[UnitIssueRead]
    errors: list[UnitIssueRead]
    audit_recorded: bool

    @classmethod
    def from_result(cls, result: MonthlyGenerationResult) -> Self:
        t = result.totals

        def issues(items: Any) -> list[UnitIssueRead]:
            return [UnitIssueRead(unit_id=i.unit_id, label=i.label, code=i.code, message=i.message) for i in items]

        return cls(
            billing_period=f"{result.billing_period:%Y-%m}", amount=result.amount, due_date=result.due_date,
            units_evaluated=t.units_evaluated, created=t.created, existing=t.existing, omitted=t.omitted,
            failed=t.failed, generated_total=t.generated_total, credit_applied_total=t.credit_applied_total,
            credit_applied_to_new_charges=t.credit_applied_to_new_charges,
            units_with_credit_applied=t.units_with_credit_applied, outstanding_total=t.outstanding_total,
            omitted_units=issues(t.omitted_units), errors=issues(t.errors), audit_recorded=result.audit_recorded,
        )
