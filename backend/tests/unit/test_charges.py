"""Obligations extension: pure rules (charge status, credit allocation, billing period)."""

import copy
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.allocation import (
    AvailableCredit,
    ChargeStatus,
    CreditAllocation,
    PendingCharge,
    allocate_credit,
    charge_status,
    parse_period,
)

TODAY = date(2026, 10, 15)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
CREATED = datetime(2026, 1, 1, 12, 0, 0)
REGISTERED = datetime(2026, 1, 1, 15, 0, 0)


def credit(payment_id: int, available: str, paid: date = date(2026, 1, 10),
           registered: datetime = REGISTERED) -> AvailableCredit:
    return AvailableCredit(payment_id, paid, registered, Decimal(available))


def pending(charge_id: int, outstanding: str, due: date = date(2026, 2, 5), created: datetime = CREATED) -> PendingCharge:
    return PendingCharge(charge_id, due, created, Decimal(outstanding))


def result(*rows: tuple[int, int, str]) -> tuple[CreditAllocation, ...]:
    return tuple(CreditAllocation(payment_id, charge_id, Decimal(amount)) for payment_id, charge_id, amount in rows)


# --- charge_status -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("applied", "due", "expected"),
    [
        ("0", TOMORROW, ChargeStatus.PENDING),  # 1
        ("50", TOMORROW, ChargeStatus.PARTIAL),  # 2
        ("100", TOMORROW, ChargeStatus.PAID),  # 3
        ("0", YESTERDAY, ChargeStatus.OVERDUE),  # 4
        ("50", YESTERDAY, ChargeStatus.OVERDUE),  # 5: partial and overdue
        ("100", YESTERDAY, ChargeStatus.PAID),  # 6: paid wins over overdue
        ("0", TODAY, ChargeStatus.PENDING),  # 7: due today is not overdue yet
        ("50", TODAY, ChargeStatus.PARTIAL),  # 8
        ("100.01", YESTERDAY, ChargeStatus.PAID),  # 9: applied > amount
        ("99.99", TOMORROW, ChargeStatus.PARTIAL),  # never rounded up
        ("0.01", TOMORROW, ChargeStatus.PARTIAL),
    ],
)
def test_charge_status(applied: str, due: date, expected: ChargeStatus) -> None:
    assert charge_status(Decimal("100"), Decimal(applied), due, TODAY) is expected


def test_charge_status_values_are_stable() -> None:
    assert [s.value for s in ChargeStatus] == ["PAID", "OVERDUE", "PARTIAL", "PENDING"]


@pytest.mark.parametrize("amount", ["0", "-1", "-0.01"])
def test_charge_status_rejects_an_invalid_amount(amount: str) -> None:  # 10
    with pytest.raises(ValueError, match="always positive"):
        charge_status(Decimal(amount), Decimal("0"), TOMORROW, TODAY)


def test_charge_status_rejects_a_negative_applied_amount() -> None:  # 11
    with pytest.raises(ValueError, match="never negative"):
        charge_status(Decimal("100"), Decimal("-0.01"), TOMORROW, TODAY)


@pytest.mark.parametrize(("amount", "applied"), [(100.0, Decimal("0")), (Decimal("100"), 0.0), (100, Decimal("0"))])
def test_charge_status_only_accepts_decimal_money(amount: object, applied: object) -> None:
    with pytest.raises(ValueError, match="Decimal"):
        charge_status(amount, applied, TOMORROW, TODAY)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("due", "today"),
    [(datetime(2026, 10, 15, 8), TODAY), (TOMORROW, datetime(2026, 10, 15, 8)), ("2026-10-16", TODAY), (TOMORROW, None)],
)
def test_charge_status_only_accepts_calendar_dates(due: object, today: object) -> None:
    with pytest.raises(ValueError, match="calendar dates"):
        charge_status(Decimal("100"), Decimal("0"), due, today)  # type: ignore[arg-type]


# --- allocate_credit: required examples ----------------------------------------------------------------------


def test_exact_credit() -> None:  # A / 1
    assert allocate_credit([credit(1, "100000")], [pending(10, "100000")]) == result((1, 10, "100000"))


def test_partial_credit_leaves_the_charge_partially_covered() -> None:  # B / 2
    assert allocate_credit([credit(1, "50000")], [pending(10, "100000")]) == result((1, 10, "50000"))


