"""Demo payment transactions of the resident (HU-010): the internal demo gateway, no real money.

The resident is always the caller's resident_id (the future API takes it from
the session, never from the client). The apartment is the selected charge's
and must be the resident's current residence. The amount is validated against
the charge's outstanding balance; the reference, the status and the dates are
decided here. Completing never takes an amount, a reference or a charge again:
everything comes from the stored transaction.

start     validate (no database) · charge, its apartment and the residence (no locks), COMMIT
          BEGIN
            lock block (S) → tower (S) → apartment (X)      same order as HU-004, HU-013 and HU-019
            current residence of the apartment (S), resident (S)
            the selected charge (X) and its outstanding balance
            INITIATED transactions of the charge: an expired one → EXPIRED; a live one → 409
            transaction INITIATED (expires 15 minutes after created_at) + DEMO_PAYMENT_STARTED
          COMMIT
complete  outcome APPROVED | REJECTED | FAILED (the simulated answer, validated)
          the transaction and its apartment (no locks), COMMIT
          BEGIN
            lock block (S) → tower (S) → apartment (X) → the transaction (X)
            already finished: the same outcome returns it unchanged, any other is a 409
            expired: EXPIRED, COMMIT, 409
            REJECTED / FAILED: status, finished_at, audit (no payment, no allocation)
            APPROVED: residence (S), resident (S), the charge (X) and its outstanding balance
              still payable: payment (DEMO_GATEWAY) + ONE allocation to the selected charge,
                             invariants, APPROVED (payment_id, finished_at), audit
              not payable:   FAILED, audit, COMMIT, 409 (no payment)
          COMMIT
cancel    the same locks: INITIATED → CANCELLED; CANCELLED again returns it unchanged
get       a read; an INITIATED transaction past its expiry is first made EXPIRED (same locks)
payable   read only (no locks, nothing persisted): the obligations of the resident's current
          apartment with an outstanding balance (charges.list_outstanding_charges) and the
          resident's own live transaction of each; start checks everything again

Every write to the charges, payments, allocations or demo transactions of an
apartment holds the apartment lock (X), so operations on the same apartment run
one after the other: two starts never leave two live transactions of a charge
and two completions never create two payments (payment_id is also UNIQUE).
After the lock, sums are consistent reads, never FOR SHARE (see
payments._covered: locking reads of allocations deadlocked apartments). Any
error rolls everything back; only an expiry or an approval that can no longer
be applied is committed (EXPIRED / FAILED) before its error. No global FIFO:
the approved amount goes to the selected charge only. No email (HU-017).
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import bogota_today, utc_now
from app.core.exceptions import (
    ChargeNotFoundError,
    ChargeNotPayableError,
    DemoPaymentInProgressError,
    DemoPaymentNotAppliedError,
    DemoTransactionExpiredError,
    DemoTransactionFinishedError,
    DemoTransactionNotFoundError,
    FieldValidationError,
    ForbiddenError,
    UnitNotFoundError,
)
from app.core.money import CENT, MONEY_MAX, format_cop
from app.core.unit_labels import unit_label
from app.models import (
    AuditAction,
    Charge,
    DemoPaymentTransaction,
    DemoPaymentTransactionStatus,
    FinancialConcept,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    PaymentSource,
    ReceiptEmailStatus,
    ResidentUnit,
    Tower,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.schemas.invitation import ID_MAX
from app.services.allocation import receipt_number
from app.services.audit import record_audit
from app.services.charges import ChargeSummary, list_outstanding_charges
from app.services.payments import money
from app.services.structure import lock_unit_path

DEMO_TRANSACTION_TTL = timedelta(minutes=15)
REFERENCE_PREFIX = "DEMO-"
ENTITY = "demo_payment_transaction"

Status = DemoPaymentTransactionStatus


class DemoOutcome(StrEnum):
    """The answers the demo gateway can give when a transaction is completed."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class FailureReason(StrEnum):
    """Why a transaction ended FAILED (audit only; the status is the same)."""

    SIMULATED = "SIMULATED"  # the demo gateway answered FAILED
    OUTSTANDING_CHANGED = "OUTSTANDING_CHANGED"  # the charge no longer accepts the whole amount
    NOT_ELIGIBLE = "NOT_ELIGIBLE"  # the resident no longer lives there or the account is not active


