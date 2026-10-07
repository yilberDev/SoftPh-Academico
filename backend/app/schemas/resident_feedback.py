"""HU-009: the signed-in resident's feedback (POST/GET /resident/feedback and GET /{feedback_id}).

The resident is always the session's, never a field or a parameter: unknown
body fields (resident_id, status, admin_response, resolution_note, ...) and
unknown query parameters are rejected with 422 (extra="forbid"). The body uses
the rules of a new feedback prepared in HU-009 (schemas/feedback.py); the API
codes of category and urgency are the only accepted values (the Spanish labels
belong to the frontend). The responses carry exactly what services/
resident_feedback.py returns: never the administrator's response or internal
note, the resolution date, the version, the resident, the IP or the audit.
category and urgency are null for the feedback stored before HU-009.
"""

from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.feedback import FeedbackCategory, FeedbackStatus, FeedbackUrgency
from app.schemas.feedback import (
    MESSAGE_REQUIRED,
    PAGE_MAX,
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    clean_category,
    clean_subject,
    clean_text,
    clean_urgency,
)
from app.services.resident_feedback import ResidentFeedbackDetail, ResidentFeedbackSummary
from app.services.resident_feedback import ResidentFeedbackPage as ResidentFeedbackResult


def _utc(moment: datetime) -> datetime:
    # Stored as naive UTC; the API always says it is UTC (same convention as HU-004).
    return moment.replace(tzinfo=UTC)


class ResidentFeedbackCreate(BaseModel):
    """POST /resident/feedback: what the resident writes (criteria 2.1-2.3 and 3)."""

    model_config = ConfigDict(extra="forbid")

    # Validated even when omitted, so that a missing field gets its own message.
    subject: str | None = None
    message: str = Field(default=None, validate_default=True)  # type: ignore[assignment]
    category: FeedbackCategory = Field(default=None, validate_default=True)  # type: ignore[assignment]
    urgency: FeedbackUrgency = Field(default=None, validate_default=True)  # type: ignore[assignment]

    @field_validator("subject", mode="before")
    @classmethod
    def validate_subject(cls, value: Any) -> Any:
        return clean_subject(value)

    @field_validator("message", mode="before")
    @classmethod
    def validate_message(cls, value: Any) -> Any:
        return clean_text(value, required=MESSAGE_REQUIRED, label="El mensaje")

    @field_validator("category", mode="before")
    @classmethod
    def validate_category(cls, value: Any) -> Any:
        return clean_category(value)

    @field_validator("urgency", mode="before")
    @classmethod
    def validate_urgency(cls, value: Any) -> Any:
        return clean_urgency(value)


class ResidentFeedbackListQuery(BaseModel):
    """Query string of GET /resident/feedback. Unknown parameters are rejected."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=PAGE_MAX)
    page_size: int = Field(default=PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX)


# --- Responses --------------------------------------------------------------------------------------


class ResidentFeedbackItem(BaseModel):
    """One row of the resident's list (criterion 5.1)."""

    id: int
    subject: str | None
    category: FeedbackCategory | None  # null: stored before HU-009
    urgency: FeedbackUrgency | None  # null: stored before HU-009
    status: FeedbackStatus
    created_at: datetime  # sending date, UTC

    @classmethod
    def from_summary(cls, item: ResidentFeedbackSummary) -> Self:
        return cls(id=item.id, subject=item.subject, category=item.category, urgency=item.urgency, status=item.status,
                   created_at=_utc(item.created_at))


class ResidentFeedbackPage(BaseModel):
    items: list[ResidentFeedbackItem]
    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def from_page(cls, page: ResidentFeedbackResult) -> Self:
        return cls(items=[ResidentFeedbackItem.from_summary(i) for i in page.items], page=page.page,
                   page_size=page.page_size, total=page.total, total_pages=page.total_pages)


class ResidentFeedbackRead(BaseModel):
    """One feedback of the resident (criterion 5.2), also the answer of POST."""

    id: int
    subject: str | None
    message: str
    category: FeedbackCategory | None
    urgency: FeedbackUrgency | None
    status: FeedbackStatus
    created_at: datetime  # sending date, UTC

    @classmethod
    def from_detail(cls, detail: ResidentFeedbackDetail) -> Self:
        return cls(id=detail.id, subject=detail.subject, message=detail.message, category=detail.category,
                   urgency=detail.urgency, status=detail.status, created_at=_utc(detail.created_at))
