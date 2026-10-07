"""Feedback of one resident, read by the resident (HU-009, criteria 5.1 and 5.2). Read only:
no locks, no writes, no audit.

Ownership: every query carries feedback.resident_id = the resident given by
the caller (the future API takes it from the session, never from the client).
A feedback of another resident is "not found", exactly as a feedback that does
not exist (FeedbackNotFoundError, 404 "El feedback no existe.").

What the resident reads is only what the resident wrote, plus its status:
subject, message, category, urgency, status (PENDING or RESOLVED, as stored)
and the sending date (created_at, UTC). Never the administrator's internal
note (resolution_note), the response (admin_response, not part of HU-009 in
this stage), the resolution date, the administrator, the audit or anybody
else's data. category and urgency are NULL for the feedback stored before
HU-009 and are returned as NULL (nothing invented).

list  one COUNT and one page (ORDER BY created_at DESC, id DESC, LIMIT, OFFSET in MySQL)
get   one query
"""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FeedbackNotFoundError, FieldValidationError
from app.models import Feedback, FeedbackCategory, FeedbackStatus, FeedbackUrgency
from app.schemas.feedback import PAGE_MAX, PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX
from app.schemas.invitation import ID_MAX

# --- Query ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResidentFeedbackQuery:
    page: int = 1
    page_size: int = PAGE_SIZE_DEFAULT


def _positive_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= ID_MAX


def _validated(resident_id: Any, query: ResidentFeedbackQuery) -> ResidentFeedbackQuery:
    """The service never trusts its caller: the future API validates too, with the same rules."""
    if not _positive_id(resident_id):
        raise ValueError("resident_id is the signed-in resident (a positive id).")
    for field, value in (("page", query.page), ("page_size", query.page_size)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise FieldValidationError(field, "El valor no es válido.")
    if not 1 <= query.page <= PAGE_MAX:
        raise FieldValidationError("page", f"La página debe estar entre 1 y {PAGE_MAX}.")
    if not 1 <= query.page_size <= PAGE_SIZE_MAX:
        raise FieldValidationError("page_size", f"El tamaño de página debe estar entre 1 y {PAGE_SIZE_MAX}.")
    return query


# --- Results -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResidentFeedbackSummary:
    """One row of the resident's list (criterion 5.1)."""

    id: int
    subject: str | None
    category: FeedbackCategory | None  # None: stored before HU-009
    urgency: FeedbackUrgency | None  # None: stored before HU-009
    status: FeedbackStatus
    created_at: datetime  # sending date, UTC


@dataclass(frozen=True)
class ResidentFeedbackPage:
    items: tuple[ResidentFeedbackSummary, ...]
    page: int
    page_size: int
    total: int
    total_pages: int


@dataclass(frozen=True)
class ResidentFeedbackDetail:
    """One feedback of the resident (criterion 5.2: sending date, message and current status)."""

    id: int
    subject: str | None
    message: str
    category: FeedbackCategory | None
    urgency: FeedbackUrgency | None
    status: FeedbackStatus
    created_at: datetime  # sending date, UTC


def _category(value: str | None) -> FeedbackCategory | None:
    return None if value is None else FeedbackCategory(value)


def _urgency(value: str | None) -> FeedbackUrgency | None:
    return None if value is None else FeedbackUrgency(value)


# --- Queries -------------------------------------------------------------------------------------------


async def list_resident_feedback(
    db: AsyncSession, resident_id: int, query: ResidentFeedbackQuery
) -> ResidentFeedbackPage:
    """The resident's feedback, the most recent first; one COUNT and one page."""
    checked = _validated(resident_id, query)
    # Ownership first, always in SQL.
    owned = Feedback.resident_id == resident_id
    total = int(await db.scalar(select(func.count(Feedback.id)).where(owned)) or 0)
    total_pages = math.ceil(total / checked.page_size)
    items: tuple[ResidentFeedbackSummary, ...] = ()
    if checked.page <= total_pages:
        rows = (
            await db.execute(
                select(Feedback.id, Feedback.subject, Feedback.category, Feedback.urgency, Feedback.status,
                       Feedback.created_at)
                .where(owned)
                # feedback.id breaks ties in the same direction: a stable order across pages.
                .order_by(Feedback.created_at.desc(), Feedback.id.desc())
                .limit(checked.page_size)
                .offset((checked.page - 1) * checked.page_size)
            )
        ).all()
        items = tuple(
            ResidentFeedbackSummary(
                id=row.id, subject=row.subject, category=_category(row.category), urgency=_urgency(row.urgency),
                status=FeedbackStatus(row.status), created_at=row.created_at,
            )
            for row in rows
        )
    return ResidentFeedbackPage(items=items, page=checked.page, page_size=checked.page_size, total=total,
                                total_pages=total_pages)


async def get_resident_feedback(db: AsyncSession, resident_id: int, feedback_id: int) -> ResidentFeedbackDetail:
    """One feedback of the resident (one query).

    resident_id is required: a feedback id alone never authorizes anything. A
    feedback of another resident raises the same FeedbackNotFoundError as a missing one.
    """
    if not _positive_id(resident_id):
        raise ValueError("resident_id is the signed-in resident (a positive id).")
    if not _positive_id(feedback_id):
        raise FeedbackNotFoundError()
    row = (
        await db.execute(
            select(Feedback.id, Feedback.subject, Feedback.message, Feedback.category, Feedback.urgency,
                   Feedback.status, Feedback.created_at)
            .where(Feedback.id == feedback_id, Feedback.resident_id == resident_id)
        )
    ).first()
    if row is None:
        raise FeedbackNotFoundError()
    return ResidentFeedbackDetail(
        id=row.id, subject=row.subject, message=row.message, category=_category(row.category),
        urgency=_urgency(row.urgency), status=FeedbackStatus(row.status), created_at=row.created_at,
    )