_END_ACTIONS = {
    Status.REJECTED: AuditAction.DEMO_PAYMENT_REJECTED,
    Status.FAILED: AuditAction.DEMO_PAYMENT_FAILED,
    Status.CANCELLED: AuditAction.DEMO_PAYMENT_CANCELLED,
}


@dataclass(frozen=True)
class DemoPaymentTransactionResult:
    id: int
    reference: str
    charge_id: int
    amount: Decimal
    status: DemoPaymentTransactionStatus
    created_at: datetime  # UTC
    expires_at: datetime  # UTC
    finished_at: datetime | None  # UTC; None while INITIATED
    payment_id: int | None  # APPROVED only
    receipt_number: str | None  # APPROVED only (derived from payment_id)


@dataclass(frozen=True)
class _Path:
    unit_id: int
    tower_id: int
    block_id: int


# --- Validation (no database) ----------------------------------------------------------------------------


def _positive_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= ID_MAX


def _check_resident_id(resident_id: Any) -> None:
    if not _positive_id(resident_id):
        raise ValueError("resident_id is the signed-in resident (a positive id).")


def _checked_amount(amount: Any) -> Decimal:
    """An exact Decimal: positive, at most 2 decimals and within DECIMAL(12,2). Never a float."""
    if not isinstance(amount, Decimal) or not amount.is_finite():
        raise FieldValidationError("amount", "El monto no es válido.")
    if amount <= 0:
        raise FieldValidationError("amount", "El monto debe ser mayor que cero.")
    if amount > MONEY_MAX:
        raise FieldValidationError("amount", f"El monto no puede superar {format_cop(MONEY_MAX)}.")
    if amount != amount.quantize(CENT):
        raise FieldValidationError("amount", "El monto admite hasta 2 decimales.")
    return amount.quantize(CENT)


def _checked_outcome(outcome: Any) -> DemoOutcome:
    try:
        return DemoOutcome(outcome)
    except ValueError:
        raise FieldValidationError("outcome", "Selecciona un resultado válido de la pasarela de demostración.") from None


def new_reference() -> str:
    """The demo reference: DEMO- and 16 random hexadecimal digits (never a real provider's)."""
    return f"{REFERENCE_PREFIX}{secrets.token_hex(8).upper()}"


def is_expired(transaction: DemoPaymentTransaction, now: datetime) -> bool:
    """Expired from the very instant expires_at is reached."""
    return now >= transaction.expires_at


def _is_active_resident(user: User | None) -> bool:
    return (
        user is not None
        and user.role == UserRole.RESIDENTE
        and user.status == UserStatus.ACTIVE
        and user.deleted_at is None
    )


def _result(transaction: DemoPaymentTransaction) -> DemoPaymentTransactionResult:
    return DemoPaymentTransactionResult(
        id=transaction.id,
        reference=transaction.reference,
        charge_id=transaction.charge_id,
        amount=money(transaction.amount),
        status=Status(transaction.status),
        created_at=transaction.created_at,
        expires_at=transaction.expires_at,
        finished_at=transaction.finished_at,
        payment_id=transaction.payment_id,
        receipt_number=receipt_number(transaction.payment_id) if transaction.payment_id is not None else None,
    )


# --- Reads without locks (before the transaction of the apartment) ----------------------------------------


async def _charge_path(db: AsyncSession, charge_id: int) -> _Path | None:
    """The apartment of a charge and its parents (they never change)."""
    row = (
        await db.execute(
            select(Charge.unit_id, Unit.tower_id, Tower.block_id)
            .join(Unit, Unit.id == Charge.unit_id)
            .join(Tower, Tower.id == Unit.tower_id)
            .where(Charge.id == charge_id)
        )
    ).first()
    return None if row is None else _Path(row.unit_id, row.tower_id, row.block_id)


async def _current_unit_id(db: AsyncSession, resident_id: int) -> int | None:
    return await db.scalar(
        select(ResidentUnit.unit_id).where(ResidentUnit.user_id == resident_id, ResidentUnit.ended_at.is_(None))
    )


