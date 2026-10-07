"""HU-008: the resident's payment query without a database (search and validation)."""

from datetime import date
from typing import Any

import pytest

from app.core.exceptions import FieldValidationError
from app.services.allocation import PaymentStatus, receipt_number
from app.services.resident_payments import (
    SEARCH_MESSAGE,
    PaymentSearch,
    ResidentPaymentQuery,
    _validated,
    parse_search,
)


def field_error(error: pytest.ExceptionInfo[FieldValidationError]) -> tuple[str, str]:
    [detail] = error.value.details
    return detail["field"], detail["message"]


# --- Search: a receipt number or a specific date ------------------------------------------------------------


@pytest.mark.parametrize("text", ["RC-00000012", "rc-00000012", "RC12", "RC-12", "12", "0012", " 12 ", receipt_number(12)])
def test_receipt_numbers_mean_the_payment_id(text: str) -> None:
    assert parse_search(text) == PaymentSearch(payment_id=12)


def test_a_date_means_that_payment_date() -> None:
    assert parse_search("2026-10-05") == PaymentSearch(payment_date=date(2026, 10, 5))
    assert parse_search(" 2026-02-28 ") == PaymentSearch(payment_date=date(2026, 2, 28))


@pytest.mark.parametrize("text", [None, "", "   "])
def test_blank_is_no_search(text: Any) -> None:
    assert parse_search(text) is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Laura", SEARCH_MESSAGE),
        ("RC-12a", SEARCH_MESSAGE),
        ("12.5", SEARCH_MESSAGE),
        ("-12", SEARCH_MESSAGE),
        ("05/10/2026", SEARCH_MESSAGE),
        ("2026-10-5", SEARCH_MESSAGE),
        ("١٢", SEARCH_MESSAGE),  # other scripts' digits are not receipt numbers
        ("%", SEARCH_MESSAGE),
        ("2026-02-30", "La fecha buscada no es válida."),
        ("RC-00000000", "El número de recibo no es válido."),
        ("99999999999999999999", SEARCH_MESSAGE),  # more than 19 digits
        ("9223372036854775808", "El número de recibo no es válido."),  # beyond BIGINT
        ("x" * 101, "La búsqueda no puede superar 100 caracteres."),
    ],
)
def test_anything_else_is_a_validation_error(text: str, message: str) -> None:
    with pytest.raises(FieldValidationError) as error:
        parse_search(text)
    assert field_error(error) == ("search", message)


def test_a_non_text_search_is_rejected() -> None:
    with pytest.raises(FieldValidationError):
        parse_search(12)


# --- Validation of the query (the service never trusts its caller) ---------------------------------------------


def test_defaults() -> None:
    checked = _validated(7, ResidentPaymentQuery())
    assert (checked.query.page, checked.query.page_size, checked.query.status, checked.search) == (1, 10, None, None)


def test_status_values_are_the_existing_payment_statuses() -> None:
    for status in PaymentStatus:
        assert _validated(7, ResidentPaymentQuery(status=status.value)).query.status is status  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (ResidentPaymentQuery(page=0), ("page", "La página debe estar entre 1 y 10000.")),
        (ResidentPaymentQuery(page=10_001), ("page", "La página debe estar entre 1 y 10000.")),
        (ResidentPaymentQuery(page=True), ("page", "El valor no es válido.")),  # type: ignore[arg-type]
        (ResidentPaymentQuery(page_size=0), ("page_size", "El tamaño de página debe estar entre 1 y 100.")),
        (ResidentPaymentQuery(page_size=101), ("page_size", "El tamaño de página debe estar entre 1 y 100.")),
        (ResidentPaymentQuery(page_size="10"), ("page_size", "El valor no es válido.")),  # type: ignore[arg-type]
        (ResidentPaymentQuery(status="PAID"), ("status", "Selecciona un estado válido.")),  # type: ignore[arg-type]
        (ResidentPaymentQuery(date_from="2026-10-01"), ("date_from", "La fecha no es válida.")),  # type: ignore[arg-type]
        (ResidentPaymentQuery(date_from=date(2026, 10, 2), date_to=date(2026, 10, 1)),
         ("date_to", "La fecha final no puede ser anterior a la inicial.")),
        (ResidentPaymentQuery(search="Laura"), ("search", SEARCH_MESSAGE)),
    ],
)
def test_invalid_queries_point_at_the_field(query: ResidentPaymentQuery, expected: tuple[str, str]) -> None:
    with pytest.raises(FieldValidationError) as error:
        _validated(7, query)
    assert field_error(error) == expected


def test_same_day_range_is_valid() -> None:
    checked = _validated(7, ResidentPaymentQuery(date_from=date(2026, 10, 1), date_to=date(2026, 10, 1)))
    assert checked.query.date_from == checked.query.date_to == date(2026, 10, 1)


@pytest.mark.parametrize("resident_id", [None, 0, -1, True, "7", 2**63])
def test_the_resident_is_always_required(resident_id: Any) -> None:
    # Ownership cannot be skipped: there is no "every resident" query.
    with pytest.raises(ValueError, match="resident_id"):
        _validated(resident_id, ResidentPaymentQuery())
