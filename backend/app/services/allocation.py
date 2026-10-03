"""Pure rules of HU-004: FIFO allocation, derived status and receipt number.

FIFO: a payment covers the pending charges of the apartment by due_date, then
created_at, then id (all ascending). What is left is kept as credit (saldo a
favor), never lost. Exact Decimal arithmetic: nothing is rounded.
"""

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
