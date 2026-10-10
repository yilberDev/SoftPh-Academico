"""Payments of one resident, read by the resident (HU-008). Read only: no locks, no writes.

Ownership: every query carries payments.resident_id = the resident given by the
caller (the future API takes it from the session, never from the client). The
current apartment is never used: a resident sees the payments registered to
them, and not those of another resident of the same apartment. A payment of
another resident is "not found", exactly as a payment that does not exist.

Nothing financial is computed here in a new way:
- applied amount: the correlated subquery of HU-004 (SUM of payment_allocations);
- credit (saldo a favor) left by the payment: amount - applied;
- status: payment_status (Aplicado, Aplicado parcialmente, Saldo a favor); the
  status filter uses the same rule written in SQL (_status_expression);
- receipt number: derived from the id (RC-00000001), never stored;
- covered obligations: the existing payment_allocations rows of the payment.

list     one COUNT and one page (ORDER BY payment_date DESC, id DESC, LIMIT, OFFSET in MySQL)
get      the payment (with its applied amount) and its allocations: 2 queries
receipt  HU-010: the data of the receipt of an approved demo payment (3 queries, no locks, no
         writes, no commit): the payment, its demo transaction and its allocation with the
         obligation, checked against each other before anything is composed
"""

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FieldValidationError, PaymentNotFoundError, ReceiptUnavailableError
from app.models import (
    Charge,
    DemoPaymentTransaction,
    DemoPaymentTransactionStatus,
    FinancialConcept,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    PaymentSource,
)
from app.schemas.invitation import ID_MAX
from app.schemas.payment import PAGE_MAX, PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, SEARCH_MAX_LENGTH
from app.services.allocation import RECEIPT_PREFIX, PaymentStatus, payment_status, receipt_number
from app.services.payments import _applied_amount, money
from app.services.receipts import DemoReceiptData

logger = logging.getLogger(__name__)

SEARCH_MESSAGE = "Busca por número de recibo (p. ej., RC-00000012 o 12) o por fecha (AAAA-MM-DD)."

# "RC-00000012", "RC12", "rc-12" or "12" (the prefix of receipt_number, its dash optional).
# ASCII digits only (\d would also accept other scripts' digits).
_RECEIPT_RE = re.compile(rf"^(?:{re.escape(RECEIPT_PREFIX.rstrip('-'))}-?)?([0-9]{{1,19}})$", re.IGNORECASE)
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


# --- Query ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResidentPaymentQuery:
    page: int = 1
    page_size: int = PAGE_SIZE_DEFAULT
    status: PaymentStatus | None = None
    date_from: date | None = None  # inclusive, on payment_date
    date_to: date | None = None  # inclusive, on payment_date
    search: str | None = None  # a receipt number (RC-00000012 or 12) or a date (YYYY-MM-DD)


@dataclass(frozen=True)
class PaymentSearch:
    """What the search box means: exactly one of the two is set."""

    payment_id: int | None = None  # receipt number → id
    payment_date: date | None = None  # a specific day


def _positive_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= ID_MAX


def _is_date(value: object) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def parse_search(text: Any) -> PaymentSearch | None:
    """A receipt number (RC-00000012, rc-12, 12) or a calendar date (2026-10-05); blank is no search."""
    if text is None:
        return None
    if not isinstance(text, str):
        raise FieldValidationError("search", SEARCH_MESSAGE)
    value = text.strip()
    if not value:
        return None
    if len(value) > SEARCH_MAX_LENGTH:
        raise FieldValidationError("search", f"La búsqueda no puede superar {SEARCH_MAX_LENGTH} caracteres.")
    if _DATE_RE.match(value):
        try:
            return PaymentSearch(payment_date=date.fromisoformat(value))
        except ValueError:
            raise FieldValidationError("search", "La fecha buscada no es válida.") from None
    match = _RECEIPT_RE.match(value)
    if match is None:
        raise FieldValidationError("search", SEARCH_MESSAGE)
    number = int(match.group(1))
    if not 1 <= number <= ID_MAX:
        raise FieldValidationError("search", "El número de recibo no es válido.")
    return PaymentSearch(payment_id=number)


@dataclass(frozen=True)
class _Validated:
    query: ResidentPaymentQuery
    search: PaymentSearch | None


