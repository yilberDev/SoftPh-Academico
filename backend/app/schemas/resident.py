"""Registered residents seen by the administrator: list (HU-006), detail, update and deletion (HU-015)."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.validation import invalid
from app.schemas.user import clean_document_number, clean_full_name, clean_phone

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


# --- HU-015: detail, update and deletion ------------------------------------------------------------------


def utc_naive(moment: datetime) -> datetime:
    """The UTC wall time MySQL stores (DATETIME(6), no zone); a naive value is already UTC."""
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo is not None else moment


class ResidentDetail(ResidentRead):
    """One registered resident, as the administrator manages it (HU-015).

    updated_at is the optimistic-locking token: users has no version column, and MySQL sets
    updated_at (DATETIME(6), ON UPDATE) on every change of the row. The update and the deletion
    send back the value they read; another value means someone changed the resident meanwhile.
    """

    updated_at: datetime  # UTC, with microseconds


class ResidentUpdate(BaseModel):
    """The data of the resident's registration that the administrator may correct (HU-015 criterion 1.2).

    Exactly the fields the resident completed when the account was created: the full name
    (HU-003) and the document and phone (HU-019), with the same rules. The email (the login),
    the role, the account status and the apartment are not editable here: unknown fields are
    rejected (extra="forbid"). Every field must be sent (the form sends them all): a missing one
    is "incomplete information" (criterion 2), never "keep the old value".
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str
    document_number: str
    phone: str | None  # required key; null or "" removes the phone (it is optional, as in HU-019)
    updated_at: datetime  # the token read with the resident (ResidentDetail.updated_at)

    @field_validator("full_name", mode="before")
    @classmethod
    def validate_full_name(cls, value: Any) -> Any:
        return clean_full_name(value)

    @field_validator("document_number", mode="before")
    @classmethod
    def validate_document_number(cls, value: Any) -> Any:
        document = clean_document_number(value)
        if document is None:
            # A registered resident always has one: HU-019 requires it to activate the account.
            raise invalid("document_number_required", "El número de documento es obligatorio.")
        return document

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, value: Any) -> Any:
        return clean_phone(value)

    @field_validator("updated_at")
    @classmethod
    def as_utc(cls, value: datetime) -> datetime:
        return utc_naive(value)
