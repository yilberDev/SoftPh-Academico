"""Obligations extension, list and detail: validation of the query (no database)."""

from datetime import date, datetime
from typing import Any

import pytest

from app.core.exceptions import FieldValidationError
from app.services.allocation import ChargeStatus
from app.services.charges import (
    LIST_PAGE_MAX,
    LIST_PAGE_SIZE_MAX,
    ChargeListQuery,
    ChargeSortField,
    _sort_keys,
    _validated_query,
)

TODAY = date(2026, 10, 15)


def field_of(**changes: Any) -> str:
    with pytest.raises(FieldValidationError) as error:
        _validated_query(ChargeListQuery(**changes), TODAY)
    [detail] = error.value.details or []
    return detail["field"]


def test_defaults() -> None:
    query = _validated_query(ChargeListQuery(), TODAY)
    assert (query.page, query.page_size, query.sort_by, query.sort_order) == (1, 10, ChargeSortField.DUE_DATE, "asc")


def test_values_from_text_become_enums() -> None:
    query = _validated_query(ChargeListQuery(concept="LATE_FEE", status="OVERDUE", sort_by="outstanding",  # type: ignore[arg-type]
                                             sort_order="desc"), TODAY)  # type: ignore[arg-type]
    assert (query.concept, query.status, query.sort_by, query.sort_order) == (
        "LATE_FEE", ChargeStatus.OVERDUE, ChargeSortField.OUTSTANDING, "desc")


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"page": 0}, "page"), ({"page": LIST_PAGE_MAX + 1}, "page"), ({"page": True}, "page"), ({"page": "1"}, "page"),
        ({"page_size": 0}, "page_size"), ({"page_size": LIST_PAGE_SIZE_MAX + 1}, "page_size"),
        ({"unit_id": 0}, "unit_id"), ({"unit_id": -3}, "unit_id"), ({"unit_id": 2**63}, "unit_id"), ({"unit_id": True}, "unit_id"),
        ({"block_id": 0}, "block_id"), ({"tower_id": "2"}, "tower_id"),
        ({"concept": "RENT"}, "concept"),  # never an invented concept
        ({"status": "PAGADA"}, "status"), ({"status": "paid"}, "status"),
        ({"billing_period": date(2026, 10, 2)}, "billing_period"), ({"billing_period": datetime(2026, 10, 1)}, "billing_period"),
        ({"billing_period": "2026-10"}, "billing_period"),
        ({"due_from": "2026-10-01"}, "due_from"), ({"due_to": datetime(2026, 10, 1)}, "due_to"),
        ({"due_from": date(2026, 10, 2), "due_to": date(2026, 10, 1)}, "due_to"),
        ({"sort_by": "id; DROP TABLE charges"}, "sort_by"), ({"sort_by": "due_date", "sort_order": "sideways"}, "sort_order"),
    ],
)
def test_invalid_queries(changes: dict[str, Any], field: str) -> None:
    assert field_of(**changes) == field


def test_inclusive_single_day_range_is_valid() -> None:
    query = _validated_query(ChargeListQuery(due_from=date(2026, 10, 1), due_to=date(2026, 10, 1)), TODAY)
    assert query.due_from == query.due_to == date(2026, 10, 1)


def test_today_must_be_a_calendar_date() -> None:
    with pytest.raises(ValueError, match="calendar date"):
        _validated_query(ChargeListQuery(), datetime(2026, 10, 15, 8))  # type: ignore[arg-type]


@pytest.mark.parametrize("field", list(ChargeSortField))
def test_every_sort_field_maps_to_sql_expressions(field: ChargeSortField) -> None:
    # A whitelist: each allowed name has its own expressions, never the client's text.
    keys = _sort_keys(field, applied=0, status="x")
    assert keys and all(not isinstance(k, str) or k == "x" for k in keys)


def test_sort_fields_are_exactly_the_allowed_ones() -> None:
    assert {f.value for f in ChargeSortField} == {"due_date", "unit", "concept", "amount", "outstanding", "status", "created"}
