"""The account statement of a registered resident: HU-011 (administrator) and HU-014 (the resident).

One core for both. The statement belongs to the resident's CURRENT apartment, because charges
belong to the apartment, never to a person (HU-004 D1; HU-014: "el estado de cuenta asociado a mi
unidad residencial"). Nothing here computes a balance or a status of its own:

    obligations   charges.list_unit_charges: amount, applied, outstanding and status
                  (PAID > OVERDUE > PARTIAL > PENDING for Bogotá's today), the same rows as the
                  obligations of the administrator and of HU-010.
    totals        payments.unit_balance: pending = charges - allocations to them; credit
                  (saldo a favor) = payments - allocations from them (HU-004).
    payments      the apartment's payments with the applied amount of HU-004 (_applied_amount)
                  and allocation.payment_status. Every payment keeps who made it: the
                  administrator sees the name recorded on it (resident_name_snapshot) and
                  whether this resident made it; the resident sees only their own payments
                  (HU-014 criterion 5.1), while the obligations show everything applied to them.
    account status  derived from the obligation statuses only: OVERDUE ("en mora") when an
                  obligation is OVERDUE, PENDING ("pendiente") when another one still has a balance,
                  UP_TO_DATE ("al día") otherwise (HU-011 criterion 3.1).

Consistent reads in ONE transaction (InnoDB REPEATABLE READ: the snapshot is taken by its first
read), so the lines and the totals never mix two moments; the sum of the outstanding amounts must
equal the pending balance, or the statement is refused (a 500, never an incomplete statement:
CU-011 6c, CU-014 4b). Read only: no lock, no write, no audit.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import bogota_today, utc_now
from app.models import Charge, FinancialConcept, Payment, PaymentAllocation, PaymentMethod
from app.models.finance import PaymentSource
from app.schemas.resident import ResidentDetail
from app.services.allocation import ChargeStatus, PaymentStatus, payment_status, receipt_number
from app.services.charges import ChargeSummary, list_unit_charges
from app.services.payments import _applied_amount, money, unit_balance
from app.services.residents import get_resident


class AccountStatus(StrEnum):
    UP_TO_DATE = "UP_TO_DATE"  # al día: nothing left to pay
    PENDING = "PENDING"  # pendiente: something to pay, nothing past its due date
    OVERDUE = "OVERDUE"  # en mora: an obligation past its due date still has a balance


@dataclass(frozen=True)
class StatementPayment:
    id: int
    receipt_number: str
    payment_date: date
    registered_at: datetime  # UTC
    amount: Decimal
    applied_amount: Decimal
    credit_amount: Decimal  # the part of this payment not applied yet (saldo a favor)
    status: PaymentStatus
    method: PaymentMethod
    source: PaymentSource
    concept: FinancialConcept
    concept_description: str | None
    by_this_resident: bool
    payer_name: str | None  # the name recorded on the payment; administrator only (None for the resident)


@dataclass(frozen=True)
class StatementAllocation:
    payment_id: int
    charge_id: int
    amount: Decimal


@dataclass(frozen=True)
class StatementSummary:
    status: AccountStatus
    total_charged: Decimal
    total_applied: Decimal  # what payments covered of the obligations
    pending_balance: Decimal  # unit_balance: total pending (criterion 2.1)
    overdue_balance: Decimal  # the part of it past its due date
    credit_balance: Decimal  # unit_balance: saldo a favor
    obligations: int
    pending_obligations: int
    overdue_obligations: int
    payments: int


@dataclass(frozen=True)
class AccountStatement:
    resident: ResidentDetail
    as_of: date  # Bogotá's today: the statuses (overdue) are computed for it
    generated_at: datetime  # UTC
    summary: StatementSummary
    obligations: tuple[ChargeSummary, ...]
    payments: tuple[StatementPayment, ...]
    allocations: tuple[StatementAllocation, ...]

    @property
    def has_movements(self) -> bool:
        """False: no obligations and no payments (CU-011 5a, CU-014 3a, HU-014 criterion 5.2)."""
        return bool(self.obligations or self.payments)


class StatementInconsistentError(RuntimeError):
    """The lines and the totals disagree: never shown (a generic 500)."""


def status_from_counts(overdue_obligations: int, pending_obligations: int) -> AccountStatus:
    """The one rule (HU-011 criterion 3.1), also used by the pending balances report of HU-012."""
    if overdue_obligations > 0:
        return AccountStatus.OVERDUE
    if pending_obligations > 0:
        return AccountStatus.PENDING
    return AccountStatus.UP_TO_DATE


def account_status(obligations: Sequence[ChargeSummary]) -> AccountStatus:
    return status_from_counts(sum(1 for item in obligations if item.status == ChargeStatus.OVERDUE),
                              sum(1 for item in obligations if item.outstanding_amount > 0))


async def _payments(
    db: AsyncSession, unit_id: int, resident_id: int, *, all_payers: bool
) -> tuple[StatementPayment, ...]:
    applied = _applied_amount()
    conditions: list[Any] = [Payment.unit_id == unit_id]
    if not all_payers:
        conditions.append(Payment.resident_id == resident_id)  # the resident never sees another person's payment
    rows = (
        await db.execute(
            select(
                Payment.id, Payment.payment_date, Payment.registered_at, Payment.amount, Payment.method,
                Payment.source, Payment.concept, Payment.concept_description, Payment.resident_id,
                Payment.resident_name_snapshot, applied.label("applied"),
            )
            .where(*conditions)
            .order_by(Payment.payment_date.desc(), Payment.id.desc())
        )
    ).all()
    payments = []
    for row in rows:
        amount, applied_amount = money(row.amount), money(row.applied)
        payments.append(StatementPayment(
            id=row.id, receipt_number=receipt_number(row.id), payment_date=row.payment_date,
            registered_at=row.registered_at, amount=amount, applied_amount=applied_amount,
            credit_amount=amount - applied_amount, status=payment_status(amount, applied_amount),
            method=PaymentMethod(row.method), source=PaymentSource(row.source), concept=FinancialConcept(row.concept),
            concept_description=row.concept_description, by_this_resident=row.resident_id == resident_id,
            payer_name=row.resident_name_snapshot if all_payers else None,
        ))
    return tuple(payments)


async def _allocations(db: AsyncSession, payment_ids: Sequence[int]) -> tuple[StatementAllocation, ...]:
    if not payment_ids:
        return ()
    rows = (
        await db.execute(
            select(PaymentAllocation.payment_id, PaymentAllocation.charge_id, PaymentAllocation.amount)
            .join(Charge, Charge.id == PaymentAllocation.charge_id)
            .where(PaymentAllocation.payment_id.in_(payment_ids))
            .order_by(PaymentAllocation.payment_id, Charge.due_date, Charge.id)
        )
    ).all()
    return tuple(StatementAllocation(row.payment_id, row.charge_id, money(row.amount)) for row in rows)


async def build_account_statement(db: AsyncSession, resident_id: int, *, all_payers: bool) -> AccountStatement:
    """The statement of a registered resident's current apartment, from one consistent snapshot.

    all_payers: True for the administrator (every payment of the apartment, with who made it),
    False for the resident (only their own payments). ResidentNotFoundError when the resident is
    not registered (missing, another role, deleted, never activated or without a current residence).
    """
    await db.commit()  # a new transaction: its first read below takes the snapshot of every other read
    try:
        resident = await get_resident(db, resident_id)
        unit_id = resident.unit.id
        now = utc_now()
        today = bogota_today(now)
        obligations = await list_unit_charges(db, unit_id, today)
        balance = await unit_balance(db, unit_id)
        payments = await _payments(db, unit_id, resident_id, all_payers=all_payers)
        allocations = await _allocations(db, [payment.id for payment in payments])
    finally:
        await db.commit()  # read only: nothing to keep, the snapshot ends here

    pending = sum((item.outstanding_amount for item in obligations), Decimal("0.00"))
    if pending != balance.pending_balance:
        raise StatementInconsistentError("The obligations and the pending balance disagree.")
    overdue = [item for item in obligations if item.status == ChargeStatus.OVERDUE]
    summary = StatementSummary(
        status=account_status(obligations),
        total_charged=sum((item.amount for item in obligations), Decimal("0.00")),
        total_applied=sum((item.applied_amount for item in obligations), Decimal("0.00")),
        pending_balance=balance.pending_balance,
        overdue_balance=sum((item.outstanding_amount for item in overdue), Decimal("0.00")),
        credit_balance=balance.credit_balance,
        obligations=len(obligations),
        pending_obligations=sum(1 for item in obligations if item.outstanding_amount > 0),
        overdue_obligations=len(overdue),
        payments=len(payments),
    )
    return AccountStatement(resident=resident, as_of=today, generated_at=now, summary=summary,
                            obligations=obligations, payments=payments, allocations=allocations)