def _validated(resident_id: Any, query: ResidentPaymentQuery) -> _Validated:
    """The service never trusts its caller: the future API validates too, with the same rules."""
    if not _positive_id(resident_id):
        raise ValueError("resident_id is the signed-in resident (a positive id).")
    for field, value in (("page", query.page), ("page_size", query.page_size)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise FieldValidationError(field, "El valor no es válido.")
    if not 1 <= query.page <= PAGE_MAX:
        raise FieldValidationError("page", f"La página debe estar entre 1 y {PAGE_MAX}.")
    if not 1 <= query.page_size <= PAGE_SIZE_MAX:
        raise FieldValidationError("page_size", f"El tamaño de página debe estar entre 1 y {PAGE_SIZE_MAX}.")
    try:
        status = None if query.status is None else PaymentStatus(query.status)
    except ValueError:
        raise FieldValidationError("status", "Selecciona un estado válido.") from None
    for field, value in (("date_from", query.date_from), ("date_to", query.date_to)):
        if value is not None and not _is_date(value):
            raise FieldValidationError(field, "La fecha no es válida.")
    if query.date_from is not None and query.date_to is not None and query.date_from > query.date_to:
        raise FieldValidationError("date_to", "La fecha final no puede ser anterior a la inicial.")
    search = parse_search(query.search)
    return _Validated(
        ResidentPaymentQuery(query.page, query.page_size, status, query.date_from, query.date_to, query.search),
        search,
    )


# --- Results -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResidentPaymentSummary:
    """One row of the resident's table (HU-008 criterion 1.1); the notes are in the detail only."""

    id: int
    receipt_number: str
    payment_date: date
    concept: FinancialConcept
    concept_description: str | None  # "Otro" only
    amount: Decimal
    method: PaymentMethod
    status: PaymentStatus
    transaction_reference: str | None


@dataclass(frozen=True)
class ResidentPaymentPage:
    items: tuple[ResidentPaymentSummary, ...]
    page: int
    page_size: int
    total: int
    total_pages: int


@dataclass(frozen=True)
class PaymentUnit:
    id: int
    label: str  # as on the receipt (snapshot at registration time)


@dataclass(frozen=True)
class PaymentAllocationSummary:
    """One obligation covered by the payment (an existing payment_allocations row)."""

    charge_id: int
    concept: FinancialConcept
    description: str | None
    billing_period: date | None  # monthly fees only (first day of the month)
    due_date: date
    charge_amount: Decimal  # the obligation's original amount
    allocated_amount: Decimal  # what this payment covered of it
    allocated_at: datetime  # UTC (payment_allocations.created_at)


@dataclass(frozen=True)
class ResidentPaymentDetail:
    id: int
    receipt_number: str
    payment_date: date
    registered_at: datetime  # UTC
    concept: FinancialConcept
    concept_description: str | None
    amount: Decimal
    method: PaymentMethod
    status: PaymentStatus
    transaction_reference: str | None
    notes: str | None  # written by the administrator, visible to the resident
    unit: PaymentUnit
    applied_amount: Decimal
    credit_amount: Decimal  # saldo a favor left by this payment (amount - applied)
    allocations: tuple[PaymentAllocationSummary, ...]


# --- SQL ---------------------------------------------------------------------------------------------------


def _status_expression(applied: Any) -> Any:
    """payment_status in SQL, same rule and order: APPLIED (applied >= amount), CREDIT (applied <= 0), else partial."""
    return case(
        (applied >= Payment.amount, literal(PaymentStatus.APPLIED.value)),
        (applied <= 0, literal(PaymentStatus.CREDIT.value)),
        else_=literal(PaymentStatus.PARTIALLY_APPLIED.value),
    )


def _conditions(resident_id: int, checked: _Validated, applied: Any) -> list[ColumnElement[bool]]:
    query, search = checked.query, checked.search
    # Ownership first, always in SQL.
    conditions: list[ColumnElement[bool]] = [Payment.resident_id == resident_id]
    if query.status is not None:
        conditions.append(_status_expression(applied) == query.status.value)
    if query.date_from is not None:
        conditions.append(Payment.payment_date >= query.date_from)
    if query.date_to is not None:
        conditions.append(Payment.payment_date <= query.date_to)
    if search is not None and search.payment_id is not None:
        conditions.append(Payment.id == search.payment_id)
    if search is not None and search.payment_date is not None:
        conditions.append(Payment.payment_date == search.payment_date)  # exact comparison, never LIKE
    return conditions


def _summary(row: Any) -> ResidentPaymentSummary:
    amount = money(row.amount)
    return ResidentPaymentSummary(
        id=row.id,
        receipt_number=receipt_number(row.id),
        payment_date=row.payment_date,
        concept=FinancialConcept(row.concept),
        concept_description=row.concept_description,
        amount=amount,
        method=PaymentMethod(row.method),
        status=payment_status(amount, money(row.applied)),
        transaction_reference=row.transaction_reference,
    )


async def list_resident_payments(db: AsyncSession, resident_id: int, query: ResidentPaymentQuery) -> ResidentPaymentPage:
    """The resident's payments, newest payment date first; one COUNT and one page."""
    checked = _validated(resident_id, query)
    applied = _applied_amount()
    conditions = _conditions(resident_id, checked, applied)
    total = int(await db.scalar(select(func.count(Payment.id)).select_from(Payment).where(*conditions)) or 0)
    page, page_size = checked.query.page, checked.query.page_size
    total_pages = math.ceil(total / page_size)
    items: tuple[ResidentPaymentSummary, ...] = ()
    if page <= total_pages:
        rows = (
            await db.execute(
                select(
                    Payment.id,
                    Payment.payment_date,
                    Payment.concept,
                    Payment.concept_description,
                    Payment.amount,
                    Payment.method,
                    Payment.transaction_reference,
                    applied.label("applied"),
                )
                .where(*conditions)
                # payments.id breaks ties in the same direction: a stable order across pages.
                .order_by(Payment.payment_date.desc(), Payment.id.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).all()
        items = tuple(_summary(row) for row in rows)
    return ResidentPaymentPage(items=items, page=page, page_size=page_size, total=total, total_pages=total_pages)


async def get_resident_payment(db: AsyncSession, resident_id: int, payment_id: int) -> ResidentPaymentDetail:
    """One payment of the resident with the obligations it covered (2 queries).

    resident_id is required: a payment id alone never authorizes anything. A payment
    of another resident raises the same PaymentNotFoundError as a missing one.
    """
    if not _positive_id(resident_id):
        raise ValueError("resident_id is the signed-in resident (a positive id).")
    if not _positive_id(payment_id):
        raise PaymentNotFoundError()
    applied = _applied_amount()
    row = (
        await db.execute(
            select(
                Payment.id,
                Payment.payment_date,
                Payment.registered_at,
                Payment.concept,
                Payment.concept_description,
                Payment.amount,
                Payment.method,
                Payment.transaction_reference,
                Payment.notes,
                Payment.unit_id,
                Payment.unit_label_snapshot,
                applied.label("applied"),
            ).where(Payment.id == payment_id, Payment.resident_id == resident_id)
        )
    ).first()
    if row is None:
        raise PaymentNotFoundError()
    allocations = tuple(
        PaymentAllocationSummary(
            charge_id=r.charge_id,
            concept=FinancialConcept(r.concept),
            description=r.description,
            billing_period=r.billing_period,
            due_date=r.due_date,
            charge_amount=money(r.charge_amount),
            allocated_amount=money(r.allocated_amount),
            allocated_at=r.allocated_at,
        )
        for r in (
            await db.execute(
                select(
                    PaymentAllocation.charge_id,
                    PaymentAllocation.amount.label("allocated_amount"),
                    PaymentAllocation.created_at.label("allocated_at"),
                    Charge.concept,
                    Charge.description,
                    Charge.billing_period,
                    Charge.due_date,
                    Charge.amount.label("charge_amount"),
                )
                .join(Charge, Charge.id == PaymentAllocation.charge_id)
                .where(PaymentAllocation.payment_id == row.id)
                .order_by(PaymentAllocation.created_at, PaymentAllocation.id)
            )
        ).all()
    )
    amount, applied_amount = money(row.amount), money(row.applied)
    return ResidentPaymentDetail(
        id=row.id,
        receipt_number=receipt_number(row.id),
        payment_date=row.payment_date,
        registered_at=row.registered_at,
        concept=FinancialConcept(row.concept),
        concept_description=row.concept_description,
        amount=amount,
        method=PaymentMethod(row.method),
        status=payment_status(amount, applied_amount),
        transaction_reference=row.transaction_reference,
        notes=row.notes,
        unit=PaymentUnit(id=row.unit_id, label=row.unit_label_snapshot),
        applied_amount=applied_amount,
        credit_amount=amount - applied_amount,
        allocations=allocations,
    )


# --- HU-010: receipt of an approved demo payment ---------------------------------------------------------


def _inconsistent(payment_id: int, check: str) -> ReceiptUnavailableError:
    # Only the payment id and the failed check are logged: never names, amounts or references.
    logger.error("Demo receipt not generated: inconsistent records (payment_id=%s, check=%s)", payment_id, check)
    return ReceiptUnavailableError()


async def get_demo_receipt(db: AsyncSession, resident_id: int, payment_id: int) -> DemoReceiptData:
    """The receipt data of the resident's approved demo payment, only from persisted records.

    The payment is found by its id AND the session's resident AND source DEMO_GATEWAY: another
    resident's payment, a manual payment and a missing one are the same PaymentNotFoundError.
    Then the payment, its demo transaction (by payment_id) and its single allocation must agree;
    when they do not, nothing is composed (ReceiptUnavailableError, a generic 500). Read only:
    no locks, no writes, no commit (payments, allocations and approved transactions are never
    changed after the approval, so consistent reads are enough).
    """
    if not _positive_id(resident_id):
        raise ValueError("resident_id is the signed-in resident (a positive id).")
    if not _positive_id(payment_id):
        raise PaymentNotFoundError()
    payment = (
        await db.execute(
            select(
                Payment.id, Payment.resident_id, Payment.unit_id, Payment.amount, Payment.method, Payment.concept,
                Payment.transaction_reference, Payment.payment_date, Payment.registered_at,
                Payment.resident_name_snapshot, Payment.unit_label_snapshot,
            ).where(
                Payment.id == payment_id,
                Payment.resident_id == resident_id,  # ownership first, always in SQL
                Payment.source == PaymentSource.DEMO_GATEWAY.value,
            )
        )
    ).first()
    if payment is None:
        raise PaymentNotFoundError()
    if payment.method != PaymentMethod.DEMO_GATEWAY.value:
        raise _inconsistent(payment.id, "method")

    transactions = (
        await db.execute(
            select(
                DemoPaymentTransaction.resident_id, DemoPaymentTransaction.charge_id, DemoPaymentTransaction.amount,
                DemoPaymentTransaction.reference, DemoPaymentTransaction.status,
            ).where(DemoPaymentTransaction.payment_id == payment.id)
        )
    ).all()
    if len(transactions) != 1:
        raise _inconsistent(payment.id, "transaction_count")
    [transaction] = transactions
    if transaction.status != DemoPaymentTransactionStatus.APPROVED.value:
        raise _inconsistent(payment.id, "transaction_status")
    if transaction.reference != payment.transaction_reference:
        raise _inconsistent(payment.id, "reference")
    if transaction.resident_id != payment.resident_id or money(transaction.amount) != money(payment.amount):
        raise _inconsistent(payment.id, "transaction_payment")

    allocations = (
        await db.execute(
            select(
                PaymentAllocation.charge_id, PaymentAllocation.amount, Charge.unit_id, Charge.concept,
                Charge.description, Charge.billing_period, Charge.due_date,
            )
            .join(Charge, Charge.id == PaymentAllocation.charge_id)
            .where(PaymentAllocation.payment_id == payment.id)
        )
    ).all()
    if len(allocations) != 1:
        raise _inconsistent(payment.id, "allocation_count")
    [allocation] = allocations
    if money(allocation.amount) != money(payment.amount):
        raise _inconsistent(payment.id, "allocation_amount")
    if allocation.charge_id != transaction.charge_id or allocation.unit_id != payment.unit_id:
        raise _inconsistent(payment.id, "allocation_charge")
    if allocation.concept != payment.concept:
        raise _inconsistent(payment.id, "concept")

    return DemoReceiptData(
        receipt_number=receipt_number(payment.id),
        resident_name=payment.resident_name_snapshot,
        unit_label=payment.unit_label_snapshot,
        concept=FinancialConcept(allocation.concept),
        concept_description=allocation.description,
        billing_period=allocation.billing_period,
        due_date=allocation.due_date,
        amount=money(payment.amount),
        applied_amount=money(allocation.amount),
        method=PaymentMethod(payment.method),
        reference=transaction.reference,
        payment_date=payment.payment_date,
        registered_at=payment.registered_at,
    )