# --- Locked reads (after the apartment lock) ---------------------------------------------------------------


async def _residence_user(db: AsyncSession, unit_id: int) -> int | None:
    """Who lives in the apartment now (S), the same locking read as HU-004."""
    residence = await db.scalar(
        select(ResidentUnit)
        .where(ResidentUnit.active_unit_marker == unit_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    return None if residence is None else residence.user_id


async def _lock_resident(db: AsyncSession, resident_id: int) -> User | None:
    return await db.scalar(
        select(User).where(User.id == resident_id).with_for_update(read=True).execution_options(populate_existing=True)
    )


async def _lock_charge(db: AsyncSession, charge_id: int) -> Charge | None:
    """The selected charge (X), as HU-004 locks the charges it pays."""
    return await db.scalar(
        select(Charge).where(Charge.id == charge_id).with_for_update().execution_options(populate_existing=True)
    )


async def _outstanding(db: AsyncSession, charge: Charge) -> Decimal:
    """Charge amount minus everything allocated to it (consistent read under the apartment lock)."""
    covered = await db.scalar(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0)).where(PaymentAllocation.charge_id == charge.id)
    )
    return money(charge.amount) - money(covered)


async def _lock_path(db: AsyncSession, path: _Path) -> str | None:
    """The apartment lock; its label, or None when the apartment was deleted (nothing can be paid there)."""
    try:
        block, tower, unit = await lock_unit_path(db, path.unit_id, tower_id=path.tower_id, block_id=path.block_id)
    except UnitNotFoundError:
        return None
    return unit_label(block.number, tower.number, unit.number)


# --- Transitions -----------------------------------------------------------------------------------------


def _audit(
    db: AsyncSession, action: AuditAction, transaction: DemoPaymentTransaction, actor_user_id: int | None,
    ip_address: str | None, **extra: Any,
) -> None:
    context: dict[str, Any] = {
        "reference": transaction.reference,
        "charge_id": transaction.charge_id,
        "amount": str(money(transaction.amount)),
    }
    record_audit(db, action=action, entity_type=ENTITY, entity_id=transaction.id, actor_user_id=actor_user_id,
                 context=context | extra, ip_address=ip_address)


def _expire(db: AsyncSession, transaction: DemoPaymentTransaction, ip_address: str | None) -> None:
    """INITIATED → EXPIRED. It ended when it expired; the actor is the clock (no user)."""
    transaction.status = Status.EXPIRED
    transaction.finished_at = transaction.expires_at
    _audit(db, AuditAction.DEMO_PAYMENT_EXPIRED, transaction, None, ip_address,
           expires_at=transaction.expires_at.isoformat())


def _end(
    db: AsyncSession, transaction: DemoPaymentTransaction, status: DemoPaymentTransactionStatus, now: datetime,
    ip_address: str | None, reason: FailureReason | None = None,
) -> None:
    """INITIATED → REJECTED, FAILED or CANCELLED: nothing financial changes."""
    transaction.status = status
    transaction.finished_at = now
    extra = {} if reason is None else {"reason": reason.value}
    _audit(db, _END_ACTIONS[status], transaction, transaction.resident_id, ip_address, **extra)


async def _check_applied(db: AsyncSession, payment: Payment, charge: Charge) -> None:
    """Defense in depth before COMMIT (consistent reads under the apartment lock): the payment is
    applied whole and only to its charge, and the charge is never covered beyond its amount."""
    to_payment = money(await db.scalar(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0)).where(PaymentAllocation.payment_id == payment.id)
    ))
    targets = (await db.scalars(
        select(PaymentAllocation.charge_id).where(PaymentAllocation.payment_id == payment.id)
    )).all()
    # Read again here (not through _outstanding): the guard must not share a stale read.
    to_charge = money(await db.scalar(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0)).where(PaymentAllocation.charge_id == charge.id)
    ))
    if to_payment != money(payment.amount) or list(targets) != [charge.id] or to_charge > money(charge.amount):
        await db.rollback()
        raise RuntimeError("Demo payment invariant violated; the transaction was rolled back.")


