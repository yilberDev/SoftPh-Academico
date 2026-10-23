"""HU-011 / HU-014: the account statement of a registered resident's current apartment (read only).

The same response for the administrator and for the resident: only the payments differ (the
administrator sees every payment of the apartment with the name recorded on it; the resident only
their own, without payer_name). Money is exact Decimal text, as in HU-004 ("150000.00").
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict

from app.models import FinancialConcept, PaymentMethod
from app.models.finance import PaymentSource
from app.schemas.charge import period_text
from app.schemas.resident import ResidentStatus, ResidentUnitRef
from app.services.account_statement import AccountStatement, AccountStatus
from app.services.allocation import ChargeStatus, PaymentStatus


def _utc(moment: datetime) -> datetime:
    # Stored as naive UTC; the API always says it is UTC (same convention as HU-004).
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


class AccountStatementQuery(BaseModel):
    """Query string of both routes: it takes none (a resident_id, a unit_id... is a 422, as HU-008)."""

    model_config = ConfigDict(extra="forbid")


class StatementResidentRead(BaseModel):
    id: int
    full_name: str
    status: ResidentStatus
    unit: ResidentUnitRef  # the current apartment: the statement is the apartment's


class StatementSummaryRead(BaseModel):
    status: AccountStatus  # UP_TO_DATE (al día), PENDING (pendiente), OVERDUE (en mora)
    total_charged: Decimal
    total_applied: Decimal
    pending_balance: Decimal
    overdue_balance: Decimal
    credit_balance: Decimal
    obligations: int
    pending_obligations: int
    overdue_obligations: int
    payments: int


class StatementObligationRead(BaseModel):
    id: int
    concept: FinancialConcept
    description: str | None
    billing_period: str | None  # "YYYY-MM"; monthly fees only
    due_date: date
    amount: Decimal
    applied_amount: Decimal
    outstanding_amount: Decimal
    status: ChargeStatus  # PAID, OVERDUE (en mora), PARTIAL, PENDING


class StatementAllocationRead(BaseModel):
    charge_id: int
    amount: Decimal


class StatementPaymentRead(BaseModel):
    id: int
    receipt_number: str
    payment_date: date
    registered_at: datetime  # UTC
    amount: Decimal
    applied_amount: Decimal
    credit_amount: Decimal
    status: PaymentStatus
    method: PaymentMethod
    source: PaymentSource
    concept: FinancialConcept
    concept_description: str | None
    by_this_resident: bool
    payer_name: str | None  # administrator only
    allocations: list[StatementAllocationRead]  # the obligations it covered


class AccountStatementRead(BaseModel):
    resident: StatementResidentRead
    as_of: date  # Bogotá: the day the statuses are computed for
    generated_at: datetime  # UTC
    has_movements: bool  # False: "no existen obligaciones ni movimientos" (CU-011 5a, CU-014 3a)
    summary: StatementSummaryRead
    obligations: list[StatementObligationRead]
    payments: list[StatementPaymentRead]

    @classmethod
    def from_statement(cls, statement: AccountStatement) -> Self:
        resident, summary = statement.resident, statement.summary
        by_payment: dict[int, list[StatementAllocationRead]] = {}
        for allocation in statement.allocations:
            by_payment.setdefault(allocation.payment_id, []).append(
                StatementAllocationRead(charge_id=allocation.charge_id, amount=allocation.amount))
        return cls(
            resident=StatementResidentRead(id=resident.id, full_name=resident.full_name, status=resident.status,
                                           unit=resident.unit),
            as_of=statement.as_of,
            generated_at=_utc(statement.generated_at),
            has_movements=statement.has_movements,
            summary=StatementSummaryRead(
                status=summary.status, total_charged=summary.total_charged, total_applied=summary.total_applied,
                pending_balance=summary.pending_balance, overdue_balance=summary.overdue_balance,
                credit_balance=summary.credit_balance, obligations=summary.obligations,
                pending_obligations=summary.pending_obligations, overdue_obligations=summary.overdue_obligations,
                payments=summary.payments,
            ),
            obligations=[
                StatementObligationRead(
                    id=item.id, concept=item.concept, description=item.description,
                    billing_period=period_text(item.billing_period), due_date=item.due_date, amount=item.amount,
                    applied_amount=item.applied_amount, outstanding_amount=item.outstanding_amount, status=item.status,
                )
                for item in statement.obligations
            ],
            payments=[
                StatementPaymentRead(
                    id=payment.id, receipt_number=payment.receipt_number, payment_date=payment.payment_date,
                    registered_at=_utc(payment.registered_at), amount=payment.amount,
                    applied_amount=payment.applied_amount, credit_amount=payment.credit_amount,
                    status=payment.status, method=payment.method, source=payment.source, concept=payment.concept,
                    concept_description=payment.concept_description, by_this_resident=payment.by_this_resident,
                    payer_name=payment.payer_name, allocations=by_payment.get(payment.id, []),
                )
                for payment in statement.payments
            ],
        )
