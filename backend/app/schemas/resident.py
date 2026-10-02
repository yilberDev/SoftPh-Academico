"""Registered residents seen by the administrator (HU-006). Read-only."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.validation import invalid

PAGE_SIZE_DEFAULT = 10
PAGE_SIZE_MAX = 100
# Technical limit: keeps OFFSET reasonable (PAGE_MAX * PAGE_SIZE_MAX rows).
PAGE_MAX = 10_000
SEARCH_MAX_LENGTH = 100


class ResidentStatus(StrEnum):
    """Status shown for a registered resident: ACTIVE account or DISABLED account (INACTIVE)."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ResidentSortField(StrEnum):
    # Whitelist: each value is translated to SQLAlchemy expressions by the service.
    NAME = "name"
    DOCUMENT = "document"
    UNIT = "unit"
    PHONE = "phone"
    EMAIL = "email"
    STATUS = "status"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class ResidentListQuery(BaseModel):
    """Query string of GET /admin/residents. Unknown parameters are rejected."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=PAGE_MAX)
    page_size: int = Field(default=PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX)
    search: str | None = None
    status: ResidentStatus | None = None
    sort_by: ResidentSortField = ResidentSortField.NAME
    sort_order: SortOrder = SortOrder.ASC

    @field_validator("search", mode="before")
    @classmethod
    def clean_search(cls, value: Any) -> Any:
        """Name or document fragment: stripped; empty means no search."""
        if not isinstance(value, str):
            return value
        search = value.strip()
        if len(search) > SEARCH_MAX_LENGTH:
            raise invalid("search_too_long", f"La búsqueda no puede superar {SEARCH_MAX_LENGTH} caracteres.")
        return search or None


class ResidentUnitRef(BaseModel):
    id: int
    label: str  # derived (HU-013), e.g. "B1-TO2-APTO-104"; never stored


class ResidentRead(BaseModel):
    id: int
    full_name: str
    document_number: str | None
    phone: str | None
    email: str
    unit: ResidentUnitRef
    status: ResidentStatus


class ResidentPage(BaseModel):
    items: list[ResidentRead]
    page: int
    page_size: int
    total: int
    total_pages: int
