"""Manual payments registered by the administrator (HU-004).

register  One atomic transaction, then the receipt:

            BEGIN
              lock block (S) → tower (S) → apartment (X)    same order as HU-013/HU-019
              current residence of the apartment (S)       must still be the resident's
              resident account (S)                         RESIDENTE, not deleted, ACTIVE/DISABLED
              pending charges of the apartment (X)         FIFO: due_date, created_at, id
              payment + allocations + PAYMENT_REGISTERED
              invariants checked again (never over-allocated)
            COMMIT
            PDF receipt + email (no transaction or lock open)
            receipt result + RECEIPT_EMAIL_SENT / RECEIPT_EMAIL_FAILED   (another transaction)

          A failed PDF or email never undoes the payment. The client chooses the
          resident; the apartment, the registration time, the snapshots, the
          allocations and the status are always decided here.
list      Search, filters, sort and pagination in MySQL (one COUNT, one page).

Balances are derived (charges - allocations; payments - allocations), never stored.
"""

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import anyio
from sqlalchemy import ColumnElement, Select, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.exceptions import FieldValidationError, ResidenceChangedError, UnitNotFoundError
from app.core.money import CENT
from app.core.unit_labels import unit_label
from app.models import (
    AuditAction,
    Charge,
    FinancialConcept,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    ReceiptEmailStatus,
    ResidentUnit,
    Tower,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.models.finance import PaymentSource
from app.schemas.payment import (
    AccountBalance,
    AllocationRead,
    PaymentCreate,
    PaymentListQuery,
    PaymentPage,
    PaymentRead,
    PaymentRegistered,
    PaymentSortField,
    ResidentRef,
    SortOrder,
    UnitRef,
)
from app.services.allocation import PendingCharge, allocate_fifo, payment_status, receipt_number
from app.services.audit import record_audit
from app.services.mail import MailSender
from app.services.receipts import ReceiptData, build_receipt_email, build_receipt_pdf
from app.services.structure import Actor, lock_unit_path

logger = logging.getLogger(__name__)

NOT_PAYABLE_MESSAGE = "Selecciona un residente registrado con una residencia vigente."
# Accounts that can receive payments: activated residents, enabled or not.
PAYABLE_STATUSES = (UserStatus.ACTIVE, UserStatus.DISABLED)


def money(value: Decimal | int | None) -> Decimal:
    """Same representation everywhere: 2 decimal places (exact, never rounded)."""
    return Decimal(value or 0).quantize(CENT)


def is_payable(user: User | None) -> bool:
    return (
        user is not None
        and user.role == UserRole.RESIDENTE
        and user.deleted_at is None
        and user.status in PAYABLE_STATUSES
    )


# --- Derived amounts ----------------------------------------------------------------------------------


def _applied_amount() -> Any:
    """Correlated subquery: what a payment covers of charges."""
    return (
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
        .where(PaymentAllocation.payment_id == Payment.id)
        .correlate(Payment)
        .scalar_subquery()
    )


async def unit_balance(db: AsyncSession, unit_id: int) -> AccountBalance:
    """Pending = charges - allocations to them; credit = payments - allocations from them.

    Consistent reads. Inside a financial transaction it runs under the apartment
    lock (X), after the transaction's first consistent read, so it sees every
    committed change plus the transaction's own. No FOR SHARE: these joins
    locked allocation rows and gaps of other apartments (deadlock, see _covered).
    """

    async def total(statement: Any) -> Decimal:
        return money(await db.scalar(statement))

    charged = await total(select(func.coalesce(func.sum(Charge.amount), 0)).where(Charge.unit_id == unit_id))
    covered = await total(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
        .join(Charge, Charge.id == PaymentAllocation.charge_id)
        .where(Charge.unit_id == unit_id)
    )
    paid = await total(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.unit_id == unit_id))
    applied = await total(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(Payment.unit_id == unit_id)
    )
    return AccountBalance(pending_balance=charged - covered, credit_balance=paid - applied)


# --- Register ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Registered:
    response: PaymentRegistered
    receipt: ReceiptData
    email: str


async def _current_unit(db: AsyncSession, resident_id: int) -> tuple[int, int, int]:
    """(unit_id, tower_id, block_id) of the resident's current residence (no locks)."""
    row = (
        await db.execute(
            select(ResidentUnit.unit_id, Unit.tower_id, Tower.block_id)
            .join(Unit, Unit.id == ResidentUnit.unit_id)
            .join(Tower, Tower.id == Unit.tower_id)
            .where(ResidentUnit.user_id == resident_id, ResidentUnit.ended_at.is_(None))
        )
    ).first()
    if row is None:
        raise FieldValidationError("resident_id", NOT_PAYABLE_MESSAGE)
    return row.unit_id, row.tower_id, row.block_id


