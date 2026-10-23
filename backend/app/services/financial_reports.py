"""HU-012: financial reports for the administrator (read only).

    payments          every payment registered in a range of payment dates (criterion 2: the
                      history of payments, each one identified), with its totals and the totals
                      by method. Its rows are those of the HU-004 list (payments.payments_between).
    pending balances  the apartments that still owe (criterion 3: the pending balances and the
                      residents with pending obligations; HU-011 criterion 3.2): what each one owes,
                      the part en mora, its credit (saldo a favor), its account status and its current
                      registered resident (to open the HU-011 statement). The amounts come from
                      charges.outstanding_by_unit (the rows of the obligations list, aggregated) and
                      payments.unit_balances (unit_balance, grouped); the status from
                      account_statement.status_from_counts, the rule of HU-011.

The statement of one resident (criterion 4) is the HU-011 statement itself (account_statement).
Each report reads one consistent snapshot (one transaction, REPEATABLE READ), so its lines and its
totals belong to the same moment; if the two sources of the pending balance disagree the report is
refused (a generic 500), never shown incomplete (CU-012 6c). No lock, no write, no audit.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import bogota_today, utc_now
from app.models import PaymentMethod, Unit, User
from app.schemas.payment import PaymentRead
from app.schemas.resident import ResidentListQuery
from app.services.account_statement import AccountStatus, status_from_counts
from app.services.charges import ChargeUnit, outstanding_by_unit
from app.services.payments import payments_between, unit_balances
from app.services.residents import _registered

ZERO = Decimal("0.00")


class ReportInconsistentError(RuntimeError):
    """Two sources of the same amount disagree: the report is never shown (a generic 500)."""


@dataclass(frozen=True)
class MethodTotal:
    method: PaymentMethod
    payments: int
    amount: Decimal


@dataclass(frozen=True)
class PaymentsReport:
    date_from: date
    date_to: date
    generated_at: datetime  # UTC
    items: tuple[PaymentRead, ...]
    total_amount: Decimal
    total_applied: Decimal
    total_credit: Decimal
    by_method: tuple[MethodTotal, ...]


@dataclass(frozen=True)
class ReportResident:
    id: int
    full_name: str


@dataclass(frozen=True)
class PendingBalance:
    unit: ChargeUnit
    resident: ReportResident | None  # the current registered resident; None for an apartment without one
    status: AccountStatus  # PENDING or OVERDUE (an apartment that owes is never UP_TO_DATE)
    pending_balance: Decimal
    overdue_balance: Decimal
    credit_balance: Decimal
    pending_obligations: int
    overdue_obligations: int
    oldest_due_date: date


@dataclass(frozen=True)
class PendingBalancesReport:
    as_of: date  # Bogotá: the day the statuses (en mora) are computed for
    generated_at: datetime  # UTC
    only_overdue: bool
    items: tuple[PendingBalance, ...]
    total_pending: Decimal
    total_overdue: Decimal
    overdue_units: int


async def _snapshot_start(db: AsyncSession) -> None:
    # A new transaction: its first read takes the snapshot every other read of the report shares.
    await db.commit()


async def payments_report(db: AsyncSession, date_from: date, date_to: date) -> PaymentsReport:
    """The payments registered with a payment date between date_from and date_to (both included)."""
    if date_from > date_to:
        raise ValueError("date_from must not be after date_to.")  # checked by the schema first
    await _snapshot_start(db)
    try:
        items = tuple(await payments_between(db, date_from, date_to))
    finally:
        await db.commit()
    by_method: dict[PaymentMethod, list[Decimal]] = defaultdict(list)
    for item in items:
        by_method[item.method].append(item.amount)
    return PaymentsReport(
        date_from=date_from, date_to=date_to, generated_at=utc_now(), items=items,
        total_amount=sum((item.amount for item in items), ZERO),
        total_applied=sum((item.applied_amount for item in items), ZERO),
        total_credit=sum((item.credit_amount for item in items), ZERO),
        by_method=tuple(MethodTotal(method, len(amounts), sum(amounts, ZERO))
                        for method, amounts in sorted(by_method.items(), key=lambda pair: pair[0].value)),
    )


async def _current_residents(db: AsyncSession) -> dict[int, ReportResident]:
    """unit_id -> its registered resident (the HU-006 definition: _registered with no filter)."""
    rows = (
        await db.execute(
            _registered(select(Unit.id.label("unit_id"), User.id, User.full_name).select_from(User),
                        ResidentListQuery())
        )
    ).all()
    return {row.unit_id: ReportResident(row.id, row.full_name) for row in rows}


async def pending_balances_report(db: AsyncSession, *, only_overdue: bool) -> PendingBalancesReport:
    """The apartments with a pending balance (all of them, or only those en mora)."""
    await _snapshot_start(db)
    try:
        now = utc_now()
        today = bogota_today(now)
        owing = await outstanding_by_unit(db, today)
        balances = await unit_balances(db)
        residents = await _current_residents(db)
    finally:
        await db.commit()

    items = []
    for row in owing:
        balance = balances.get(row.unit.id)
        if balance is None or balance.pending_balance != row.pending_amount:
            raise ReportInconsistentError("The obligations and the balance of an apartment disagree.")
        if only_overdue and row.overdue_obligations == 0:
            continue
        items.append(PendingBalance(
            unit=row.unit, resident=residents.get(row.unit.id),
            status=status_from_counts(row.overdue_obligations, row.pending_obligations),
            pending_balance=row.pending_amount, overdue_balance=row.overdue_amount,
            credit_balance=balance.credit_balance, pending_obligations=row.pending_obligations,
            overdue_obligations=row.overdue_obligations, oldest_due_date=row.oldest_due_date,
        ))
    # Every other apartment with charges must owe nothing (the two sources agree on all of them).
    owing_units = {row.unit.id for row in owing}
    if any(balance.pending_balance != 0 for unit_id, balance in balances.items() if unit_id not in owing_units):
        raise ReportInconsistentError("An apartment has a pending balance without pending obligations.")
    return PendingBalancesReport(
        as_of=today, generated_at=now, only_overdue=only_overdue, items=tuple(items),
        total_pending=sum((item.pending_balance for item in items), ZERO),
        total_overdue=sum((item.overdue_balance for item in items), ZERO),
        overdue_units=sum(1 for item in items if item.status == AccountStatus.OVERDUE),
    )