def test_one_credit_over_several_charges() -> None:  # C / 5
    assert allocate_credit([credit(1, "150000")], [pending(10, "100000"), pending(11, "100000", date(2026, 3, 5))]) == result(
        (1, 10, "100000"), (1, 11, "50000")
    )


def test_several_credits_over_one_charge() -> None:  # D / 4
    assert allocate_credit([credit(1, "50000"), credit(2, "70000", date(2026, 1, 11))], [pending(10, "100000")]) == result(
        (1, 10, "50000"), (2, 10, "50000")
    )  # payment 2 keeps 20000 of credit


def test_leftover_credit_stays_as_credit() -> None:  # E / 3
    allocations = allocate_credit([credit(1, "100000")], [pending(10, "30000"), pending(11, "20000", date(2026, 3, 5))])
    assert allocations == result((1, 10, "30000"), (1, 11, "20000"))
    assert Decimal("100000") - sum(a.amount for a in allocations) == Decimal("50000")


def test_several_credits_over_several_charges() -> None:  # 6
    credits = [credit(1, "30000"), credit(2, "45000", date(2026, 1, 11)), credit(3, "10000", date(2026, 1, 12))]
    charges = [pending(10, "40000"), pending(11, "25000", date(2026, 3, 5)), pending(12, "50000", date(2026, 4, 5))]
    assert allocate_credit(credits, charges) == result(
        (1, 10, "30000"), (2, 10, "10000"), (2, 11, "25000"), (2, 12, "10000"), (3, 12, "10000")
    )


# --- allocate_credit: order ----------------------------------------------------------------------------------


def test_oldest_payment_date_first() -> None:  # 7
    newer, older = credit(1, "60000", date(2026, 3, 1)), credit(2, "60000", date(2026, 2, 1))
    assert allocate_credit([newer, older], [pending(10, "100000")]) == result((2, 10, "60000"), (1, 10, "40000"))


def test_payment_date_tie_uses_registered_at() -> None:  # 8
    later = credit(1, "60000", registered=datetime(2026, 1, 10, 9, 0, 1))
    earlier = credit(2, "60000", registered=datetime(2026, 1, 10, 9, 0, 0))
    assert allocate_credit([later, earlier], [pending(10, "100000")]) == result((2, 10, "60000"), (1, 10, "40000"))


def test_full_tie_uses_payment_id() -> None:  # 9
    assert allocate_credit([credit(7, "60000"), credit(3, "60000")], [pending(10, "100000")]) == result(
        (3, 10, "60000"), (7, 10, "40000")
    )


def test_oldest_due_date_first() -> None:  # 10
    charges = [pending(10, "50000", date(2026, 3, 5)), pending(11, "50000", date(2026, 2, 5))]
    assert allocate_credit([credit(1, "70000")], charges) == result((1, 11, "50000"), (1, 10, "20000"))


def test_due_date_tie_uses_created_at() -> None:  # 11
    charges = [pending(10, "50000", created=datetime(2026, 1, 1, 12, 0, 1)), pending(11, "50000", created=CREATED)]
    assert allocate_credit([credit(1, "70000")], charges) == result((1, 11, "50000"), (1, 10, "20000"))


def test_full_tie_uses_charge_id() -> None:  # 12
    assert allocate_credit([credit(1, "70000")], [pending(12, "50000"), pending(10, "50000")]) == result(
        (1, 10, "50000"), (1, 12, "20000")
    )


def test_charges_not_yet_due_are_included() -> None:  # 13 (D7)
    charges = [pending(10, "50000", TODAY + timedelta(days=400)), pending(11, "50000", YESTERDAY)]
    assert allocate_credit([credit(1, "100000")], charges) == result((1, 11, "50000"), (1, 10, "50000"))


def test_same_order_as_hu004_fifo() -> None:
    # A payment (allocate_fifo) and a credit cover the charges in exactly the same order.
    from app.services.allocation import allocate_fifo

    charges = [pending(10, "1000", date(2026, 3, 5)), pending(11, "1000", date(2026, 2, 5)),
               pending(12, "1000", date(2026, 2, 5), datetime(2026, 1, 1, 11)), pending(13, "1000", date(2026, 2, 5))]
    by_payment = [a.charge_id for a in allocate_fifo(Decimal("4000"), charges).allocations]
    by_credit = [a.charge_id for a in allocate_credit([credit(1, "4000")], charges)]
    assert by_credit == by_payment == [12, 11, 13, 10]


