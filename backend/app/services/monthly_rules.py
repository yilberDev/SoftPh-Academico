"""Pure rules of the monthly generation (obligations extension): preview
fingerprint and the totals of a run. No database access, no side effects.

The number of apartments is never a constant: everything here works on the
apartments the caller found in the database at that moment.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

FINGERPRINT_VERSION = "v1"


@dataclass(frozen=True)
class MonthlyParams:
    """The same values for every apartment of a run."""

    billing_period: date  # first day of the month
    amount: Decimal
    due_date: date


def preview_fingerprint(params: MonthlyParams, active_unit_ids: Sequence[int], existing_unit_ids: Sequence[int]) -> str:
    """Deterministic SHA-256 of what a preview showed.

    Canonical text, one field per "|" and ids in ascending order joined by ","
    (never the order of a query, a Python repr or a label):

        v1|<period>|<amount 2 decimals>|<due date>|A:<active unit ids>|E:<active unit ids with the monthly fee>

    Any change in the composition (an apartment created or deleted, a monthly
    fee created meanwhile, even with the same counts) or in the parameters
    gives another fingerprint. Only ids and the run parameters: nothing secret.
    """
    canonical = "|".join(
        (
            FINGERPRINT_VERSION,
            params.billing_period.isoformat(),
            f"{params.amount:.2f}",
            params.due_date.isoformat(),
            "A:" + ",".join(str(unit_id) for unit_id in sorted(set(active_unit_ids))),
            "E:" + ",".join(str(unit_id) for unit_id in sorted(set(existing_unit_ids))),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OutcomeKind(StrEnum):
    CREATED = "CREATED"
    EXISTING = "EXISTING"  # already there (before the run or created meanwhile)
    OMITTED = "OMITTED"  # e.g. the apartment was deleted meanwhile
    FAILED = "FAILED"


@dataclass(frozen=True)
class UnitOutcome:
    """What happened to one apartment of the run."""

    unit_id: int
    label: str
    kind: OutcomeKind
    code: str | None = None  # OMITTED / FAILED
    message: str | None = None
    amount: Decimal = Decimal("0.00")  # CREATED: the monthly fee
    credit_to_new_charge: Decimal = Decimal("0.00")  # CREATED: credit applied to the new monthly fee
    credit_total: Decimal = Decimal("0.00")  # CREATED: all the credit applied (FIFO may reach older charges)
    outstanding: Decimal = Decimal("0.00")  # CREATED: what is left of the new monthly fee


@dataclass(frozen=True)
class UnitIssue:
    unit_id: int
    label: str
    code: str
    message: str


@dataclass(frozen=True)
class MonthlyTotals:
    """existing + created + omitted + failed = units_evaluated (every apartment considered)."""

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
    omitted_units: tuple[UnitIssue, ...]
    errors: tuple[UnitIssue, ...]


def summarize(outcomes: Sequence[UnitOutcome]) -> MonthlyTotals:
    """Totals of a run; each apartment is counted exactly once."""
    if len({o.unit_id for o in outcomes}) != len(outcomes):
        raise ValueError("Each apartment has exactly one outcome.")
    created = [o for o in outcomes if o.kind == OutcomeKind.CREATED]

    def issues(kind: OutcomeKind) -> tuple[UnitIssue, ...]:
        return tuple(UnitIssue(o.unit_id, o.label, o.code or kind.value, o.message or "") for o in outcomes if o.kind == kind)

    totals = MonthlyTotals(
        units_evaluated=len(outcomes),
        created=len(created),
        existing=sum(1 for o in outcomes if o.kind == OutcomeKind.EXISTING),
        omitted=sum(1 for o in outcomes if o.kind == OutcomeKind.OMITTED),
        failed=sum(1 for o in outcomes if o.kind == OutcomeKind.FAILED),
        generated_total=sum((o.amount for o in created), Decimal("0.00")),
        credit_applied_total=sum((o.credit_total for o in created), Decimal("0.00")),
        credit_applied_to_new_charges=sum((o.credit_to_new_charge for o in created), Decimal("0.00")),
        units_with_credit_applied=sum(1 for o in created if o.credit_total > 0),
        outstanding_total=sum((o.outstanding for o in created), Decimal("0.00")),
        omitted_units=issues(OutcomeKind.OMITTED),
        errors=issues(OutcomeKind.FAILED),
    )
    if totals.existing + totals.created + totals.omitted + totals.failed != totals.units_evaluated:
        raise RuntimeError("Monthly generation counts are inconsistent.")
    return totals
