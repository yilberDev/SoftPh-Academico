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

monthly Preview (read only) and generation of the monthly fee of a period for
        every active apartment found at that moment (never a fixed number):

            apartments and existing fees again (no locks), COMMIT
            count + fingerprint differ from the preview → MONTHLY_PREVIEW_OUTDATED, nothing created
            for each missing apartment: the transaction of create (its core),
              rolled back alone on error; deadlock / lock timeout retried once
              in a new transaction
            MONTHLY_CHARGES_GENERATED                     (another transaction)

        Idempotent: UNIQUE (unit_id, billing_period) plus the check under the
        apartment lock. Two runs of the same period share the apartments
        without duplicates; a repeated run creates only what is missing.

Status, applied amount and balances are derived from payment_allocations, never stored.
"""

import logging
import math
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, case, func, literal, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import bogota_today
from app.core.exceptions import (
    ChargeNotFoundError,
    FieldValidationError,
    ForbiddenError,
    LateFeeExistsError,
    MonthlyFeeExistsError,
    MonthlyPreviewOutdatedError,
    UnitNotFoundError,
)
from app.core.money import CENT, MONEY_MAX, format_cop
from app.core.unit_labels import unit_label
from app.models import (
    AuditAction,
    Block,
    Charge,
    FinancialConcept,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    Tower,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.schemas.payment import AccountBalance, SortOrder
from app.services.allocation import (
    AvailableCredit,
    ChargeStatus,
    CreditAllocation,
    allocate_credit,
    charge_status,
    parse_period,
    receipt_number,
)
from app.services.audit import record_audit
from app.services.monthly_rules import (
    MonthlyParams,
    MonthlyTotals,
    OutcomeKind,
    UnitOutcome,
    preview_fingerprint,
    summarize,
)
from app.services.payments import _CONCEPT_ORDER, _check_invariants, _covered, _pending_charges, money, unit_balance
from app.services.structure import Actor, lock_unit_path

logger = logging.getLogger(__name__)

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


def _checked_amount(amount: Decimal) -> Decimal:
    if not isinstance(amount, Decimal) or not amount.is_finite():
        raise FieldValidationError("amount", "El monto no es válido.")
    if amount <= 0:
        raise FieldValidationError("amount", "El monto debe ser mayor que cero.")
    if amount != amount.quantize(CENT):
        raise FieldValidationError("amount", "El monto admite hasta 2 decimales.")
    if amount > MONEY_MAX:
        raise FieldValidationError("amount", f"El monto no puede superar {format_cop(MONEY_MAX)}.")
    return amount


def _check_monthly_dates(period: date | None, due_date: date) -> None:
    """A monthly fee: period = first day of a month (any month, past or future); due on or after it."""
    if period is None:
        raise FieldValidationError("billing_period", "El periodo es obligatorio para la mensualidad.")
    if not _is_date(period) or period.day != 1:
        raise FieldValidationError("billing_period", "El periodo no es válido.")
    if due_date < period:
        raise FieldValidationError("due_date", "El vencimiento no puede ser anterior al inicio del periodo.")


def _validated(data: ChargeInput) -> ChargeInput:
    """Format and per-concept rules; the database enforces the same ones (CHECK)."""
    if not isinstance(data.unit_id, int) or isinstance(data.unit_id, bool) or data.unit_id <= 0:
        raise FieldValidationError("unit_id", UNIT_MISSING_MESSAGE)
    try:
        concept = FinancialConcept(data.concept)
    except ValueError:
        raise FieldValidationError("concept", "Selecciona un concepto válido.") from None
    amount = _checked_amount(data.amount)
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
        _check_monthly_dates(period, data.due_date)
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


def _audit_context(charge: Charge, label: str, origin: str | None) -> dict[str, Any]:
    context: dict[str, Any] = {
        "concept": str(charge.concept),
        "amount": str(money(charge.amount)),
        "due_date": charge.due_date.isoformat(),
        "billing_period": charge.billing_period.isoformat() if charge.billing_period else None,
        "unit_id": charge.unit_id,
        "label": label,
        "source_charge_id": charge.source_charge_id,
    }
    if origin is not None:
        context["origin"] = origin
    return context


async def create_charge(db: AsyncSession, data: ChargeInput, actor: Actor) -> CreatedCharge:
    """Creates one obligation of an apartment and applies the apartment's credit to its pending charges."""
    data = _validated(data)

    # 1. Actor and apartment (no locks).
    await db.commit()
    await _check_actor(db, actor)
    tower_id, block_id = await _unit_parents(db, data.unit_id)
    await db.commit()

    # 2. One transaction of the apartment.
    return await _create_locked(db, data, actor, tower_id=tower_id, block_id=block_id)


