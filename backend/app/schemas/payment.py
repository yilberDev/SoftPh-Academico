"""Input and output of manual payments (HU-004).

The client only sends what the administrator chooses: resident, amount,
method, concept (+ description for OTHER) and payment date. Everything else
(apartment, registration time, snapshots, allocations, status, receipt number)
is decided by the backend; any other field is rejected (extra="forbid").
"""

import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationInfo,
    WithJsonSchema,
    field_validator,
)

from app.core.clock import bogota_today
from app.core.money import MONEY_MAX, format_cop
from app.core.validation import invalid
from app.models.finance import (
    MANUAL_PAYMENT_METHODS,
    PAYMENT_NOTES_MAX_LENGTH,
    TRANSACTION_REFERENCE_MAX_LENGTH,
    FinancialConcept,
    PaymentMethod,
    ReceiptEmailStatus,
)
from app.schemas.invitation import ID_MAX
from app.services.allocation import PaymentStatus

DESCRIPTION_MAX_LENGTH = 200
SEARCH_MAX_LENGTH = 100
PAGE_SIZE_DEFAULT = 10
PAGE_SIZE_MAX = 100
PAGE_MAX = 10_000
# Technical lower limit of a payment date (DATE columns accept far older dates).
PAYMENT_DATE_MIN = date(2000, 1, 1)