def _covered() -> Any:
    """Correlated subquery: what has been allocated to a charge.

    A consistent read, never FOR SHARE: a locking read of an allocation that does
    not exist yet locks the gap at the end of ix_payment_allocations_charge_id,
    the same gap every new allocation of any apartment is inserted into, so two
    operations on different apartments deadlocked (1213). The apartment lock (X)
    already serializes every writer of these rows.
    """
    covered = select(func.coalesce(func.sum(PaymentAllocation.amount), 0)).where(PaymentAllocation.charge_id == Charge.id)
    return covered.correlate(Charge).scalar_subquery()


async def _pending_charges(db: AsyncSession, unit_id: int) -> list[PendingCharge]:
    """Charges of the apartment not fully covered, locked (FOR UPDATE) in FIFO order.

    Only called while the apartment is locked (X), and the transaction's first
    consistent read happens after that lock: what is covered is read from a
    snapshot that already has every allocation committed before it. Every writer
    of the apartment's charges and allocations holds the same apartment lock.
    """
    covered = _covered()
    rows = (
        await db.execute(
            select(Charge.id, Charge.due_date, Charge.created_at, (Charge.amount - covered).label("outstanding"))
            .where(Charge.unit_id == unit_id, Charge.amount > covered)
            .order_by(Charge.due_date, Charge.created_at, Charge.id)
            .with_for_update()
        )
    ).all()
    return [PendingCharge(row.id, row.due_date, row.created_at, money(row.outstanding)) for row in rows]


async def _check_invariants(db: AsyncSession, payment: Payment, charge_ids: list[int]) -> None:
    """Defense in depth before COMMIT: never more allocated than a payment or a charge.

    Consistent reads under the apartment lock (X): the snapshot was taken after
    the lock, so it has every allocation committed before, plus this
    transaction's own. No FOR SHARE: see _covered (deadlock between apartments).
    """
    allocated = money(
        await db.scalar(
            select(func.coalesce(func.sum(PaymentAllocation.amount), 0)).where(PaymentAllocation.payment_id == payment.id)
        )
    )
    over_charged = await db.scalar(
        select(func.count()).select_from(Charge).where(Charge.id.in_(charge_ids), Charge.amount < _covered())
    ) if charge_ids else 0
    if allocated > payment.amount or over_charged:
        await db.rollback()
        raise RuntimeError("Payment allocation invariant violated; the transaction was rolled back.")