async def _approve(
    db: AsyncSession, transaction: DemoPaymentTransaction, unit_id: int, label: str | None, now: datetime,
    ip_address: str | None,
) -> FailureReason | None:
    """The payment and its single allocation, or the reason it can no longer be applied (nothing written)."""
    if label is None or await _residence_user(db, unit_id) != transaction.resident_id:
        return FailureReason.NOT_ELIGIBLE
    resident = await _lock_resident(db, transaction.resident_id)
    if resident is None or not _is_active_resident(resident):
        return FailureReason.NOT_ELIGIBLE
    charge = await _lock_charge(db, transaction.charge_id)
    if charge is None:  # never: a charge with transactions cannot be deleted (RESTRICT)
        return FailureReason.NOT_ELIGIBLE
    amount = money(transaction.amount)
    if amount > await _outstanding(db, charge):
        return FailureReason.OUTSTANDING_CHANGED

    concept = FinancialConcept(charge.concept)
    payment = Payment(
        resident_id=resident.id,
        unit_id=charge.unit_id,
        registered_by_user_id=resident.id,  # the resident paid; never attributed to the administrator
        amount=amount,
        method=PaymentMethod.DEMO_GATEWAY,
        concept=concept,
        concept_description=charge.description if concept == FinancialConcept.OTHER else None,
        source=PaymentSource.DEMO_GATEWAY,
        transaction_reference=transaction.reference,
        payment_date=bogota_today(now),
        registered_at=now,
        resident_name_snapshot=resident.full_name,
        unit_label_snapshot=label,
        receipt_email_status=ReceiptEmailStatus.PENDING,
    )
    db.add(payment)
    await db.flush()
    db.add(PaymentAllocation(payment_id=payment.id, charge_id=charge.id, amount=amount))
    await db.flush()
    await _check_applied(db, payment, charge)

    transaction.status = Status.APPROVED
    transaction.payment_id = payment.id
    transaction.finished_at = now
    await db.flush()
    number = receipt_number(payment.id)
    record_audit(
        db,
        action=AuditAction.PAYMENT_REGISTERED,
        entity_type="payment",
        entity_id=payment.id,
        actor_user_id=resident.id,
        context={
            "payment_id": payment.id,
            "amount": str(amount),
            "method": PaymentMethod.DEMO_GATEWAY.value,
            "concept": concept.value,
            "unit_id": charge.unit_id,
            "receipt_number": number,
            "source": PaymentSource.DEMO_GATEWAY.value,
            "demo_transaction_id": transaction.id,
        },
        ip_address=ip_address,
    )
    _audit(db, AuditAction.DEMO_PAYMENT_APPROVED, transaction, resident.id, ip_address,
           payment_id=payment.id, receipt_number=number)
    return None


# --- Start -------------------------------------------------------------------------------------------------


async def _settle_live(db: AsyncSession, charge_id: int, now: datetime, ip_address: str | None) -> None:
    """At most one live transaction per charge: an expired one becomes EXPIRED, a live one is a 409."""
    initiated = (await db.scalars(
        select(DemoPaymentTransaction)
        .where(DemoPaymentTransaction.charge_id == charge_id, DemoPaymentTransaction.status == Status.INITIATED)
        .order_by(DemoPaymentTransaction.id)
        .execution_options(populate_existing=True)
    )).all()
    for transaction in initiated:
        if not is_expired(transaction, now):
            raise DemoPaymentInProgressError()
        _expire(db, transaction, ip_address)
    await db.flush()