async def _create_locked(
    db: AsyncSession, data: ChargeInput, actor: Actor, *, tower_id: int, block_id: int, origin: str | None = None
) -> CreatedCharge:
    """One validated obligation in its own transaction, from the apartment lock to COMMIT.

    The caller has validated the data and the actor, and committed its earlier
    reads, so the first statement here is a locking read (fresh view after it).
    origin is internal (audit only), never a client value. Any error rolls the
    transaction back: no charge, allocation or audit entry is left behind.
    """
    try:
        try:
            block, tower, unit = await lock_unit_path(db, data.unit_id, tower_id=tower_id, block_id=block_id)
        except UnitNotFoundError:
            raise FieldValidationError("unit_id", UNIT_DELETED_MESSAGE) from None
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
            context=_audit_context(charge, label, origin),
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


# --- Monthly generation ----------------------------------------------------------------------------------

GENERATION_ORIGIN = "MONTHLY_GENERATION"
# Deadlock / lock wait timeout: the apartment is retried once, in a new transaction.
RETRYABLE_MYSQL_ERRORS = (1213, 1205)
UNIT_FAILED_MESSAGE = "No fue posible generar la mensualidad de este apartamento."


@dataclass(frozen=True)
class MonthlyGenerationInput:
    params: MonthlyParams
    expected_to_create: int  # what the preview showed
    preview_fingerprint: str


@dataclass(frozen=True)
class MonthlyPreview:
    billing_period: date
    amount: Decimal
    due_date: date
    active_units: int
    existing: int
    missing: int
    to_create: int
    total_amount: Decimal
    existing_with_other_amount: int  # monthly fees of the period already there with another amount
    preview_fingerprint: str


@dataclass(frozen=True)
class MonthlyGenerationResult:
    billing_period: date
    amount: Decimal
    due_date: date
    totals: MonthlyTotals
    audit_recorded: bool


@dataclass(frozen=True)
class _ActiveUnit:
    id: int
    tower_id: int
    block_id: int
    label: str
    existing_charge_id: int | None
    existing_amount: Decimal | None


def monthly_params(billing_period: str, amount: Decimal, due_date: date) -> MonthlyParams:
    """Parameters of a run from the external values: billing_period is "YYYY-MM" (any month,
    past, current or future); the due date is on or after the first day of that month."""
    try:
        period = parse_period(billing_period)
    except ValueError:
        raise FieldValidationError("billing_period", "El periodo debe tener el formato AAAA-MM.") from None
    return _validated_params(MonthlyParams(period, amount, due_date))


def _validated_params(params: MonthlyParams) -> MonthlyParams:
    amount = _checked_amount(params.amount)
    if not _is_date(params.due_date):
        raise FieldValidationError("due_date", "La fecha de vencimiento no es válida.")
    _check_monthly_dates(params.billing_period, params.due_date)
    return MonthlyParams(params.billing_period, amount, params.due_date)


async def _composition(db: AsyncSession, period: date) -> list[_ActiveUnit]:
    """Every active apartment now (no fixed number) and its monthly fee of the period, if any.

    No locks. One row per apartment: the LEFT JOIN uses
    uq_charges_unit_id_billing_period (one lookup per apartment).
    """
    rows = (
        await db.execute(
            select(
                Unit.id,
                Unit.tower_id,
                Tower.block_id,
                Block.number.label("block_number"),
                Tower.number.label("tower_number"),
                Unit.number.label("unit_number"),
                Charge.id.label("charge_id"),
                Charge.amount.label("charge_amount"),
            )
            .join(Tower, Tower.id == Unit.tower_id)
            .join(Block, Block.id == Tower.block_id)
            .outerjoin(Charge, and_(Charge.unit_id == Unit.id, Charge.billing_period == period))
            .where(Unit.deleted_at.is_(None))
            .order_by(Block.number, Tower.number, Unit.number, Unit.id)
        )
    ).all()
    return [
        _ActiveUnit(row.id, row.tower_id, row.block_id, unit_label(row.block_number, row.tower_number, row.unit_number),
                    row.charge_id, row.charge_amount)
        for row in rows
    ]