# --- allocate_credit: limits ---------------------------------------------------------------------------------


def test_charges_without_outstanding_receive_nothing() -> None:  # 14
    charges = [pending(10, "0"), pending(11, "-5"), pending(12, "30000", date(2026, 3, 5))]
    assert allocate_credit([credit(1, "50000")], charges) == result((1, 12, "30000"))


def test_credits_without_available_amount_allocate_nothing() -> None:  # 15
    credits = [credit(1, "0"), credit(2, "-100", date(2026, 1, 11)), credit(3, "20000", date(2026, 1, 12))]
    assert allocate_credit(credits, [pending(10, "50000")]) == result((3, 10, "20000"))


def test_no_credits_or_no_charges() -> None:
    assert allocate_credit([], [pending(10, "50000")]) == ()
    assert allocate_credit([credit(1, "50000")], []) == ()


def test_never_negative_and_never_over_allocated() -> None:  # 16, 17, 18
    credits = [credit(i, amount, date(2026, 1, i)) for i, amount in enumerate(["0.01", "33333.33", "7", "100000", "0"], 1)]
    charges = [pending(10 + i, amount, date(2026, 2, i)) for i, amount in enumerate(["12345.67", "0.02", "50000", "99"], 1)]
    allocations = allocate_credit(credits, charges)
    assert allocations and all(a.amount > 0 for a in allocations)
    for c in credits:
        assert sum((a.amount for a in allocations if a.payment_id == c.payment_id), Decimal(0)) <= max(c.available, Decimal(0))
    for ch in charges:
        assert sum((a.amount for a in allocations if a.charge_id == ch.id), Decimal(0)) <= ch.outstanding
    # Every (payment, charge) pair appears at most once (UNIQUE in payment_allocations).
    assert len({(a.payment_id, a.charge_id) for a in allocations}) == len(allocations)
    # Here the credit (133340.34) exceeds the charges (62444.69): every charge is covered, never more.
    assert sum(a.amount for a in allocations) == Decimal("62444.69")


def test_exact_decimals_never_rounded() -> None:
    assert allocate_credit([credit(1, "0.03")], [pending(10, "0.01"), pending(11, "0.01", date(2026, 3, 5))]) == result(
        (1, 10, "0.01"), (1, 11, "0.01")
    )


def test_inputs_are_never_modified() -> None:  # 19
    credits = [credit(2, "70000", date(2026, 1, 11)), credit(1, "50000")]
    charges = [pending(11, "30000", date(2026, 3, 5)), pending(10, "40000")]
    before = (copy.deepcopy(credits), copy.deepcopy(charges))
    first = allocate_credit(credits, charges)
    assert (credits, charges) == before  # same order and values (the dataclasses are frozen)
    assert allocate_credit(credits, charges) == first  # deterministic
    assert allocate_credit(tuple(reversed(credits)), tuple(reversed(charges))) == first  # independent of input order


def test_duplicated_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="Each payment"):
        allocate_credit([credit(1, "10"), credit(1, "20")], [pending(10, "50")])
    with pytest.raises(ValueError, match="Each charge"):
        allocate_credit([credit(1, "10")], [pending(10, "50"), pending(10, "60")])


# --- parse_period --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2026-10", date(2026, 10, 1)), ("2027-01", date(2027, 1, 1)), ("2026-12", date(2026, 12, 1)), ("2000-02", date(2000, 2, 1))],
)
def test_parse_period(value: str, expected: date) -> None:
    assert parse_period(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "", "2026", "26-10", "2026-1", "2026-010", "2026/10", "2026-13", "2026-00", "2026-10-01",
        " 2026-10", "2026-10 ", "2026-10\n", "x2026-10", "2026-10x", "2026_10", "0000-10",
        "２０２６-１０",  # full-width digits
        "+2026-10", "2026--10",
    ],
)
def test_parse_period_rejects_anything_else(value: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM"):
        parse_period(value)


@pytest.mark.parametrize("value", [None, 202610, date(2026, 10, 1)])
def test_parse_period_only_accepts_text(value: object) -> None:
    with pytest.raises(ValueError, match="YYYY-MM"):
        parse_period(value)  # type: ignore[arg-type]