async def _register(db: AsyncSession, data: PaymentCreate, actor: Actor) -> _Registered:
    # 1. Which apartment (no locks): the resident's current residence and its parents.
    await db.commit()
    unit_id, tower_id, block_id = await _current_unit(db, data.resident_id)
    await db.commit()

    # 2. New transaction whose first statement is a locking read (fresh view after it).
    try:
        block, tower, unit = await lock_unit_path(db, unit_id, tower_id=tower_id, block_id=block_id)
    except UnitNotFoundError:
        await db.rollback()
        raise FieldValidationError("resident_id", NOT_PAYABLE_MESSAGE) from None
    residence = await db.scalar(
        select(ResidentUnit)
        .where(ResidentUnit.active_unit_marker == unit_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if residence is None or residence.user_id != data.resident_id:
        await db.rollback()
        raise ResidenceChangedError()
    resident = await db.scalar(
        select(User).where(User.id == data.resident_id).with_for_update(read=True).execution_options(populate_existing=True)
    )
    if resident is None or not is_payable(resident):
        await db.rollback()
        raise FieldValidationError("resident_id", NOT_PAYABLE_MESSAGE)

    # 3. FIFO over the locked pending charges; the rest is credit.
    pending = await _pending_charges(db, unit_id)
    result = allocate_fifo(data.amount, pending)
    now = utc_now()
    label = unit_label(block.number, tower.number, unit.number)
    payment = Payment(
        resident_id=resident.id,
        unit_id=unit_id,
        registered_by_user_id=actor.user_id,
        amount=data.amount,
        method=data.method,
        concept=data.concept,
        concept_description=data.concept_description,
        source=PaymentSource.MANUAL,
        payment_date=data.payment_date,
        registered_at=now,
        resident_name_snapshot=resident.full_name,
        unit_label_snapshot=label,
        receipt_email_status=ReceiptEmailStatus.PENDING,
    )
    db.add(payment)
    await db.flush()
    for allocation in result.allocations:
        db.add(PaymentAllocation(payment_id=payment.id, charge_id=allocation.charge_id, amount=allocation.amount))
    await db.flush()
    await _check_invariants(db, payment, [a.charge_id for a in result.allocations])

    number = receipt_number(payment.id)
    record_audit(
        db,
        action=AuditAction.PAYMENT_REGISTERED,
        entity_type="payment",
        entity_id=payment.id,
        actor_user_id=actor.user_id,
        context={
            "payment_id": payment.id,
            "amount": str(money(data.amount)),
            "method": str(data.method),
            "concept": str(data.concept),
            "unit_id": unit_id,
            "receipt_number": number,
        },
        ip_address=actor.ip_address,
    )
    charges = {
        row.id: row
        for row in (
            await db.execute(
                select(Charge.id, Charge.concept, Charge.description, Charge.due_date).where(
                    Charge.id.in_([a.charge_id for a in result.allocations])
                )
            )
        ).all()
    } if result.allocations else {}
    account = await unit_balance(db, unit_id)
    response = PaymentRegistered(
        id=payment.id,
        receipt_number=number,
        resident=ResidentRef(id=resident.id, full_name=resident.full_name),
        unit=UnitRef(id=unit_id, label=label),
        amount=money(data.amount),
        method=data.method,
        concept=data.concept,
        concept_description=data.concept_description,
        payment_date=data.payment_date,
        registered_at=_utc(now),
        applied_amount=money(result.applied),
        credit_amount=money(result.credit),
        status=payment_status(data.amount, result.applied),
        receipt_email_status=ReceiptEmailStatus.PENDING,
        allocations=[
            AllocationRead(
                charge_id=a.charge_id,
                concept=FinancialConcept(charges[a.charge_id].concept),
                description=charges[a.charge_id].description,
                due_date=charges[a.charge_id].due_date,
                amount=money(a.amount),
            )
            for a in result.allocations
        ],
        account=account,
    )
    receipt = ReceiptData(
        receipt_number=number,
        resident_name=resident.full_name,
        unit_label=label,
        amount=money(data.amount),
        method=PaymentMethod(data.method),
        concept=FinancialConcept(data.concept),
        concept_description=data.concept_description,
        payment_date=data.payment_date,
        registered_at=now,
    )
    email = resident.email
    await db.commit()
    return _Registered(response=response, receipt=receipt, email=email)


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC)


async def _record_receipt(db: AsyncSession, payment_id: int, number: str, error: str | None, actor: Actor) -> None:
    sent = error is None
    await db.execute(
        update(Payment)
        .where(Payment.id == payment_id)
        .values(
            receipt_email_status=ReceiptEmailStatus.SENT if sent else ReceiptEmailStatus.FAILED,
            receipt_sent_at=utc_now() if sent else None,
        )
    )
    context: dict[str, Any] = {"payment_id": payment_id, "receipt_number": number}
    if error is not None:
        context["error"] = error
    record_audit(
        db,
        action=AuditAction.RECEIPT_EMAIL_SENT if sent else AuditAction.RECEIPT_EMAIL_FAILED,
        entity_type="payment",
        entity_id=payment_id,
        actor_user_id=actor.user_id,
        context=context,
        ip_address=actor.ip_address,
    )
    await db.commit()


async def register_payment(
    db: AsyncSession, data: PaymentCreate, actor: Actor, *, mailer: MailSender
) -> PaymentRegistered:
    registered = await _register(db, data, actor)  # committed: the payment exists from here on
    response, receipt = registered.response, registered.receipt
    error: str | None = None
    try:
        # Off the event loop; no transaction or lock is open while the PDF and SMTP run.
        pdf = await anyio.to_thread.run_sync(build_receipt_pdf, receipt)
        await mailer.send(build_receipt_email(to=registered.email, data=receipt, pdf=pdf))
    except Exception as exc:  # any PDF/SMTP/network failure; the message is never logged
        error = type(exc).__name__
        logger.warning("Receipt email not sent (payment_id=%s, error=%s)", response.id, error)
    try:
        await _record_receipt(db, response.id, response.receipt_number, error, actor)
        status = ReceiptEmailStatus.SENT if error is None else ReceiptEmailStatus.FAILED
    except Exception as exc:
        # The payment stays registered; its receipt status stays PENDING (unconfirmed).
        await db.rollback()
        status = ReceiptEmailStatus.PENDING
        logger.error("Receipt result not recorded (payment_id=%s, error=%s)", response.id, type(exc).__name__)
    return response.model_copy(update={"receipt_email_status": status})


# --- List ----------------------------------------------------------------------------------------------

_CONCEPT_ORDER = {  # Spanish labels in alphabetical order: Mensualidad, Mora, Otro, Penalización
    FinancialConcept.MONTHLY_FEE: 1, FinancialConcept.LATE_FEE: 2, FinancialConcept.OTHER: 3, FinancialConcept.PENALTY: 4,
}
_METHOD_ORDER = {PaymentMethod.CASH: 1, PaymentMethod.CARD: 2, PaymentMethod.TRANSFER: 3}  # Efectivo, Tarjeta, Transferencia


def _sort_keys(field: PaymentSortField, applied: Any) -> tuple[Any, ...]:
    # Never built from the client's text: each whitelisted value maps to expressions.
    match field:
        case PaymentSortField.DATE:
            return (Payment.payment_date, Payment.registered_at)
        case PaymentSortField.RESIDENT:
            return (Payment.resident_name_snapshot,)
        case PaymentSortField.AMOUNT:
            return (Payment.amount,)
        case PaymentSortField.CONCEPT:
            return (case(*((Payment.concept == k.value, v) for k, v in _CONCEPT_ORDER.items())),)
        case PaymentSortField.METHOD:
            return (case(*((Payment.method == k.value, v) for k, v in _METHOD_ORDER.items())),)
        case PaymentSortField.STATUS:  # Aplicado, Aplicado parcialmente, Saldo a favor
            return (case((applied >= Payment.amount, 1), (applied == 0, 3), else_=2),)


def _filtered(statement: Select[Any], query: PaymentListQuery) -> Select[Any]:
    conditions: list[ColumnElement[bool]] = []
    if query.resident_id is not None:
        conditions.append(Payment.resident_id == query.resident_id)
    if query.date_from is not None:
        conditions.append(Payment.payment_date >= query.date_from)
    if query.date_to is not None:
        conditions.append(Payment.payment_date <= query.date_to)
    if query.search:
        # Name as shown in the table (snapshot) or the resident's document; literal % and _.
        statement = statement.join(User, User.id == Payment.resident_id)
        conditions.append(
            Payment.resident_name_snapshot.contains(query.search, autoescape=True)
            | User.document_number.contains(query.search, autoescape=True)
        )
    return statement.where(*conditions)


async def list_payments(db: AsyncSession, query: PaymentListQuery) -> PaymentPage:
    total = int(await db.scalar(_filtered(select(func.count(Payment.id)).select_from(Payment), query)) or 0)
    total_pages = math.ceil(total / query.page_size)
    items: list[PaymentRead] = []
    if query.page <= total_pages:
        applied = _applied_amount()
        descending = query.sort_order == SortOrder.DESC
        keys = [*_sort_keys(query.sort_by, applied), Payment.id]
        rows = (
            await db.execute(
                _filtered(
                    select(
                        Payment.id,
                        Payment.resident_id,
                        Payment.resident_name_snapshot,
                        Payment.unit_id,
                        Payment.unit_label_snapshot,
                        Payment.amount,
                        Payment.method,
                        Payment.concept,
                        Payment.concept_description,
                        Payment.payment_date,
                        Payment.registered_at,
                        Payment.receipt_email_status,
                        applied.label("applied"),
                    ).select_from(Payment),
                    query,
                )
                .order_by(*(key.desc() if descending else key.asc() for key in keys))
                .limit(query.page_size)
                .offset((query.page - 1) * query.page_size)
            )
        ).all()
        items = [_payment_read(row) for row in rows]
    return PaymentPage(items=items, page=query.page, page_size=query.page_size, total=total, total_pages=total_pages)


def _payment_read(row: Any) -> PaymentRead:
    amount, applied = money(row.amount), money(row.applied)
    return PaymentRead(
        id=row.id,
        receipt_number=receipt_number(row.id),
        resident=ResidentRef(id=row.resident_id, full_name=row.resident_name_snapshot),
        unit=UnitRef(id=row.unit_id, label=row.unit_label_snapshot),
        amount=amount,
        method=PaymentMethod(row.method),
        concept=FinancialConcept(row.concept),
        concept_description=row.concept_description,
        payment_date=row.payment_date,
        registered_at=_utc(row.registered_at),
        applied_amount=applied,
        credit_amount=amount - applied,
        status=payment_status(amount, applied),
        receipt_email_status=ReceiptEmailStatus(row.receipt_email_status),
    )