def _preview(params: MonthlyParams, units: list[_ActiveUnit]) -> MonthlyPreview:
    existing = {u.id: u.existing_charge_id for u in units if u.existing_charge_id is not None}
    missing = len(units) - len(existing)
    return MonthlyPreview(
        billing_period=params.billing_period,
        amount=money(params.amount),
        due_date=params.due_date,
        active_units=len(units),
        existing=len(existing),
        missing=missing,
        to_create=missing,
        total_amount=money(params.amount * missing),
        existing_with_other_amount=sum(
            1 for u in units if u.existing_amount is not None and money(u.existing_amount) != money(params.amount)
        ),
        preview_fingerprint=preview_fingerprint(params, [u.id for u in units], list(existing)),
    )


async def preview_monthly(db: AsyncSession, params: MonthlyParams) -> MonthlyPreview:
    """What a monthly generation would do now. Read only: no locks, no writes, no audit."""
    params = _validated_params(params)
    await db.commit()  # fresh view of the latest committed data
    return _preview(params, await _composition(db, params.billing_period))


def _retryable(error: Exception) -> bool:
    return isinstance(error, DBAPIError) and getattr(error.orig, "args", (None,))[0] in RETRYABLE_MYSQL_ERRORS


async def _generate_for_unit(db: AsyncSession, unit: _ActiveUnit, params: MonthlyParams, actor: Actor) -> UnitOutcome:
    """The monthly fee of one apartment in its own transaction (the core of create_charge).

    A failure rolls back only this apartment. A deadlock or lock wait timeout is
    retried once, after the rollback, in a new transaction (never the aborted one).
    """
    data = ChargeInput(unit.id, FinancialConcept.MONTHLY_FEE, params.amount, params.due_date, None, params.billing_period)
    for attempt in (1, 2):
        try:
            created = await _create_locked(
                db, data, actor, tower_id=unit.tower_id, block_id=unit.block_id, origin=GENERATION_ORIGIN
            )
        except MonthlyFeeExistsError:
            return UnitOutcome(unit.id, unit.label, OutcomeKind.EXISTING)  # created meanwhile
        except FieldValidationError as exc:
            if exc.details and exc.details[0]["field"] == "unit_id":
                return UnitOutcome(unit.id, unit.label, OutcomeKind.OMITTED, "UNIT_DELETED", exc.details[0]["message"])
            return _failed(unit, params, exc, "VALIDATION_ERROR")
        except Exception as exc:
            # _create_locked already rolled back; make sure the aborted transaction is gone
            # before anything else (a failed rollback discards the connection).
            with suppress(Exception):
                await db.rollback()
            if attempt == 1 and _retryable(exc):
                continue
            return _failed(unit, params, exc, "LOCK_CONFLICT" if _retryable(exc) else "INTERNAL_ERROR")
        return UnitOutcome(
            unit.id,
            created.unit_label,
            OutcomeKind.CREATED,
            amount=created.amount,
            credit_to_new_charge=created.applied_amount,
            credit_total=sum((c.amount for c in created.credit_applied), Decimal("0.00")),
            outstanding=created.outstanding_amount,
        )
    raise AssertionError("unreachable")


def _failed(unit: _ActiveUnit, params: MonthlyParams, error: Exception, code: str) -> UnitOutcome:
    # Only the error type is logged: the response never carries internal details.
    logger.error("Monthly fee not generated (unit_id=%s, period=%s, error=%s)",
                 unit.id, params.billing_period.isoformat(), type(error).__name__)
    return UnitOutcome(unit.id, unit.label, OutcomeKind.FAILED, code, UNIT_FAILED_MESSAGE)