async def start_demo_payment(
    db: AsyncSession, resident_id: int, charge_id: int, amount: Decimal, *, ip_address: str | None = None
) -> DemoPaymentTransactionResult:
    """Starts a demo transaction of the resident for one charge of their current apartment."""
    _check_resident_id(resident_id)
    if not _positive_id(charge_id):
        raise ChargeNotFoundError()
    amount = _checked_amount(amount)

    # 1. Which apartment (no locks). Another apartment's charge looks exactly like a missing one.
    await db.commit()
    path = await _charge_path(db, charge_id)
    current = await _current_unit_id(db, resident_id)
    await db.commit()
    if path is None or current != path.unit_id:
        raise ChargeNotFoundError()

    # 2. One transaction of the apartment, whose first statement is a locking read.
    try:
        if await _lock_path(db, path) is None or await _residence_user(db, path.unit_id) != resident_id:
            raise ChargeNotFoundError()
        if not _is_active_resident(await _lock_resident(db, resident_id)):
            raise ForbiddenError()
        charge = await _lock_charge(db, charge_id)
        if charge is None:
            raise ChargeNotFoundError()
        outstanding = await _outstanding(db, charge)
        if outstanding <= 0:
            raise ChargeNotPayableError()
        if amount > outstanding:
            raise FieldValidationError(
                "amount", f"El monto no puede superar el saldo pendiente de la obligación ({format_cop(outstanding)})."
            )
        now = utc_now()
        await _settle_live(db, charge_id, now, ip_address)
        transaction = DemoPaymentTransaction(
            resident_id=resident_id,
            charge_id=charge_id,
            amount=amount,
            reference=new_reference(),
            status=Status.INITIATED,
            created_at=now,
            expires_at=now + DEMO_TRANSACTION_TTL,
        )
        db.add(transaction)
        await db.flush()
        _audit(db, AuditAction.DEMO_PAYMENT_STARTED, transaction, resident_id, ip_address,
               unit_id=path.unit_id, expires_at=transaction.expires_at.isoformat())
        result = _result(transaction)
        await db.commit()
    except Exception:
        # Nothing is left behind: no transaction or audit entry.
        await db.rollback()
        raise
    return result


# --- Complete, cancel, get ---------------------------------------------------------------------------------


async def _transaction_path(db: AsyncSession, resident_id: int, transaction_id: Any) -> _Path:
    """The apartment of the resident's transaction (no locks). Another resident's looks like a missing one."""
    if not _positive_id(transaction_id):
        raise DemoTransactionNotFoundError()
    await db.commit()
    row = (
        await db.execute(
            select(Charge.unit_id, Unit.tower_id, Tower.block_id)
            .select_from(DemoPaymentTransaction)
            .join(Charge, Charge.id == DemoPaymentTransaction.charge_id)
            .join(Unit, Unit.id == Charge.unit_id)
            .join(Tower, Tower.id == Unit.tower_id)
            .where(DemoPaymentTransaction.id == transaction_id, DemoPaymentTransaction.resident_id == resident_id)
        )
    ).first()
    await db.commit()
    if row is None:
        raise DemoTransactionNotFoundError()
    return _Path(row.unit_id, row.tower_id, row.block_id)


