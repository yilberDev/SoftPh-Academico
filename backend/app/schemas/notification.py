"""HU-017: the signed-in resident's notifications (read only).

Only page and page_size are accepted (extra="forbid": resident_id, user_id, ... are 422). The answer
never carries the email address, the error of a failed delivery or the event key.
"""

from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from app.models import NotificationEmailStatus, NotificationEvent
from app.schemas.payment import PAGE_MAX, PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX
from app.services.notifications import NotificationItem, NotificationPage


class ResidentNotificationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=PAGE_MAX)
    page_size: int = Field(default=PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX)


def _utc(moment: datetime | None) -> datetime | None:
    return None if moment is None else moment.replace(tzinfo=UTC)


class ResidentNotificationRead(BaseModel):
    id: int
    event_type: NotificationEvent  # what originated it (HU-017 criterion 4.1)
    entity_type: str  # "payment" | "feedback"
    entity_id: int  # the resident's own payment or feedback
    title: str
    message: str
    email_status: NotificationEmailStatus  # PENDING | SENT | FAILED | NO_CONTACT (CU-017 6, 2a, 4a)
    created_at: datetime  # UTC
    sent_at: datetime | None  # UTC, only when SENT

    @classmethod
    def from_item(cls, item: NotificationItem) -> Self:
        return cls(
            id=item.id, event_type=item.event_type, entity_type=item.entity_type, entity_id=item.entity_id,
            title=item.title, message=item.message, email_status=item.email_status,
            created_at=_utc(item.created_at), sent_at=_utc(item.sent_at),  # type: ignore[arg-type]
        )


class ResidentNotificationPage(BaseModel):
    items: list[ResidentNotificationRead]
    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def from_page(cls, page: NotificationPage) -> Self:
        return cls(items=[ResidentNotificationRead.from_item(i) for i in page.items], page=page.page,
                   page_size=page.page_size, total=page.total, total_pages=page.total_pages)