async def _record_generation(db: AsyncSession, params: MonthlyParams, totals: MonthlyTotals, actor: Actor) -> bool:
    """MONTHLY_CHARGES_GENERATED in its own transaction; a failure never undoes the fees already committed."""
    try:
        record_audit(
            db,
            action=AuditAction.MONTHLY_CHARGES_GENERATED,
            entity_type="billing_period",
            actor_user_id=actor.user_id,
            context={
                "billing_period": params.billing_period.isoformat(),
                "amount": str(money(params.amount)),
                "due_date": params.due_date.isoformat(),
                "active_units": totals.units_evaluated,
                "created": totals.created,
                "existing": totals.existing,
                "omitted": totals.omitted,
                "failed": totals.failed,
                "generated_total": str(money(totals.generated_total)),
                "credit_applied_total": str(money(totals.credit_applied_total)),
                "omitted_unit_ids": [issue.unit_id for issue in totals.omitted_units],
                "error_unit_ids": [issue.unit_id for issue in totals.errors],
            },
            ip_address=actor.ip_address,
        )
        await db.commit()
        return True
    except Exception as exc:
        await db.rollback()
        logger.error("Monthly generation summary not recorded (period=%s, error=%s)",
                     params.billing_period.isoformat(), type(exc).__name__)
        return False


async def generate_monthly(db: AsyncSession, data: MonthlyGenerationInput, actor: Actor) -> MonthlyGenerationResult:
    """Monthly fee of the period for every active apartment that does not have it.

    1. Validates, checks the actor and reads the apartments again (no locks).
    2. If the count or the composition changed since the preview: nothing is
       created (MONTHLY_PREVIEW_OUTDATED, with the current preview).
    3. One transaction per missing apartment; an error affects only that
       apartment and the run continues with the others.
    4. MONTHLY_CHARGES_GENERATED with the totals.
    """
    params = _validated_params(data.params)
    if not isinstance(data.expected_to_create, int) or isinstance(data.expected_to_create, bool) \
            or data.expected_to_create < 0:
        raise FieldValidationError("expected_to_create", "La cantidad esperada no es válida.")
    if not isinstance(data.preview_fingerprint, str) or not data.preview_fingerprint:
        raise FieldValidationError("preview_fingerprint", "Revisa la vista previa antes de confirmar.")

    await db.commit()
    await _check_actor(db, actor)
    units = await _composition(db, params.billing_period)
    await db.commit()
    preview = _preview(params, units)
    if preview.to_create != data.expected_to_create or preview.preview_fingerprint != data.preview_fingerprint:
        raise MonthlyPreviewOutdatedError(preview)

    outcomes: list[UnitOutcome] = []
    for unit in units:
        if unit.existing_charge_id is not None:
            outcomes.append(UnitOutcome(unit.id, unit.label, OutcomeKind.EXISTING))
        else:
            outcomes.append(await _generate_for_unit(db, unit, params, actor))

    totals = summarize(outcomes)
    recorded = await _record_generation(db, params, totals, actor)
    return MonthlyGenerationResult(params.billing_period, money(params.amount), params.due_date, totals, recorded)


# --- List and detail (read only) -------------------------------------------------------------------------
#
# Plain consistent reads: no locks and no writes. applied = SUM(payment_allocations.amount) of the
# charge (the correlated subquery of HU-004), outstanding = amount - applied, and the status is
# computed in SQL with the same rules as charge_status (PAID > OVERDUE > PARTIAL > PENDING) for the
# "today" given by the caller (Bogotá). Nothing is stored.

LIST_PAGE_SIZE_DEFAULT = 10
LIST_PAGE_SIZE_MAX = 100
LIST_PAGE_MAX = 10_000
_ID_MAX = 2**63 - 1


class ChargeSortField(StrEnum):
    DUE_DATE = "due_date"
    UNIT = "unit"
    CONCEPT = "concept"
    AMOUNT = "amount"
    OUTSTANDING = "outstanding"
    STATUS = "status"
    CREATED = "created"


