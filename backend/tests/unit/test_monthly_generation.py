"""Obligations extension, monthly generation: pure rules (parameters, preview fingerprint, totals)."""

import inspect
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.core.exceptions import FieldValidationError
from app.services import charges, monthly_rules
from app.services.charges import monthly_params
from app.services.monthly_rules import (
    MonthlyParams,
    OutcomeKind,
    UnitIssue,
    UnitOutcome,
    preview_fingerprint,
    summarize,
)

PARAMS = MonthlyParams(date(2026, 10, 1), Decimal("180000"), date(2026, 10, 10))


# --- Parameters ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("period", "due", "expected"),
    [
        ("2026-10", date(2026, 10, 10), date(2026, 10, 1)),
        ("2026-10", date(2026, 11, 10), date(2026, 10, 1)),  # due in the next month
        ("2026-10", date(2026, 10, 1), date(2026, 10, 1)),  # due on the first day
        ("1998-01", date(1998, 1, 5), date(1998, 1, 1)),  # historical: no year limit
        ("2100-12", date(2100, 12, 31), date(2100, 12, 1)),  # future: no year limit
    ],
)
def test_valid_parameters(period: str, due: date, expected: date) -> None:
    params = monthly_params(period, Decimal("180000.5"), due)
    assert params == MonthlyParams(expected, Decimal("180000.5"), due)


@pytest.mark.parametrize("period", ["", "2026", "2026-1", "2026-13", "2026-00", "2026/10", "2026-10-01", " 2026-10", None, 202610])
def test_invalid_billing_period(period: Any) -> None:
    with pytest.raises(FieldValidationError) as error:
        monthly_params(period, Decimal("1"), date(2026, 10, 10))
    assert error.value.details == [{"field": "billing_period", "message": "El periodo debe tener el formato AAAA-MM."}]


@pytest.mark.parametrize(
    ("due", "message"),
    [
        (date(2026, 9, 30), "El vencimiento no puede ser anterior al inicio del periodo."),
        (datetime(2026, 10, 10), "La fecha de vencimiento no es válida."),
        ("2026-10-10", "La fecha de vencimiento no es válida."),
        (None, "La fecha de vencimiento no es válida."),
    ],
)
def test_invalid_due_date(due: Any, message: str) -> None:
    with pytest.raises(FieldValidationError) as error:
        monthly_params("2026-10", Decimal("1"), due)
    assert error.value.details == [{"field": "due_date", "message": message}]


@pytest.mark.parametrize(
    ("amount", "message"),
    [
        (Decimal("0"), "El monto debe ser mayor que cero."),
        (Decimal("-1"), "El monto debe ser mayor que cero."),
        (Decimal("0.001"), "El monto admite hasta 2 decimales."),
        (Decimal("10000000000"), "El monto no puede superar $ 9.999.999.999,99."),
        (Decimal("NaN"), "El monto no es válido."),
        (180000.0, "El monto no es válido."),
    ],
)
def test_invalid_amount(amount: Any, message: str) -> None:
    with pytest.raises(FieldValidationError) as error:
        monthly_params("2026-10", amount, date(2026, 10, 10))
    assert error.value.details == [{"field": "amount", "message": message}]


def test_amounts_are_exact() -> None:
    assert monthly_params("2026-10", Decimal("0.01"), date(2026, 10, 10)).amount == Decimal("0.01")
    assert monthly_params("2026-10", Decimal("9999999999.99"), date(2026, 10, 10)).amount == Decimal("9999999999.99")


def test_no_fixed_number_of_apartments() -> None:
    """The scenario has 336 apartments today; that is data, never a constant or a limit."""
    for module in (charges, monthly_rules):
        source = inspect.getsource(module)
        assert not re.search(r"(?<![\d.])(336|500|1000)(?![\d.])", source), module.__name__
        assert not re.search(r"MAX_UNITS|UNIT_LIMIT", source), module.__name__


# --- preview_fingerprint -------------------------------------------------------------------------------------


def test_fingerprint_is_a_sha256_hex_digest() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", preview_fingerprint(PARAMS, [1, 2, 3], [1]))


def test_fingerprint_is_deterministic_and_independent_of_order() -> None:
    first = preview_fingerprint(PARAMS, [3, 1, 2], [2, 1])
    assert first == preview_fingerprint(PARAMS, [1, 2, 3], [1, 2])
    assert first == preview_fingerprint(PARAMS, (2, 3, 1), (1, 2))
    # The amount is normalized to 2 decimals: 180000 and 180000.00 are the same preview.
    same_amount = MonthlyParams(PARAMS.billing_period, Decimal("180000.00"), PARAMS.due_date)
    assert first == preview_fingerprint(same_amount, [1, 2, 3], [1, 2])


