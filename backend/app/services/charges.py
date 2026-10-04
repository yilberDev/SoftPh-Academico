"""Obligations of an apartment (obligations extension): creation and credit.

create  One atomic transaction:

            validate the data (no database)
            actor and apartment (no locks), COMMIT
            BEGIN
              lock block (S) → tower (S) → apartment (X)   same order as HU-004/HU-013
              origin of a late fee, existing monthly fee / late fee
              charge
              pending charges of the apartment (X)        FIFO: due_date, created_at, id
              payments of the apartment with credit       oldest first: payment_date, registered_at, id
              allocations of the credit + CHARGE_CREATED + CREDIT_APPLIED
              invariants checked again (never over-allocated)
            COMMIT

        Every change to the charges, payments or allocations of an apartment
        holds its lock (X), so two operations on the same apartment run one
        after the other and the credit is consumed only once. After the lock,
        payments and allocations are read with consistent reads (never FOR
        SHARE, which deadlocked operations on different apartments). The author is
        always the authenticated actor, never a value from the client. Any
        error rolls everything back: no charge, allocation or audit entry is
        left behind.

Status, applied amount and balances are derived from payment_allocations, never stored.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import bogota_today
from app.core.exceptions import (
    FieldValidationError,
    ForbiddenError,
    LateFeeExistsError,
    MonthlyFeeExistsError,
    UnitNotFoundError,
)
from app.core.money import CENT, MONEY_MAX, format_cop
from app.core.unit_labels import unit_label
from app.models import AuditAction, Charge, FinancialConcept, Payment, PaymentAllocation, Tower, Unit, User, UserRole, UserStatus
from app.schemas.payment import AccountBalance
from app.services.allocation import AvailableCredit, ChargeStatus, CreditAllocation, allocate_credit, charge_status, receipt_number
from app.services.audit import record_audit
from app.services.payments import _check_invariants, _pending_charges, money, unit_balance
from app.services.structure import Actor, lock_unit_path

DESCRIPTION_MAX_LENGTH = 200
UNIT_MISSING_MESSAGE = "Selecciona un apartamento existente."
UNIT_DELETED_MESSAGE = "El apartamento fue eliminado."


@dataclass(frozen=True)
class ChargeInput:
    """What the administrator decides. Never the author, status, applied amount or balance."""

    unit_id: int
    concept: FinancialConcept
    amount: Decimal
    due_date: date
    description: str | None = None
    billing_period: date | None = None  # first day of the month; MONTHLY_FEE only
    source_charge_id: int | None = None  # LATE_FEE only


@dataclass(frozen=True)
class AppliedCredit:
    payment_id: int
    receipt_number: str
    charge_id: int
    amount: Decimal


@dataclass(frozen=True)
class CreatedCharge:
    id: int
    unit_id: int
    unit_label: str
    concept: FinancialConcept
    description: str | None
    amount: Decimal
    due_date: date
    billing_period: date | None
    source_charge_id: int | None
    created_by_user_id: int
    created_at: datetime
    applied_amount: Decimal  # credit applied to this charge
    outstanding_amount: Decimal
    status: ChargeStatus
    # Every allocation made from existing credit (FIFO may also reach older pending charges).
    credit_applied: tuple[AppliedCredit, ...]
    account: AccountBalance


# --- Validation (no database) ----------------------------------------------------------------------------


def _is_date(value: object) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _validated(data: ChargeInput) -> ChargeInput:
    """Format and per-concept rules; the database enforces the same ones (CHECK)."""
    if not isinstance(data.unit_id, int) or isinstance(data.unit_id, bool) or data.unit_id <= 0:
        raise FieldValidationError("unit_id", UNIT_MISSING_MESSAGE)
    try:
        concept = FinancialConcept(data.concept)
    except ValueError:
        raise FieldValidationError("concept", "Selecciona un concepto válido.") from None
    amount = data.amount
    if not isinstance(amount, Decimal) or not amount.is_finite():
        raise FieldValidationError("amount", "El monto no es válido.")
    if amount <= 0:
        raise FieldValidationError("amount", "El monto debe ser mayor que cero.")
    if amount != amount.quantize(CENT):
        raise FieldValidationError("amount", "El monto admite hasta 2 decimales.")
    if amount > MONEY_MAX:
        raise FieldValidationError("amount", f"El monto no puede superar {format_cop(MONEY_MAX)}.")
    if not _is_date(data.due_date):
        raise FieldValidationError("due_date", "La fecha de vencimiento no es válida.")

    description = data.description.strip() if isinstance(data.description, str) else data.description
    if description is not None and not isinstance(description, str):
        raise FieldValidationError("description", "La descripción no es válida.")
    description = description or None
    if description is not None and len(description) > DESCRIPTION_MAX_LENGTH:
        raise FieldValidationError("description", f"La descripción admite hasta {DESCRIPTION_MAX_LENGTH} caracteres.")
    if concept in (FinancialConcept.PENALTY, FinancialConcept.OTHER) and description is None:
        raise FieldValidationError("description", "La descripción es obligatoria para este concepto.")
    if concept == FinancialConcept.MONTHLY_FEE and description is not None:
        raise FieldValidationError("description", "La mensualidad no lleva descripción.")

    period = data.billing_period
    if concept == FinancialConcept.MONTHLY_FEE:
        if period is None:
            raise FieldValidationError("billing_period", "El periodo es obligatorio para la mensualidad.")
        if not _is_date(period) or period.day != 1:
            raise FieldValidationError("billing_period", "El periodo no es válido.")
    elif period is not None:
        raise FieldValidationError("billing_period", "Solo la mensualidad tiene periodo.")

    source = data.source_charge_id
    if source is not None:
        if concept != FinancialConcept.LATE_FEE:
            raise FieldValidationError("source_charge_id", "Solo la mora puede tener una obligación de origen.")
        if not isinstance(source, int) or isinstance(source, bool) or source <= 0:
            raise FieldValidationError("source_charge_id", "La obligación de origen no existe.")
    return ChargeInput(data.unit_id, concept, amount, data.due_date, description, period, source)


# --- Credit (saldo a favor) ------------------------------------------------------------------------------


async def _available_credits(db: AsyncSession, unit_id: int) -> list[AvailableCredit]:
    """Payments of the apartment not fully allocated, oldest first.

    Only called while the apartment is locked (X), after the transaction's first
    consistent read: a consistent read has every allocation committed before the
    lock (every writer of them holds it). No FOR SHARE: a locking read of
    allocations that do not exist yet locks the gap shared by the new allocations
    of every apartment (deadlock between apartments, see payments._covered).
    """
    applied = (
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
        .where(PaymentAllocation.payment_id == Payment.id)
        .correlate(Payment)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(Payment.id, Payment.payment_date, Payment.registered_at, (Payment.amount - applied).label("available"))
            .where(Payment.unit_id == unit_id, Payment.amount > applied)
            .order_by(Payment.payment_date, Payment.registered_at, Payment.id)
        )
    ).all()
    return [AvailableCredit(row.id, row.payment_date, row.registered_at, money(row.available)) for row in rows]


async def _settle_credit(db: AsyncSession, unit_id: int) -> tuple[CreditAllocation, ...]:
    """Applies the existing credit of the apartment to its pending charges (FIFO, as payments do).

    Locks the pending charges (X, HU-004) and reads the credit (S), inserts the
    allocations and checks the invariants of every payment and charge involved.
    """
    pending = await _pending_charges(db, unit_id)
    if not pending:
        return ()
    credits = await _available_credits(db, unit_id)
    allocations = allocate_credit(credits, pending)
    if not allocations:
        return ()
    for allocation in allocations:
        db.add(PaymentAllocation(payment_id=allocation.payment_id, charge_id=allocation.charge_id, amount=allocation.amount))
    await db.flush()
    charge_ids = sorted({a.charge_id for a in allocations})
    payments = (await db.scalars(select(Payment).where(Payment.id.in_({a.payment_id for a in allocations})))).all()
    for payment in payments:
        await _check_invariants(db, payment, charge_ids)
    return allocations


# --- Create ----------------------------------------------------------------------------------------------


async def _check_actor(db: AsyncSession, actor: Actor) -> None:
    user = await db.get(User, actor.user_id, populate_existing=True)
    if (
        user is None
        or user.role != UserRole.ADMINISTRADOR
        or user.status != UserStatus.ACTIVE
        or user.deleted_at is not None
    ):
        raise ForbiddenError()


async def _unit_parents(db: AsyncSession, unit_id: int) -> tuple[int, int]:
    """(tower_id, block_id) of the apartment (no locks; they never change)."""
    row = (
        await db.execute(
            select(Unit.tower_id, Tower.block_id, Unit.deleted_at).join(Tower, Tower.id == Unit.tower_id).where(Unit.id == unit_id)
        )
    ).first()
    if row is None:
        raise FieldValidationError("unit_id", UNIT_MISSING_MESSAGE)
    if row.deleted_at is not None:
        raise FieldValidationError("unit_id", UNIT_DELETED_MESSAGE)
    return row.tower_id, row.block_id


async def _check_source(db: AsyncSession, data: ChargeInput, today: date) -> None:
    """The origin of a late fee: same apartment, not a late fee, already overdue, without a late fee yet.

    Charges are immutable and every write to the charges of an apartment holds
    its lock, so plain reads after the lock see the latest committed data.
    """
    source = await db.scalar(select(Charge).where(Charge.id == data.source_charge_id))
    if source is None:
        raise FieldValidationError("source_charge_id", "La obligación de origen no existe.")
    if source.unit_id != data.unit_id:
        raise FieldValidationError("source_charge_id", "La obligación de origen es de otro apartamento.")
    if source.concept == FinancialConcept.LATE_FEE:
        raise FieldValidationError("source_charge_id", "Una mora no puede originar otra mora.")
    if not source.due_date < today:
        raise FieldValidationError("source_charge_id", "La obligación de origen todavía no está vencida.")
    if await db.scalar(select(Charge.id).where(Charge.source_charge_id == source.id)) is not None:
        raise LateFeeExistsError()


async def _insert(db: AsyncSession, charge: Charge, label: str) -> None:
    """Flushes; the UNIQUE indexes are the final authority (same 409 as the checks)."""
    db.add(charge)
    try:
        await db.flush()
    except IntegrityError as exc:
        if "uq_charges_unit_id_billing_period" in str(exc.orig):
            raise MonthlyFeeExistsError(label, f"{charge.billing_period:%Y-%m}") from exc
        if "uq_charges_source_charge_id" in str(exc.orig):
            raise LateFeeExistsError() from exc
        raise


def _audit_context(charge: Charge, label: str) -> dict[str, Any]:
    return {
        "concept": str(charge.concept),
        "amount": str(money(charge.amount)),
        "due_date": charge.due_date.isoformat(),
        "billing_period": charge.billing_period.isoformat() if charge.billing_period else None,
        "unit_id": charge.unit_id,
        "label": label,
        "source_charge_id": charge.source_charge_id,
    }


async def create_charge(db: AsyncSession, data: ChargeInput, actor: Actor) -> CreatedCharge:
    """Creates one obligation of an apartment and applies the apartment's credit to its pending charges."""
    data = _validated(data)

    # 1. Actor and apartment (no locks).
    await db.commit()
    await _check_actor(db, actor)
    tower_id, block_id = await _unit_parents(db, data.unit_id)
    await db.commit()

    # 2. New transaction whose first statement is a locking read (fresh view after it).
    try:
        block, tower, unit = await lock_unit_path(db, data.unit_id, tower_id=tower_id, block_id=block_id)
    except UnitNotFoundError:
        await db.rollback()
        raise FieldValidationError("unit_id", UNIT_DELETED_MESSAGE) from None
    try:
        label = unit_label(block.number, tower.number, unit.number)
        today = bogota_today()
        if data.source_charge_id is not None:
            await _check_source(db, data, today)
        if data.billing_period is not None and await db.scalar(
            select(Charge.id).where(Charge.unit_id == data.unit_id, Charge.billing_period == data.billing_period)
        ) is not None:
            raise MonthlyFeeExistsError(label, f"{data.billing_period:%Y-%m}")

        charge = Charge(
            unit_id=data.unit_id,
            concept=data.concept,
            description=data.description,
            amount=data.amount,
            due_date=data.due_date,
            billing_period=data.billing_period,
            source_charge_id=data.source_charge_id,
            created_by_user_id=actor.user_id,
        )
        await _insert(db, charge, label)
        record_audit(
            db,
            action=AuditAction.CHARGE_CREATED,
            entity_type="charge",
            entity_id=charge.id,
            actor_user_id=actor.user_id,
            context=_audit_context(charge, label),
            ip_address=actor.ip_address,
        )

        # 3. The credit of the apartment covers its pending charges (this one included).
        allocations = await _settle_credit(db, data.unit_id)
        receipts = {a.payment_id: receipt_number(a.payment_id) for a in allocations}
        for charge_id in sorted({a.charge_id for a in allocations}):
            parts = [a for a in allocations if a.charge_id == charge_id]
            record_audit(
                db,
                action=AuditAction.CREDIT_APPLIED,
                entity_type="charge",
                entity_id=charge_id,
                actor_user_id=actor.user_id,
                context={
                    "unit_id": data.unit_id,
                    "charge_id": charge_id,
                    "total": str(money(sum((a.amount for a in parts), Decimal(0)))),
                    "payments": [
                        {"payment_id": a.payment_id, "receipt_number": receipts[a.payment_id], "amount": str(money(a.amount))}
                        for a in parts
                    ],
                },
                ip_address=actor.ip_address,
            )

        applied = money(sum((a.amount for a in allocations if a.charge_id == charge.id), Decimal(0)))
        account = await unit_balance(db, data.unit_id)
        created_at = await db.scalar(select(Charge.created_at).where(Charge.id == charge.id))
        result = CreatedCharge(
            id=charge.id,
            unit_id=data.unit_id,
            unit_label=label,
            concept=data.concept,
            description=data.description,
            amount=money(data.amount),
            due_date=data.due_date,
            billing_period=data.billing_period,
            source_charge_id=data.source_charge_id,
            created_by_user_id=actor.user_id,
            created_at=created_at,
            applied_amount=applied,
            outstanding_amount=money(data.amount) - applied,
            status=charge_status(data.amount, applied, data.due_date, today),
            credit_applied=tuple(
                AppliedCredit(a.payment_id, receipts[a.payment_id], a.charge_id, money(a.amount)) for a in allocations
            ),
            account=account,
        )
        await db.commit()
    except Exception:
        # Nothing is left behind: no charge, allocation or audit entry.
        await db.rollback()
        raise
    return result
