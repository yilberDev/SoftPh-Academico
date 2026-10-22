"""HU-010: input and output of the resident's demo payment transactions (internal demo gateway).

Starting takes only the obligation (charge_id) and the amount; completing takes only the
fictitious test instrument chosen in the demo checkout (instrument_id): the backend resolves the
simulated answer from its catalog (services/demo_instruments.py), so an outcome or a status never
comes from the client. The resident is always the session's, and everything else (reference,
status, dates, the payment) is decided by services/demo_payments.py. Any other field is rejected
(extra="forbid"): a resident_id, a reference, an outcome, another amount or another charge never get in.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    StrictInt,
    WithJsonSchema,
    field_validator,
)

from app.core.validation import invalid
from app.models import DemoPaymentTransactionStatus, FinancialConcept
from app.models.finance import DEMO_INSTRUMENT_MAX_LENGTH
from app.schemas.charge import period_text
from app.schemas.invitation import ID_MAX
from app.schemas.payment import clean_amount
from app.services.allocation import ChargeStatus
from app.services.demo_instruments import CATALOG, UNKNOWN_MESSAGE, DemoInstrument, DemoInstrumentKind
from app.services.demo_payments import DemoPaymentTransactionResult, PayableCharge

AMOUNT_NOT_FLOAT_MESSAGE = 'El monto debe enviarse como texto (p. ej., "150000.50") o como número entero.'


def _valid_charge_id(value: int) -> int:
    if not 1 <= value <= ID_MAX:
        raise invalid("charge_id_invalid", "Selecciona una obligación.")
    return value


ChargeId = Annotated[StrictInt, AfterValidator(_valid_charge_id)]

# What OpenAPI documents for the amount: a decimal text or an integer, never a JSON number with a fraction.
DemoAmount = Annotated[
    Decimal,
    WithJsonSchema({"anyOf": [{"type": "string", "pattern": r"^\d{1,28}(\.\d{1,2})?$"}, {"type": "integer"}],
                    "title": "Amount", "examples": ["150000.50", 150000]}),
]


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC)


class DemoPaymentStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charge_id: ChargeId
    amount: DemoAmount

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: Any) -> Any:
        # A JSON number with a fraction is binary floating point: money never travels as one.
        if isinstance(value, float):
            raise invalid("amount_format", AMOUNT_NOT_FLOAT_MESSAGE)
        return clean_amount(value)  # exact, > 0, at most 2 decimals, within DECIMAL(12,2); never rounded


class DemoPaymentComplete(BaseModel):
    """The fictitious test instrument chosen in the demo checkout, and nothing else.

    Only its shape is checked here (a short text); whether it is an available instrument is the
    service's 422 on instrument_id, from the catalog. An outcome is never accepted, not even next to it.
    """

    model_config = ConfigDict(extra="forbid")

    instrument_id: str

    @field_validator("instrument_id", mode="before")
    @classmethod
    def validate_instrument_id(cls, value: Any) -> Any:
        if not isinstance(value, str) or len(value) > DEMO_INSTRUMENT_MAX_LENGTH:
            raise invalid("instrument_id_invalid", UNKNOWN_MESSAGE)
        return value


class DemoResultReason(StrEnum):
    """Why a resolved transaction did not end APPROVED, derived from its status and its instrument."""

    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"  # the instrument simulated a rejection
    TECHNICAL_ERROR = "TECHNICAL_ERROR"  # the instrument simulated a processing error
    NOT_APPLIED = "NOT_APPLIED"  # the instrument approved, but the payment could no longer be applied


def result_reason(status: DemoPaymentTransactionStatus, test_instrument: str | None) -> DemoResultReason | None:
    """Only from what is persisted (the final status and the instrument), so it never changes on a reload.

    No instrument (finished before instruments, cancelled, expired, still pending): no reason. An id
    no longer in the catalog (retired) gives no reason either: its metadata left with it.
    """
    instrument = CATALOG.get(test_instrument) if test_instrument is not None else None
    if instrument is None:
        return None
    if status == DemoPaymentTransactionStatus.FAILED and instrument.result == DemoPaymentTransactionStatus.APPROVED:
        return DemoResultReason.NOT_APPLIED  # the backend's own rules, never a simulated answer
    if status == instrument.result and instrument.reason is not None:
        return DemoResultReason(instrument.reason.value)
    return None


class DemoPaymentTransactionRead(BaseModel):
    """A demo transaction of the signed-in resident.

    payment_id and receipt_number exist only when APPROVED: receipt_number is the payment's derived
    number (RC-00000001); no receipt document is generated in this phase.
    """

    id: int
    reference: str  # demo reference (DEMO-…), never a real provider's
    charge_id: int
    amount: Decimal
    status: DemoPaymentTransactionStatus
    created_at: datetime  # UTC
    expires_at: datetime  # UTC: created_at + 15 minutes
    finished_at: datetime | None  # UTC; None while INITIATED
    payment_id: int | None
    receipt_number: str | None
    test_instrument: str | None  # the fictitious instrument that resolved it; None before instruments existed
    result_reason: DemoResultReason | None  # derived; None for an approval or without an instrument

    @classmethod
    def from_result(cls, result: DemoPaymentTransactionResult) -> Self:
        return cls(
            id=result.id, reference=result.reference, charge_id=result.charge_id, amount=result.amount,
            status=result.status, created_at=_utc(result.created_at), expires_at=_utc(result.expires_at),
            finished_at=_utc(result.finished_at) if result.finished_at is not None else None,
            payment_id=result.payment_id, receipt_number=result.receipt_number,
            test_instrument=result.test_instrument,
            result_reason=result_reason(result.status, result.test_instrument),
        )


class DemoInstrumentRead(BaseModel):
    """A fictitious test instrument of the demo checkout, with the result it simulates (a sandbox)."""

    id: str
    label: str
    kind: DemoInstrumentKind
    ending: str | None  # fictitious last digits of a test card; never a card number
    expected_result: Literal["APPROVED", "REJECTED", "FAILED"]
    description: str

    @classmethod
    def from_instrument(cls, instrument: DemoInstrument) -> Self:
        return cls(id=instrument.id, label=instrument.label, kind=instrument.kind, ending=instrument.ending,
                   expected_result=instrument.result.value, description=instrument.description)  # type: ignore[arg-type]


class DemoInstrumentQuery(BaseModel):
    """Query string of GET /resident/demo-payments/instruments: it takes none (unknown ones: 422)."""

    model_config = ConfigDict(extra="forbid")


class DemoInstrumentList(BaseModel):
    items: list[DemoInstrumentRead]  # the catalog's order


class DemoPayableChargeRead(BaseModel):
    """An obligation of the resident's current apartment that can still be paid (outstanding > 0).

    Only what choosing one needs: no apartment, author, allocations or other residents. The
    balances come from the persisted allocations; starting checks them again.
    """

    charge_id: int
    concept: FinancialConcept
    description: str | None
    billing_period: str | None  # "YYYY-MM"; monthly fees only
    due_date: date
    amount: Decimal
    applied_amount: Decimal
    outstanding_amount: Decimal
    status: ChargeStatus  # PENDING, PARTIAL or OVERDUE (due date already ended), never PAID
    in_progress_transaction_id: int | None  # the resident's own live INITIATED transaction of it

    @classmethod
    def from_payable(cls, payable: PayableCharge) -> Self:
        charge = payable.charge
        return cls(
            charge_id=charge.id, concept=charge.concept, description=charge.description,
            billing_period=period_text(charge.billing_period), due_date=charge.due_date, amount=charge.amount,
            applied_amount=charge.applied_amount, outstanding_amount=charge.outstanding_amount, status=charge.status,
            in_progress_transaction_id=payable.in_progress_transaction_id,
        )


class DemoPayableChargeQuery(BaseModel):
    """Query string of GET /resident/demo-payments/obligations: it takes none.

    Unknown parameters are rejected with 422, as in HU-008 (a resident_id, a unit_id or a charge_id
    never select anything: the resident is the session's).
    """

    model_config = ConfigDict(extra="forbid")


class DemoPayableChargeList(BaseModel):
    items: list[DemoPayableChargeRead]  # FIFO: due_date, created_at, id