def test_fingerprint_canonical_text() -> None:
    import hashlib

    canonical = "v1|2026-10-01|180000.00|2026-10-10|A:1,2,3|E:1"
    assert preview_fingerprint(PARAMS, [3, 2, 1], [1]) == hashlib.sha256(canonical.encode()).hexdigest()
    # Unambiguous: [1, 23] and [12, 3] are different apartments.
    assert preview_fingerprint(PARAMS, [1, 23], []) != preview_fingerprint(PARAMS, [12, 3], [])


@pytest.mark.parametrize(
    ("params", "active", "existing"),
    [
        (PARAMS, [1, 2, 4], [1]),  # same count, another apartment (composition)
        (PARAMS, [1, 2], [1]),  # an apartment less
        (PARAMS, [1, 2, 3, 4], [1]),  # an apartment more
        (PARAMS, [1, 2, 3], [1, 2]),  # a monthly fee created meanwhile
        (PARAMS, [1, 2, 3], [2]),  # same count of fees, another apartment
        (MonthlyParams(date(2026, 11, 1), PARAMS.amount, date(2026, 11, 10)), [1, 2, 3], [1]),
        (MonthlyParams(PARAMS.billing_period, Decimal("180000.01"), PARAMS.due_date), [1, 2, 3], [1]),
        (MonthlyParams(PARAMS.billing_period, PARAMS.amount, date(2026, 10, 11)), [1, 2, 3], [1]),
    ],
)
def test_any_change_gives_another_fingerprint(params: MonthlyParams, active: list[int], existing: list[int]) -> None:
    assert preview_fingerprint(params, active, existing) != preview_fingerprint(PARAMS, [1, 2, 3], [1])


def test_fingerprint_of_any_size() -> None:
    # No fixed number of apartments: thousands work the same way.
    many = list(range(1, 25_001))
    assert preview_fingerprint(PARAMS, many, []) != preview_fingerprint(PARAMS, many[:-1], [])
    assert preview_fingerprint(PARAMS, [], []) != preview_fingerprint(PARAMS, [1], [])


# --- summarize -----------------------------------------------------------------------------------------------


def created(unit_id: int, amount: str = "180000", to_new: str = "0", total: str = "0") -> UnitOutcome:
    return UnitOutcome(unit_id, f"U{unit_id}", OutcomeKind.CREATED, amount=Decimal(amount),
                       credit_to_new_charge=Decimal(to_new), credit_total=Decimal(total),
                       outstanding=Decimal(amount) - Decimal(to_new))


def test_totals_and_consistency() -> None:
    outcomes = [
        created(1),
        created(2, to_new="180000", total="180000"),
        created(3, to_new="50000", total="80000"),  # FIFO also covered an older charge
        UnitOutcome(4, "U4", OutcomeKind.EXISTING),
        UnitOutcome(5, "U5", OutcomeKind.OMITTED, "UNIT_DELETED", "El apartamento fue eliminado."),
        UnitOutcome(6, "U6", OutcomeKind.FAILED, "INTERNAL_ERROR", "No fue posible generar la mensualidad de este apartamento."),
        UnitOutcome(7, "U7", OutcomeKind.EXISTING),
    ]
    totals = summarize(outcomes)
    assert (totals.units_evaluated, totals.created, totals.existing, totals.omitted, totals.failed) == (7, 3, 2, 1, 1)
    assert totals.existing + totals.created + totals.omitted + totals.failed == totals.units_evaluated
    assert totals.generated_total == Decimal("540000")
    assert totals.credit_applied_total == Decimal("260000")
    assert totals.credit_applied_to_new_charges == Decimal("230000")
    assert totals.units_with_credit_applied == 2
    assert totals.outstanding_total == Decimal("310000")
    assert totals.omitted_units == (UnitIssue(5, "U5", "UNIT_DELETED", "El apartamento fue eliminado."),)
    assert totals.errors == (UnitIssue(6, "U6", "INTERNAL_ERROR", "No fue posible generar la mensualidad de este apartamento."),)


def test_empty_run() -> None:
    totals = summarize([])
    assert (totals.units_evaluated, totals.created, totals.generated_total, totals.errors) == (0, 0, Decimal("0.00"), ())


def test_only_existing() -> None:
    totals = summarize([UnitOutcome(i, f"U{i}", OutcomeKind.EXISTING) for i in range(1, 401)])
    assert (totals.units_evaluated, totals.existing, totals.created) == (400, 400, 0)


def test_each_apartment_is_counted_once() -> None:
    with pytest.raises(ValueError, match="exactly one outcome"):
        summarize([created(1), UnitOutcome(1, "U1", OutcomeKind.EXISTING)])


def test_exact_decimals() -> None:
    totals = summarize([created(1, "0.01", "0.01", "0.03"), created(2, "0.02")])
    assert (totals.generated_total, totals.credit_applied_total, totals.outstanding_total) == (
        Decimal("0.03"), Decimal("0.03"), Decimal("0.02"))
