"""HU-006: query string of the registered residents list (whitelist and limits)."""

import pytest
from pydantic import ValidationError

from app.schemas.resident import (
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    ResidentListQuery,
    ResidentSortField,
    ResidentStatus,
    SortOrder,
)


def test_defaults() -> None:
    query = ResidentListQuery()
    assert (query.page, query.page_size, query.search, query.status) == (1, PAGE_SIZE_DEFAULT, None, None)
    assert (query.sort_by, query.sort_order) == (ResidentSortField.NAME, SortOrder.ASC)


def test_whitelists() -> None:
    assert {field.value for field in ResidentSortField} == {"name", "document", "unit", "phone", "email", "status"}
    assert {order.value for order in SortOrder} == {"asc", "desc"}
    assert {status.value for status in ResidentStatus} == {"ACTIVE", "INACTIVE"}


@pytest.mark.parametrize(("raw", "expected"), [("  Laura  ", "Laura"), ("", None), ("   ", None), ("100%", "100%")])
def test_search_is_stripped(raw: str, expected: str | None) -> None:
    assert ResidentListQuery(search=raw).search == expected


@pytest.mark.parametrize(
    "fields",
    [
        {"page_size": 0}, {"page_size": PAGE_SIZE_MAX + 1}, {"page": 0}, {"search": "x" * 101},
        {"sort_by": "users.password_hash"}, {"sort_order": "DESC"}, {"status": "PENDING_ACTIVATION"},
        {"role": "ADMINISTRADOR"},
    ],
)
def test_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ResidentListQuery.model_validate(fields)


def test_search_limit_message() -> None:
    with pytest.raises(ValidationError) as error:
        ResidentListQuery(search="x" * 101)
    assert "La búsqueda no puede superar 100 caracteres." in str(error.value)