async def _lock_transaction(db: AsyncSession, resident_id: int, transaction_id: int) -> DemoPaymentTransaction:
    transaction = await db.scalar(
        select(DemoPaymentTransaction)
        .where(DemoPaymentTransaction.id == transaction_id, DemoPaymentTransaction.resident_id == resident_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if transaction is None:
        raise DemoTransactionNotFoundError()
    return transaction


async def _finish(
    db: AsyncSession, resident_id: int, transaction_id: Any, target: DemoPaymentTransactionStatus,
    ip_address: str | None,
) -> DemoPaymentTransactionResult:
    _check_resident_id(resident_id)
    path = await _transaction_path(db, resident_id, transaction_id)
    try:
        label = await _lock_path(db, path)
        transaction = await _lock_transaction(db, resident_id, transaction_id)
        if transaction.status != Status.INITIATED:
            # Finished: nothing changes, whatever is asked.
            result = _result(transaction)
            await db.commit()
            if result.status == target:
                return result
            if result.status == Status.EXPIRED:
                raise DemoTransactionExpiredError()
            raise DemoTransactionFinishedError()
        now = utc_now()
        if is_expired(transaction, now):
            _expire(db, transaction, ip_address)
            await db.commit()
            raise DemoTransactionExpiredError()
        if target == Status.APPROVED:
            reason = await _approve(db, transaction, path.unit_id, label, now, ip_address)
            if reason is not None:
                # The gateway approved, but the payment can no longer be applied: FAILED, account unchanged.
                _end(db, transaction, Status.FAILED, now, ip_address, reason)
                await db.commit()
                raise DemoPaymentNotAppliedError()
        else:
            _end(db, transaction, target, now, ip_address,
                 FailureReason.SIMULATED if target == Status.FAILED else None)
        await db.flush()
        result = _result(transaction)
        await db.commit()
    except Exception:
        # A real error leaves nothing behind (an expiry or FAILED committed above stays).
        await db.rollback()
        raise
    return result


async def complete_demo_payment(
    db: AsyncSession, resident_id: int, transaction_id: int, outcome: DemoOutcome | str, *,
    ip_address: str | None = None,
) -> DemoPaymentTransactionResult:
    """Applies the demo gateway's answer to the resident's transaction (at most one payment, ever)."""
    target = Status(_checked_outcome(outcome).value)
    return await _finish(db, resident_id, transaction_id, target, ip_address)


async def cancel_demo_payment(
    db: AsyncSession, resident_id: int, transaction_id: int, *, ip_address: str | None = None
) -> DemoPaymentTransactionResult:
    """The resident gives up before completing: CANCELLED, nothing financial changes."""
    return await _finish(db, resident_id, transaction_id, Status.CANCELLED, ip_address)


async def get_demo_payment(
    db: AsyncSession, resident_id: int, transaction_id: int, *, ip_address: str | None = None
) -> DemoPaymentTransactionResult:
    """The resident's transaction; one past its expiry is made EXPIRED first (persisted, same locks)."""
    _check_resident_id(resident_id)
    path = await _transaction_path(db, resident_id, transaction_id)
    transaction = await db.scalar(
        select(DemoPaymentTransaction)
        .where(DemoPaymentTransaction.id == transaction_id, DemoPaymentTransaction.resident_id == resident_id)
        .execution_options(populate_existing=True)
    )
    if transaction is None:
        raise DemoTransactionNotFoundError()
    if transaction.status != Status.INITIATED or not is_expired(transaction, utc_now()):
        result = _result(transaction)
        await db.commit()
        return result
    await db.commit()
    try:
        await _lock_path(db, path)
        transaction = await _lock_transaction(db, resident_id, transaction_id)
        if transaction.status == Status.INITIATED and is_expired(transaction, utc_now()):
            _expire(db, transaction, ip_address)
            await db.flush()
        result = _result(transaction)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return result


# --- Obligations the resident can choose from (read only) --------------------------------------------------


@dataclass(frozen=True)
class PayableCharge:
    charge: ChargeSummary  # outstanding_amount > 0, from the persisted allocations
    in_progress_transaction_id: int | None  # the resident's own INITIATED transaction of it, not expired yet


async def list_payable_charges(db: AsyncSession, resident_id: int) -> tuple[PayableCharge, ...]:
    """The obligations of the resident's current apartment that still have an outstanding balance.

    A snapshot for choosing one, never an authority: start_demo_payment checks the residence, the
    account and the balance again under the apartment lock. Nothing is locked or persisted here
    (an expired transaction is simply not reported; get or start make it EXPIRED). Without a current
    residence, or when the apartment was deleted, there is nothing to pay.
    """
    _check_resident_id(resident_id)
    now = utc_now()
    await db.commit()  # a fresh snapshot
    if not _is_active_resident(await db.get(User, resident_id, populate_existing=True)):
        raise ForbiddenError()
    unit_id = await _current_unit_id(db, resident_id)
    charges = () if unit_id is None else await list_outstanding_charges(db, unit_id, bogota_today(now))
    charges = tuple(charge for charge in charges if not charge.unit.deleted)
    live: dict[int, int] = {}
    if charges:
        rows = await db.execute(
            select(DemoPaymentTransaction.charge_id, DemoPaymentTransaction.id).where(
                DemoPaymentTransaction.resident_id == resident_id,
                DemoPaymentTransaction.status == Status.INITIATED,
                DemoPaymentTransaction.expires_at > now,  # is_expired: expired from expires_at on
                DemoPaymentTransaction.charge_id.in_([charge.id for charge in charges]),
            )
        )
        live = {row.charge_id: row.id for row in rows}
    await db.commit()  # a read: nothing to keep
    return tuple(PayableCharge(charge, live.get(charge.id)) for charge in charges)