# Digits, optionally a dot and 1-2 decimals: no signs, exponents or separators.
_AMOUNT_RE = re.compile(r"^\d{1,28}(\.\d{1,2})?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

AMOUNT_FORMAT_MESSAGE = "El monto debe ser un número con hasta 2 decimales (p. ej., 150000 o 150000.50)."


def clean_amount(value: Any) -> Any:
    """Exact Decimal from a JSON number or a numeric string; more than 2 decimals is an error, never rounded."""
    if value is None or isinstance(value, bool):
        raise invalid("amount_required" if value is None else "amount_format",
                      "El monto es obligatorio." if value is None else AMOUNT_FORMAT_MESSAGE)
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = repr(value)  # shortest exact form of the JSON number, e.g. 150000.5
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise invalid("amount_required", "El monto es obligatorio.")
    else:
        raise invalid("amount_format", AMOUNT_FORMAT_MESSAGE)
    if text.startswith("-"):
        raise invalid("amount_positive", "El monto debe ser mayor que cero.")
    if not _AMOUNT_RE.match(text):
        raise invalid("amount_format", AMOUNT_FORMAT_MESSAGE)
    amount = Decimal(text)
    if amount <= 0:
        raise invalid("amount_positive", "El monto debe ser mayor que cero.")
    if amount > MONEY_MAX:
        raise invalid("amount_too_large", f"El monto no puede superar {format_cop(MONEY_MAX)}.")
    return amount


def clean_date(value: Any, field_label: str) -> Any:
    """Calendar date as 'YYYY-MM-DD' only (no timestamps or other formats)."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
        raise invalid("date_format", f"{field_label} no es válida.")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise invalid("date_format", f"{field_label} no es válida.") from None


def _enum_value(value: Any, values: type[StrEnum], error_type: str, message: str) -> Any:
    if not isinstance(value, str) or value not in {item.value for item in values}:
        raise invalid(error_type, message)
    return value


def _valid_resident_id(value: int) -> int:
    if not 1 <= value <= ID_MAX:
        raise invalid("resident_id_invalid", "Selecciona un residente.")
    return value


ResidentId = Annotated[StrictInt, AfterValidator(_valid_resident_id)]

# HU-010: the documented choices of a manual payment (OpenAPI only). The value is still a PaymentMethod,
# validated by PaymentCreate.validate_method; the responses keep PaymentMethod (with DEMO_GATEWAY).
ManualPaymentMethod = Annotated[
    PaymentMethod,
    WithJsonSchema({"type": "string", "enum": [m.value for m in MANUAL_PAYMENT_METHODS], "title": "Method"}),
]


def clean_transaction_reference(value: Any) -> str | None:
    """HU-008: optional, one line (spaces collapsed), printable, at most 100 characters; empty is None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid("transaction_reference_format", "La referencia de transacción debe ser un texto.")
    reference = " ".join(value.split())
    if not reference:
        return None
    if len(reference) > TRANSACTION_REFERENCE_MAX_LENGTH:
        raise invalid("transaction_reference_too_long",
                      f"La referencia de transacción no puede superar {TRANSACTION_REFERENCE_MAX_LENGTH} caracteres.")
    if not reference.isprintable():
        raise invalid("transaction_reference_invalid", "La referencia de transacción contiene caracteres no válidos.")
    return reference


def clean_payment_notes(value: Any) -> str | None:
    """HU-008: optional, trimmed (inner line breaks kept), at most 500 characters; empty is None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid("notes_format", "Las notas deben ser un texto.")
    notes = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not notes:
        return None
    if len(notes) > PAYMENT_NOTES_MAX_LENGTH:
        raise invalid("notes_too_long", f"Las notas no pueden superar {PAYMENT_NOTES_MAX_LENGTH} caracteres.")
    if not all(char.isprintable() or char in "\n\t" for char in notes):
        raise invalid("notes_invalid", "Las notas contienen caracteres no válidos.")
    return notes


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resident_id: ResidentId
    amount: Decimal
    method: ManualPaymentMethod
    concept: FinancialConcept
    # Validated even when omitted: required for OTHER, not allowed otherwise.
    concept_description: str | None = Field(default=None, validate_default=True)
    payment_date: date
    # HU-008: optional data of the administrator, shown in the detail of the payment.
    transaction_reference: str | None = None
    notes: str | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: Any) -> Any:
        return clean_amount(value)

    @field_validator("method", mode="before")
    @classmethod
    def validate_method(cls, value: Any) -> Any:
        message = "Selecciona un método de pago: efectivo, tarjeta o transferencia."
        method = _enum_value(value, PaymentMethod, "method_invalid", message)
        # HU-010: DEMO_GATEWAY belongs to the resident's demo transactions, never to a manual payment.
        if method not in MANUAL_PAYMENT_METHODS:
            raise invalid("method_invalid", message)
        return method

    @field_validator("concept", mode="before")
    @classmethod
    def validate_concept(cls, value: Any) -> Any:
        return _enum_value(value, FinancialConcept, "concept_invalid",
                           "Selecciona un concepto: mensualidad, mora, penalización u otro.")

    @field_validator("concept_description", mode="before")
    @classmethod
    def validate_description(cls, value: Any, info: ValidationInfo) -> Any:
        if value is not None and not isinstance(value, str):
            raise invalid("description_format", "La descripción debe ser un texto.")
        description = " ".join(value.split()) if value else ""
        if len(description) > DESCRIPTION_MAX_LENGTH:
            raise invalid("description_too_long",
                          f"La descripción no puede superar {DESCRIPTION_MAX_LENGTH} caracteres.")
        concept = info.data.get("concept")  # absent when the concept itself was invalid
        if concept == FinancialConcept.OTHER and not description:
            raise invalid("describe_other", "Describe el concepto del pago.")
        if concept is not None and concept != FinancialConcept.OTHER and description:
            raise invalid("description_only_other", "La descripción solo se indica cuando el concepto es «Otro».")
        return description or None

    @field_validator("payment_date", mode="before")
    @classmethod
    def validate_payment_date(cls, value: Any) -> Any:
        payment_date = clean_date(value, "La fecha del pago")
        if payment_date < PAYMENT_DATE_MIN:
            raise invalid("payment_date_too_old", "La fecha del pago no puede ser anterior al 1 de enero de 2000.")
        # Never after today in Bogotá (the date the administrator sees).
        if payment_date > bogota_today():
            raise invalid("payment_date_future", "La fecha del pago no puede ser posterior a hoy.")
        return payment_date

    @field_validator("transaction_reference", mode="before")
    @classmethod
    def validate_transaction_reference(cls, value: Any) -> Any:
        return clean_transaction_reference(value)

    @field_validator("notes", mode="before")
    @classmethod
    def validate_notes(cls, value: Any) -> Any:
        return clean_payment_notes(value)


# --- Responses --------------------------------------------------------------------------------------


class ResidentRef(BaseModel):
    id: int
    full_name: str  # snapshot at registration time


class UnitRef(BaseModel):
    id: int
    label: str  # snapshot at registration time (e.g. "B1-TO2-APTO-104")


class PaymentRead(BaseModel):
    id: int
    receipt_number: str
    resident: ResidentRef
    unit: UnitRef
    amount: Decimal
    method: PaymentMethod
    concept: FinancialConcept
    concept_description: str | None
    payment_date: date
    registered_at: datetime  # UTC (timezone-aware)
    transaction_reference: str | None
    notes: str | None
    applied_amount: Decimal
    credit_amount: Decimal
    status: PaymentStatus
    receipt_email_status: ReceiptEmailStatus


class AllocationRead(BaseModel):
    charge_id: int
    concept: FinancialConcept
    description: str | None
    due_date: date
    amount: Decimal


class AccountBalance(BaseModel):
    """The apartment's balance right after the payment (derived, never stored)."""

    pending_balance: Decimal  # charges not yet covered
    credit_balance: Decimal  # unapplied parts of payments (saldo a favor)


class PaymentRegistered(PaymentRead):
    allocations: list[AllocationRead]
    account: AccountBalance


# --- List (criterion 4) -------------------------------------------------------------------------------


class PaymentSortField(StrEnum):
    # Whitelist: each value is translated to SQLAlchemy expressions by the service.
    DATE = "date"
    RESIDENT = "resident"
    AMOUNT = "amount"
    CONCEPT = "concept"
    METHOD = "method"
    STATUS = "status"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class PaymentListQuery(BaseModel):
    """Query string of GET /admin/payments. Unknown parameters are rejected."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=PAGE_MAX)
    page_size: int = Field(default=PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX)
    resident_id: int | None = Field(default=None, ge=1, le=ID_MAX)
    search: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    sort_by: PaymentSortField = PaymentSortField.DATE
    sort_order: SortOrder = SortOrder.DESC

    @field_validator("search", mode="before")
    @classmethod
    def clean_search(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        search = value.strip()
        if len(search) > SEARCH_MAX_LENGTH:
            raise invalid("search_too_long", f"La búsqueda no puede superar {SEARCH_MAX_LENGTH} caracteres.")
        return search or None

    @field_validator("date_from", mode="before")
    @classmethod
    def clean_date_from(cls, value: Any) -> Any:
        return None if value in (None, "") else clean_date(value, "La fecha inicial")

    @field_validator("date_to", mode="before")
    @classmethod
    def clean_date_to(cls, value: Any, info: ValidationInfo) -> Any:
        date_to = None if value in (None, "") else clean_date(value, "La fecha final")
        date_from = info.data.get("date_from")
        if date_to is not None and date_from is not None and date_from > date_to:
            raise invalid("date_range", "La fecha final no puede ser anterior a la inicial.")
        return date_to


class PaymentPage(BaseModel):
    items: list[PaymentRead]
    page: int
    page_size: int
    total: int
    total_pages: int
