"""Pure rules of HU-004 and of the obligations extension.

HU-004: FIFO allocation, derived payment status and receipt number. FIFO: a
payment covers the pending charges of the apartment by due_date, then
created_at, then id (all ascending). What is left is kept as credit (saldo a
favor), never lost.

Obligations extension: derived charge status, allocation of existing credit to
pending charges (same FIFO order, oldest credit first) and billing periods.

Exact Decimal arithmetic: nothing is rounded. No database access.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

RECEIPT_PREFIX = "RC-"
RECEIPT_DIGITS = 8


class PaymentStatus(StrEnum):
    """Derived from the allocations of a payment; never stored or edited."""

    APPLIED = "APPLIED"
    PARTIALLY_APPLIED = "PARTIALLY_APPLIED"
    CREDIT = "CREDIT"


@dataclass(frozen=True)
class PendingCharge:
    id: int
    due_date: date
    created_at: datetime
    outstanding: Decimal  # charge amount minus everything already allocated to it


@dataclass(frozen=True)
class Allocation:
    charge_id: int
    amount: Decimal


@dataclass(frozen=True)
class AllocationResult:
    allocations: tuple[Allocation, ...]
    applied: Decimal
    credit: Decimal


def fifo_order(charge: PendingCharge) -> tuple[date, datetime, int]:
    return (charge.due_date, charge.created_at, charge.id)


def allocate_fifo(amount: Decimal, charges: Sequence[PendingCharge]) -> AllocationResult:
    """Splits amount over the charges in FIFO order; the rest is credit."""
    if amount <= 0:
        raise ValueError("A payment amount is always positive.")
    remaining = amount
    allocations: list[Allocation] = []
    for charge in sorted(charges, key=fifo_order):
        if remaining == 0:
            break
        if charge.outstanding <= 0:
            continue
        part = min(remaining, charge.outstanding)
        allocations.append(Allocation(charge_id=charge.id, amount=part))
        remaining -= part
    return AllocationResult(allocations=tuple(allocations), applied=amount - remaining, credit=remaining)


def payment_status(amount: Decimal, applied: Decimal) -> PaymentStatus:
    if applied >= amount:
        return PaymentStatus.APPLIED
    if applied <= 0:
        return PaymentStatus.CREDIT
    return PaymentStatus.PARTIALLY_APPLIED


def receipt_number(payment_id: int) -> str:
    """Visible receipt number derived from the payment id: RC-00000001."""
    return f"{RECEIPT_PREFIX}{payment_id:0{RECEIPT_DIGITS}d}"


# --- Obligations extension ---------------------------------------------------------------------------------------

_PERIOD_RE = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")


class ChargeStatus(StrEnum):
    """Derived from the amount, what was allocated to it and its due date; never stored."""

    PAID = "PAID"  # Pagada
    OVERDUE = "OVERDUE"  # Vencida
    PARTIAL = "PARTIAL"  # Parcial
    PENDING = "PENDING"  # Pendiente


def _is_date(value: object) -> bool:
    # datetime is a subclass of date, but a due date is a calendar day.
    return isinstance(value, date) and not isinstance(value, datetime)


def charge_status(amount: Decimal, applied: Decimal, due_date: date, today: date) -> ChargeStatus:
    """PAID > OVERDUE > PARTIAL > PENDING.

    A charge is overdue once its due date has ended: due_date < today (Bogotá
    calendar day, given by the caller). On its due date it is still pending.
    """
    if not isinstance(amount, Decimal) or not isinstance(applied, Decimal):
        raise ValueError("Money is always Decimal.")
    if not _is_date(due_date) or not _is_date(today):
        raise ValueError("due_date and today are calendar dates.")
    if amount <= 0:
        raise ValueError("A charge amount is always positive.")
    if applied < 0:
        raise ValueError("An applied amount is never negative.")
    if applied >= amount:
        return ChargeStatus.PAID
    if due_date < today:
        return ChargeStatus.OVERDUE
    if applied > 0:
        return ChargeStatus.PARTIAL
    return ChargeStatus.PENDING


@dataclass(frozen=True)
class AvailableCredit:
    payment_id: int
    payment_date: date
    registered_at: datetime
    available: Decimal  # payment amount minus everything already allocated from it


@dataclass(frozen=True)
class CreditAllocation:
    """A new payment_allocations row: part of an existing payment applied to a charge."""

    payment_id: int
    charge_id: int
    amount: Decimal


def credit_order(credit: AvailableCredit) -> tuple[date, datetime, int]:
    return (credit.payment_date, credit.registered_at, credit.payment_id)


def allocate_credit(
    credits: Sequence[AvailableCredit], charges: Sequence[PendingCharge]
) -> tuple[CreditAllocation, ...]:
    """Applies the oldest credit to the oldest pending charge (FIFO, as allocate_fifo).

    Each allocation is min(credit left, charge left). Charges not yet due are
    included. Credits or charges with nothing left (<= 0) receive nothing. The
    inputs are never modified.
    """
    if len({c.payment_id for c in credits}) != len(credits):
        raise ValueError("Each payment appears once in the credits.")
    if len({c.id for c in charges}) != len(charges):
        raise ValueError("Each charge appears once in the pending charges.")
    pending = [(charge.id, charge.outstanding) for charge in sorted(charges, key=fifo_order) if charge.outstanding > 0]
    allocations: list[CreditAllocation] = []
    index = 0
    for credit in sorted(credits, key=credit_order):
        left = credit.available
        while left > 0 and index < len(pending):
            charge_id, outstanding = pending[index]
            part = min(left, outstanding)
            allocations.append(CreditAllocation(payment_id=credit.payment_id, charge_id=charge_id, amount=part))
            left -= part
            if part == outstanding:
                index += 1
            else:
                pending[index] = (charge_id, outstanding - part)
        if index == len(pending):
            break
    return tuple(allocations)


def parse_period(value: str) -> date:
    """Parses "YYYY-MM" into the first day of that month. Strict: nothing else is accepted."""
    match = _PERIOD_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("A billing period is YYYY-MM.")
    try:
        return date(int(match.group(1)), int(match.group(2)), 1)
    except ValueError:
        raise ValueError("A billing period is YYYY-MM.") from None
