"""HU-010: input and output of the resident's demo payment transactions (internal demo gateway).

Starting takes only the obligation (charge_id) and the amount; completing takes only the demo
gateway's outcome. The resident is always the session's, and everything else (reference, status,
dates, the payment) is decided by services/demo_payments.py. Any other field is rejected
(extra="forbid"): a resident_id, a reference, another amount or another charge never get in.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    StrictInt,
    WithJsonSchema,
    field_validator,
)

from app.core.validation import invalid
from app.models import DemoPaymentTransactionStatus
from app.schemas.invitation import ID_MAX
from app.schemas.payment import clean_amount
from app.services.demo_payments import DemoOutcome, DemoPaymentTransactionResult

AMOUNT_NOT_FLOAT_MESSAGE = 'El monto debe enviarse como texto (p. ej., "150000.50") o como número entero.'
OUTCOME_MESSAGE = "Selecciona un resultado de la pasarela de demostración: APPROVED, REJECTED o FAILED."


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
    """The demo gateway's answer, and nothing else."""

    model_config = ConfigDict(extra="forbid")

    outcome: DemoOutcome

    @field_validator("outcome", mode="before")
    @classmethod
    def validate_outcome(cls, value: Any) -> Any:
        if not isinstance(value, str) or value not in {outcome.value for outcome in DemoOutcome}:
            raise invalid("outcome_invalid", OUTCOME_MESSAGE)
        return value


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

    @classmethod
    def from_result(cls, result: DemoPaymentTransactionResult) -> Self:
        return cls(
            id=result.id, reference=result.reference, charge_id=result.charge_id, amount=result.amount,
            status=result.status, created_at=_utc(result.created_at), expires_at=_utc(result.expires_at),
            finished_at=_utc(result.finished_at) if result.finished_at is not None else None,
            payment_id=result.payment_id, receipt_number=result.receipt_number,
        )
