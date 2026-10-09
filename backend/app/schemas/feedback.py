"""Input and output of feedback management (HU-005, administrator).

Only two changes exist, each with its own endpoint and optimistic lock
(version): the response to the resident and the resolution (internal note).
The original message, the resident and the status are never set by the client.

HU-009: the rules of a new feedback (clean_subject, clean_text for the message,
clean_category, clean_urgency) live here too, shared by create_feedback and the
resident's API.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, field_validator

from app.core.validation import invalid
from app.models.feedback import SUBJECT_MAX_LENGTH, TEXT_MAX_LENGTH, FeedbackCategory, FeedbackStatus, FeedbackUrgency

SEARCH_MAX_LENGTH = 100
PAGE_SIZE_DEFAULT = 10
PAGE_SIZE_MAX = 100
PAGE_MAX = 10_000
VERSION_MAX = 2**32 - 1  # INT UNSIGNED


def _valid_version(value: int) -> int:
    if not 1 <= value <= VERSION_MAX:
        raise invalid("version_invalid", "La versión no es válida. Recarga la página.")
    return value


Version = Annotated[StrictInt, AfterValidator(_valid_version)]


def clean_text(value: Any, *, required: str, label: str) -> Any:
    """Trimmed text (inner line breaks kept), required and at most TEXT_MAX_LENGTH characters."""
    if value is None:
        raise invalid("text_required", required)
    if not isinstance(value, str):
        raise invalid("text_format", f"{label} debe ser un texto.")
    text = value.strip()
    if not text:
        raise invalid("text_required", required)
    if len(text) > TEXT_MAX_LENGTH:
        raise invalid("text_too_long", f"{label} no puede superar {TEXT_MAX_LENGTH} caracteres.")
    return text


CATEGORY_MESSAGE = "Selecciona una categoría: queja, sugerencia, solicitud u otros."
URGENCY_MESSAGE = "Selecciona la urgencia: alta, media o baja."
MESSAGE_REQUIRED = "Escribe el mensaje."


def clean_subject(value: Any) -> str | None:
    """HU-009: optional, one line (spaces collapsed), at most SUBJECT_MAX_LENGTH characters; empty is None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid("subject_format", "El asunto debe ser un texto.")
    subject = " ".join(value.split())
    if not subject:
        return None
    if len(subject) > SUBJECT_MAX_LENGTH:
        raise invalid("subject_too_long", f"El asunto no puede superar {SUBJECT_MAX_LENGTH} caracteres.")
    return subject


def clean_category(value: Any) -> FeedbackCategory:
    """HU-009: required, one of COMPLAINT, SUGGESTION, REQUEST, OTHER."""
    if not isinstance(value, str) or value not in {c.value for c in FeedbackCategory}:
        raise invalid("category_invalid", CATEGORY_MESSAGE)
    return FeedbackCategory(value)


def clean_urgency(value: Any) -> FeedbackUrgency:
    """HU-009: required, one of HIGH, MEDIUM, LOW."""
    if not isinstance(value, str) or value not in {u.value for u in FeedbackUrgency}:
        raise invalid("urgency_invalid", URGENCY_MESSAGE)
    return FeedbackUrgency(value)


class FeedbackResponse(BaseModel):
    """POST /admin/feedback/{id}/response."""

    model_config = ConfigDict(extra="forbid")

    response: str
    version: Version

    @field_validator("response", mode="before")
    @classmethod
    def validate_response(cls, value: Any) -> Any:
        return clean_text(value, required="Escribe la respuesta para el residente.", label="La respuesta")


class FeedbackResolution(BaseModel):
    """POST /admin/feedback/{id}/resolve."""

    model_config = ConfigDict(extra="forbid")

    resolution_note: str
    version: Version

    @field_validator("resolution_note", mode="before")
    @classmethod
    def validate_note(cls, value: Any) -> Any:
        return clean_text(value, required="Escribe la nota de resolución.", label="La nota de resolución")


# --- Responses -------------------------------------------------------------------------------------------


class ResidentRef(BaseModel):
    id: int
    full_name: str


class FeedbackListItem(BaseModel):
    id: int
    resident: ResidentRef
    subject: str | None
    category: FeedbackCategory | None  # HU-009; null: stored before HU-009
    urgency: FeedbackUrgency | None  # HU-009; null: stored before HU-009
    status: FeedbackStatus
    created_at: datetime  # UTC (timezone-aware)
    resolved_at: datetime | None


class FeedbackActor(BaseModel):
    id: int | None
    full_name: str | None  # None for system actions


class FeedbackAction(BaseModel):
    """One entry of the history (audit_logs): what, who and when. No internal context."""

    action: str
    actor: FeedbackActor
    at: datetime


class FeedbackResident(BaseModel):
    id: int
    full_name: str
    email: str
    unit_label: str | None  # current residence (None when there is none)


class FeedbackDetail(BaseModel):
    id: int
    resident: FeedbackResident
    subject: str | None
    message: str
    category: FeedbackCategory | None  # HU-009; null: stored before HU-009
    urgency: FeedbackUrgency | None  # HU-009; null: stored before HU-009
    status: FeedbackStatus
    admin_response: str | None
    resolution_note: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    version: int
    action_history: list[FeedbackAction]


class NotificationStatus(StrEnum):
    SENT = "SENT"
    FAILED = "FAILED"
    UNCONFIRMED = "UNCONFIRMED"  # sent or not, the result could not be recorded


class FeedbackActionResult(BaseModel):
    feedback: FeedbackDetail
    notification_status: NotificationStatus


# --- List ----------------------------------------------------------------------------------------------------


class FeedbackSortField(StrEnum):
    CREATED_AT = "created_at"
    RESIDENT = "resident"
    SUBJECT = "subject"
    STATUS = "status"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class FeedbackListQuery(BaseModel):
    """Query string of GET /admin/feedback. Unknown parameters are rejected."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=PAGE_MAX)
    page_size: int = Field(default=PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX)
    status: FeedbackStatus | None = None
    search: str | None = None
    sort_by: FeedbackSortField = FeedbackSortField.CREATED_AT
    sort_order: SortOrder = SortOrder.DESC

    @field_validator("search", mode="before")
    @classmethod
    def clean_search(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        search = value.strip()
        if len(search) > SEARCH_MAX_LENGTH:
            raise invalid("search_too_long", f"La búsqueda no puede superar {SEARCH_MAX_LENGTH} caracteres.")
        return search or None


class FeedbackPage(BaseModel):
    items: list[FeedbackListItem]
    page: int
    page_size: int
    total: int
    total_pages: int