@dataclass(frozen=True)
class ChargeListQuery:
    page: int = 1
    page_size: int = LIST_PAGE_SIZE_DEFAULT
    unit_id: int | None = None
    block_id: int | None = None
    tower_id: int | None = None
    concept: FinancialConcept | None = None
    status: ChargeStatus | None = None
    billing_period: date | None = None  # first day of the month
    due_from: date | None = None  # inclusive
    due_to: date | None = None  # inclusive
    sort_by: ChargeSortField = ChargeSortField.DUE_DATE
    sort_order: SortOrder = SortOrder.ASC


@dataclass(frozen=True)
class ChargeUnit:
    id: int
    label: str  # current label, derived from the structure (HU-013)
    deleted: bool


@dataclass(frozen=True)
class ChargeSummary:
    id: int
    unit: ChargeUnit
    concept: FinancialConcept
    description: str | None
    billing_period: date | None
    due_date: date
    amount: Decimal
    applied_amount: Decimal
    outstanding_amount: Decimal
    status: ChargeStatus
    source_charge_id: int | None
    created_at: datetime  # UTC


@dataclass(frozen=True)
class ChargePage:
    items: tuple[ChargeSummary, ...]
    page: int
    page_size: int
    total: int
    total_pages: int


@dataclass(frozen=True)
class ChargeAllocation:
    payment_id: int
    receipt_number: str
    payment_date: date
    payment_method: PaymentMethod
    applied_amount: Decimal
    allocation_date: datetime  # UTC (payment_allocations.created_at)


@dataclass(frozen=True)
class ChargeDetail:
    charge: ChargeSummary
    created_by_user_id: int
    source: ChargeSummary | None  # the obligation that originated this late fee
    late_fees: tuple[ChargeSummary, ...]  # every LATE_FEE whose origin is this charge
    allocations: tuple[ChargeAllocation, ...]


# Spanish labels in alphabetical order: Pagada, Parcial, Pendiente, Vencida.
_STATUS_ORDER = {ChargeStatus.PAID: 1, ChargeStatus.PARTIAL: 2, ChargeStatus.PENDING: 3, ChargeStatus.OVERDUE: 4}


def _positive_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _ID_MAX


