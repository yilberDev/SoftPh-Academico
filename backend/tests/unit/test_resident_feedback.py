"""HU-009: the resident's feedback query without a database (validation and exposed fields)."""

from dataclasses import fields
from typing import Any

import pytest

from app.core.exceptions import FieldValidationError
from app.services.resident_feedback import (
    ResidentFeedbackDetail,
    ResidentFeedbackQuery,
    ResidentFeedbackSummary,
    _validated,
)


def field_error(error: pytest.ExceptionInfo[FieldValidationError]) -> tuple[str, str]:
    [detail] = error.value.details  # type: ignore[misc]
    return detail["field"], detail["message"]


def test_defaults() -> None:
    assert _validated(7, ResidentFeedbackQuery()) == ResidentFeedbackQuery(page=1, page_size=10)
    assert _validated(7, ResidentFeedbackQuery(page=10_000, page_size=100)).page_size == 100


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (ResidentFeedbackQuery(page=0), ("page", "La página debe estar entre 1 y 10000.")),
        (ResidentFeedbackQuery(page=-1), ("page", "La página debe estar entre 1 y 10000.")),
        (ResidentFeedbackQuery(page=10_001), ("page", "La página debe estar entre 1 y 10000.")),
        (ResidentFeedbackQuery(page=True), ("page", "El valor no es válido.")),  # type: ignore[arg-type]
        (ResidentFeedbackQuery(page="1"), ("page", "El valor no es válido.")),  # type: ignore[arg-type]
        (ResidentFeedbackQuery(page_size=0), ("page_size", "El tamaño de página debe estar entre 1 y 100.")),
        (ResidentFeedbackQuery(page_size=101), ("page_size", "El tamaño de página debe estar entre 1 y 100.")),
        (ResidentFeedbackQuery(page_size=10.0), ("page_size", "El valor no es válido.")),  # type: ignore[arg-type]
    ],
)
def test_invalid_pages_point_at_the_field(query: ResidentFeedbackQuery, expected: tuple[str, str]) -> None:
    with pytest.raises(FieldValidationError) as error:
        _validated(7, query)
    assert field_error(error) == expected


@pytest.mark.parametrize("resident_id", [None, 0, -1, True, "7", 2**63])
def test_the_resident_is_always_required(resident_id: Any) -> None:
    # Ownership cannot be skipped: there is no "every resident" query.
    with pytest.raises(ValueError, match="resident_id"):
        _validated(resident_id, ResidentFeedbackQuery())


def test_only_the_residents_own_data_can_be_returned() -> None:
    # What the resident wrote and its status; never the administrator's fields, the resident or the audit.
    assert [f.name for f in fields(ResidentFeedbackSummary)] == ["id", "subject", "category", "urgency", "status",
                                                                  "created_at"]
    assert [f.name for f in fields(ResidentFeedbackDetail)] == ["id", "subject", "message", "category", "urgency",
                                                                 "status", "created_at"]