def _validated_query(query: ChargeListQuery, today: date) -> ChargeListQuery:
    """The service never trusts its caller: the future API validates too, with the same rules."""
    if not _is_date(today):
        raise ValueError("today is a calendar date (Bogotá).")
    for field, value in (("page", query.page), ("page_size", query.page_size)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise FieldValidationError(field, "El valor no es válido.")
    if not 1 <= query.page <= LIST_PAGE_MAX:
        raise FieldValidationError("page", f"La página debe estar entre 1 y {LIST_PAGE_MAX}.")
    if not 1 <= query.page_size <= LIST_PAGE_SIZE_MAX:
        raise FieldValidationError("page_size", f"El tamaño de página debe estar entre 1 y {LIST_PAGE_SIZE_MAX}.")
    for field, value in (("unit_id", query.unit_id), ("block_id", query.block_id), ("tower_id", query.tower_id)):
        if value is not None and not _positive_id(value):
            raise FieldValidationError(field, "El identificador no es válido.")
    try:
        concept = None if query.concept is None else FinancialConcept(query.concept)
    except ValueError:
        raise FieldValidationError("concept", "Selecciona un concepto válido.") from None
    try:
        status = None if query.status is None else ChargeStatus(query.status)
    except ValueError:
        raise FieldValidationError("status", "Selecciona un estado válido.") from None
    if query.billing_period is not None and (not _is_date(query.billing_period) or query.billing_period.day != 1):
        raise FieldValidationError("billing_period", "El periodo no es válido.")
    for field, value in (("due_from", query.due_from), ("due_to", query.due_to)):
        if value is not None and not _is_date(value):
            raise FieldValidationError(field, "La fecha no es válida.")
    if query.due_from is not None and query.due_to is not None and query.due_from > query.due_to:
        raise FieldValidationError("due_to", "La fecha final no puede ser anterior a la inicial.")
    try:
        sort_by = ChargeSortField(query.sort_by)
    except ValueError:
        raise FieldValidationError("sort_by", "El campo de orden no es válido.") from None
    try:
        sort_order = SortOrder(query.sort_order)
    except ValueError:
        raise FieldValidationError("sort_order", "El sentido del orden no es válido.") from None
    return ChargeListQuery(query.page, query.page_size, query.unit_id, query.block_id, query.tower_id, concept, status,
                           query.billing_period, query.due_from, query.due_to, sort_by, sort_order)


def _status_expression(applied: Any, today: date) -> Any:
    """charge_status in SQL: PAID > OVERDUE (due date already ended) > PARTIAL > PENDING."""
    return case(
        (applied >= Charge.amount, literal(ChargeStatus.PAID.value)),
        (Charge.due_date < today, literal(ChargeStatus.OVERDUE.value)),
        (applied > 0, literal(ChargeStatus.PARTIAL.value)),
        else_=literal(ChargeStatus.PENDING.value),
    )


def _charge_columns(today: date) -> tuple[Select[Any], Any, Any]:
    """One row per charge with its apartment, applied amount and status (joins, no N+1)."""
    applied = _covered()
    status = _status_expression(applied, today)
    statement = (
        select(
            Charge.id,
            Charge.unit_id,
            Unit.deleted_at.label("unit_deleted_at"),
            Block.number.label("block_number"),
            Tower.number.label("tower_number"),
            Unit.number.label("unit_number"),
            Charge.concept,
            Charge.description,
            Charge.billing_period,
            Charge.due_date,
            Charge.amount,
            applied.label("applied"),
            status.label("status"),
            Charge.source_charge_id,
            Charge.created_by_user_id,
            Charge.created_at,
        )
        .select_from(Charge)
        .join(Unit, Unit.id == Charge.unit_id)
        .join(Tower, Tower.id == Unit.tower_id)
        .join(Block, Block.id == Tower.block_id)
    )
    return statement, applied, status


def _summary(row: Any) -> ChargeSummary:
    amount, applied = money(row.amount), money(row.applied)
    return ChargeSummary(
        id=row.id,
        unit=ChargeUnit(row.unit_id, unit_label(row.block_number, row.tower_number, row.unit_number),
                        row.unit_deleted_at is not None),
        concept=FinancialConcept(row.concept),
        description=row.description,
        billing_period=row.billing_period,
        due_date=row.due_date,
        amount=amount,
        applied_amount=applied,
        outstanding_amount=amount - applied,
        status=ChargeStatus(row.status),
        source_charge_id=row.source_charge_id,
        created_at=row.created_at,
    )


def _conditions(query: ChargeListQuery, status: Any) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if query.unit_id is not None:
        conditions.append(Charge.unit_id == query.unit_id)
    if query.tower_id is not None:
        conditions.append(Unit.tower_id == query.tower_id)
    if query.block_id is not None:
        conditions.append(Tower.block_id == query.block_id)
    if query.concept is not None:
        conditions.append(Charge.concept == query.concept.value)
    if query.status is not None:
        conditions.append(status == query.status.value)
    if query.billing_period is not None:
        conditions.append(Charge.billing_period == query.billing_period)
    if query.due_from is not None:
        conditions.append(Charge.due_date >= query.due_from)
    if query.due_to is not None:
        conditions.append(Charge.due_date <= query.due_to)
    return conditions


def _sort_keys(field: ChargeSortField, applied: Any, status: Any) -> tuple[Any, ...]:
    # Never built from the client's text: each whitelisted value maps to expressions.
    match field:
        case ChargeSortField.DUE_DATE:
            return (Charge.due_date,)
        case ChargeSortField.UNIT:
            return (Block.number, Tower.number, Unit.number)
        case ChargeSortField.CONCEPT:  # by its Spanish name, as HU-004
            return (case(*((Charge.concept == k.value, v) for k, v in _CONCEPT_ORDER.items())),)
        case ChargeSortField.AMOUNT:
            return (Charge.amount,)
        case ChargeSortField.OUTSTANDING:
            return (Charge.amount - applied,)
        case ChargeSortField.STATUS:  # by its Spanish name
            return (case(*((status == k.value, v) for k, v in _STATUS_ORDER.items())),)
        case ChargeSortField.CREATED:
            return (Charge.created_at,)


async def list_charges(db: AsyncSession, query: ChargeListQuery, today: date) -> ChargePage:
    """Paginated obligations: one COUNT and one page (ORDER BY, LIMIT, OFFSET in MySQL)."""
    query = _validated_query(query, today)
    statement, applied, status = _charge_columns(today)
    conditions = _conditions(query, status)
    counted = (
        select(func.count(Charge.id))
        .select_from(Charge)
        .join(Unit, Unit.id == Charge.unit_id)
        .join(Tower, Tower.id == Unit.tower_id)
        .where(*conditions)
    )
    total = int(await db.scalar(counted) or 0)
    total_pages = math.ceil(total / query.page_size)
    items: tuple[ChargeSummary, ...] = ()
    if query.page <= total_pages:
        descending = query.sort_order == SortOrder.DESC
        # charges.id breaks ties in the same direction: a stable order across pages.
        keys = [*_sort_keys(query.sort_by, applied, status), Charge.id]
        rows = (
            await db.execute(
                statement.where(*conditions)
                .order_by(*(key.desc() if descending else key.asc() for key in keys))
                .limit(query.page_size)
                .offset((query.page - 1) * query.page_size)
            )
        ).all()
        items = tuple(_summary(row) for row in rows)
    return ChargePage(items=items, page=query.page, page_size=query.page_size, total=total, total_pages=total_pages)


async def list_outstanding_charges(db: AsyncSession, unit_id: int, today: date) -> tuple[ChargeSummary, ...]:
    """Every obligation of one apartment not fully covered yet, in FIFO order (due_date, created_at, id).

    The same columns, applied amount and status as list_charges (consistent reads, no locks): a
    snapshot for display only. Whoever pays checks the balance again under the apartment lock.
    """
    if not _is_date(today):
        raise ValueError("today is a calendar date (Bogotá).")
    if not _positive_id(unit_id):
        raise ValueError("unit_id is an apartment (a positive id).")
    statement, applied, _ = _charge_columns(today)
    rows = (
        await db.execute(
            statement.where(Charge.unit_id == unit_id, applied < Charge.amount)
            .order_by(Charge.due_date, Charge.created_at, Charge.id)
        )
    ).all()
    return tuple(_summary(row) for row in rows)


async def get_charge(db: AsyncSession, charge_id: int, today: date) -> ChargeDetail:
    """One obligation with its apartment, origin, derived late fees and allocations (4 queries at most)."""
    if not _is_date(today):
        raise ValueError("today is a calendar date (Bogotá).")
    if not _positive_id(charge_id):
        raise ChargeNotFoundError()
    statement, _, _ = _charge_columns(today)
    row = (await db.execute(statement.where(Charge.id == charge_id))).first()
    if row is None:
        raise ChargeNotFoundError()
    source = None
    if row.source_charge_id is not None:
        source_row = (await db.execute(statement.where(Charge.id == row.source_charge_id))).first()
        source = _summary(source_row) if source_row is not None else None
    # Every late fee that names this charge as its origin (the query does not assume there is one).
    late_fees = tuple(
        _summary(r) for r in (await db.execute(
            statement.where(Charge.source_charge_id == charge_id).order_by(Charge.due_date, Charge.created_at, Charge.id)
        )).all()
    )
    allocations = tuple(
        ChargeAllocation(
            payment_id=r.payment_id,
            receipt_number=receipt_number(r.payment_id),
            payment_date=r.payment_date,
            payment_method=PaymentMethod(r.method),
            applied_amount=money(r.amount),
            allocation_date=r.created_at,
        )
        for r in (await db.execute(
            select(PaymentAllocation.payment_id, Payment.payment_date, Payment.method, PaymentAllocation.amount,
                   PaymentAllocation.created_at)
            .join(Payment, Payment.id == PaymentAllocation.payment_id)
            .where(PaymentAllocation.charge_id == charge_id)
            .order_by(PaymentAllocation.created_at, PaymentAllocation.id)
        )).all()
    )
    return ChargeDetail(charge=_summary(row), created_by_user_id=row.created_by_user_id, source=source,
                        late_fees=late_fees, allocations=allocations)
