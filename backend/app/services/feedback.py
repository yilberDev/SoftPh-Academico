"""Feedback management by the administrator (HU-005).

respond   BEGIN · lock the feedback (FOR UPDATE) · same version? · still PENDING? ·
          admin_response, version + 1 · FEEDBACK_RESPONDED · COMMIT ·
          notification to the resident (no transaction or lock open) ·
          FEEDBACK_NOTIFICATION_SENT / _FAILED (another transaction).
resolve   The same, but: resolution_note (internal), status RESOLVED, resolved_at;
          FEEDBACK_RESOLVED. Never RESOLVED → PENDING (no reopening in HU-005).

A failed notification never undoes the change. The original message, the
resident and the status are never taken from the client. The history of
actions is audit_logs (entity_type 'feedback'); reading is not audited.

create_feedback is the domain rule for who can own a feedback (an activated,
non-deleted RESIDENTE). HU-005 has no endpoint for it: the tests use it to
create data, and HU-009 will use it for the resident's form.
"""

import logging
import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.clock import utc_now
from app.core.exceptions import (
    FeedbackAlreadyResolvedError,
    FeedbackNotFoundError,
    FeedbackResidentNotValidError,
    FeedbackVersionConflictError,
)
from app.core.unit_labels import unit_label
from app.models import (
    AuditAction,
    AuditLog,
    Block,
    Feedback,
    FeedbackStatus,
    ResidentUnit,
    Tower,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.schemas.feedback import (
    FeedbackAction,
    FeedbackActionResult,
    FeedbackActor,
    FeedbackDetail,
    FeedbackListItem,
    FeedbackListQuery,
    FeedbackPage,
    FeedbackResident,
    FeedbackResolution,
    FeedbackResponse,
    FeedbackSortField,
    NotificationStatus,
    ResidentRef,
    SortOrder,
)
from app.services.audit import record_audit
from app.services.feedback_email import FeedbackNotice, build_feedback_email
from app.services.mail import MailSender
from app.services.structure import Actor

logger = logging.getLogger(__name__)

ENTITY = "feedback"
# The actions shown in the history of a feedback (writes only).
HISTORY_ACTIONS = (
    AuditAction.FEEDBACK_RESPONDED,
    AuditAction.FEEDBACK_RESOLVED,
    AuditAction.FEEDBACK_NOTIFICATION_SENT,
    AuditAction.FEEDBACK_NOTIFICATION_FAILED,
)


def _utc(moment: datetime | None) -> datetime | None:
    return moment.replace(tzinfo=UTC) if moment is not None else None


# --- Owner of a feedback (domain rule; used by HU-009) ----------------------------------------------


def can_own_feedback(user: User | None) -> bool:
    return (
        user is not None
        and user.role == UserRole.RESIDENTE
        and user.deleted_at is None
        and user.status == UserStatus.ACTIVE
    )


async def create_feedback(db: AsyncSession, resident_id: int, *, subject: str | None, message: str) -> int:
    """Adds a PENDING feedback of an activated resident to the caller's transaction (no commit)."""
    resident = await db.scalar(
        select(User).where(User.id == resident_id).with_for_update(read=True).execution_options(populate_existing=True)
    )
    if not can_own_feedback(resident):
        raise FeedbackResidentNotValidError()
    feedback = Feedback(resident_id=resident_id, subject=subject, message=message, status=FeedbackStatus.PENDING)
    db.add(feedback)
    await db.flush()
    return feedback.id


# --- Detail ------------------------------------------------------------------------------------------------


async def _history(db: AsyncSession, feedback_id: int) -> list[FeedbackAction]:
    actor = aliased(User)
    rows = await db.execute(
        select(AuditLog.action, AuditLog.created_at, AuditLog.actor_user_id, actor.full_name)
        .outerjoin(actor, actor.id == AuditLog.actor_user_id)
        .where(AuditLog.entity_type == ENTITY, AuditLog.entity_id == feedback_id,
               AuditLog.action.in_([a.value for a in HISTORY_ACTIONS]))
        .order_by(AuditLog.created_at, AuditLog.id)
    )
    return [
        FeedbackAction(action=row.action, actor=FeedbackActor(id=row.actor_user_id, full_name=row.full_name),
                       at=_utc(row.created_at))  # type: ignore[arg-type]
        for row in rows
    ]


async def get_feedback(db: AsyncSession, feedback_id: int) -> FeedbackDetail:
    row = (
        await db.execute(
            select(Feedback, User.full_name, User.email, Unit.number, Tower.number, Block.number)
            .join(User, User.id == Feedback.resident_id)
            # The resident's current residence, if any (never stored in the feedback).
            .outerjoin(ResidentUnit, (ResidentUnit.user_id == User.id) & ResidentUnit.ended_at.is_(None))
            .outerjoin(Unit, Unit.id == ResidentUnit.unit_id)
            .outerjoin(Tower, Tower.id == Unit.tower_id)
            .outerjoin(Block, Block.id == Tower.block_id)
            .where(Feedback.id == feedback_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise FeedbackNotFoundError()
    feedback, name, email, unit_number, tower_number, block_number = row
    label = unit_label(block_number, tower_number, unit_number) if unit_number is not None else None
    return FeedbackDetail(
        id=feedback.id,
        resident=FeedbackResident(id=feedback.resident_id, full_name=name, email=email, unit_label=label),
        subject=feedback.subject,
        message=feedback.message,
        status=FeedbackStatus(feedback.status),
        admin_response=feedback.admin_response,
        resolution_note=feedback.resolution_note,
        created_at=_utc(feedback.created_at),  # type: ignore[arg-type]
        updated_at=_utc(feedback.updated_at),  # type: ignore[arg-type]
        resolved_at=_utc(feedback.resolved_at),
        version=feedback.version,
        action_history=await _history(db, feedback.id),
    )


# --- Changes ------------------------------------------------------------------------------------------------


async def _lock(db: AsyncSession, feedback_id: int, version: int) -> Feedback:
    """New transaction whose first statement locks the row (latest committed data); checks version and status."""
    await db.commit()
    feedback = await db.scalar(
        select(Feedback).where(Feedback.id == feedback_id).with_for_update().execution_options(populate_existing=True)
    )
    if feedback is None:
        await db.rollback()
        raise FeedbackNotFoundError()
    try:
        ensure_changeable(FeedbackStatus(feedback.status), current_version=feedback.version, sent_version=version)
    except (FeedbackVersionConflictError, FeedbackAlreadyResolvedError):
        await db.rollback()
        raise
    return feedback


def ensure_changeable(status: FeedbackStatus, *, current_version: int, sent_version: int) -> None:
    """Only PENDING → (response | RESOLVED), and only on the version the administrator saw.

    The version is checked first (a stale page always gets VERSION_CONFLICT);
    RESOLVED is final: nothing changes it back to PENDING in HU-005.
    """
    if current_version != sent_version:
        raise FeedbackVersionConflictError()
    if status != FeedbackStatus.PENDING:
        raise FeedbackAlreadyResolvedError()


def _audit(db: AsyncSession, action: AuditAction, feedback_id: int, actor: Actor, context: dict[str, Any]) -> None:
    record_audit(db, action=action, entity_type=ENTITY, entity_id=feedback_id, actor_user_id=actor.user_id,
                 context=context, ip_address=actor.ip_address)


async def _notify(
    db: AsyncSession, feedback_id: int, notice: FeedbackNotice, actor: Actor, mailer: MailSender
) -> NotificationStatus:
    """After the COMMIT: email the resident, then record the result in another transaction."""
    row = (
        await db.execute(
            select(User.email, User.full_name, Feedback.subject, Feedback.admin_response)
            .join(User, User.id == Feedback.resident_id)
            .where(Feedback.id == feedback_id)
        )
    ).one()
    await db.commit()  # nothing stays open while SMTP runs
    error: str | None = None
    try:
        email = build_feedback_email(notice=notice, to=row.email, resident_name=row.full_name,
                                     subject=row.subject, admin_response=row.admin_response)
        await mailer.send(email)
    except Exception as exc:  # any SMTP/network failure; the message is never logged
        error = type(exc).__name__
        logger.warning("Feedback notification not sent (feedback_id=%s, error=%s)", feedback_id, error)
    context: dict[str, Any] = {"notification": notice.value}
    if error is not None:
        context["error"] = error
    try:
        _audit(db, AuditAction.FEEDBACK_NOTIFICATION_SENT if error is None else AuditAction.FEEDBACK_NOTIFICATION_FAILED,
               feedback_id, actor, context)
        await db.commit()
    except Exception as exc:
        # The change stays committed; only the notification result is unconfirmed.
        await db.rollback()
        logger.error("Feedback notification result not recorded (feedback_id=%s, error=%s)", feedback_id,
                     type(exc).__name__)
        return NotificationStatus.UNCONFIRMED
    return NotificationStatus.SENT if error is None else NotificationStatus.FAILED


async def respond(
    db: AsyncSession, feedback_id: int, data: FeedbackResponse, actor: Actor, *, mailer: MailSender
) -> FeedbackActionResult:
    feedback = await _lock(db, feedback_id, data.version)
    replaced = feedback.admin_response is not None
    feedback.admin_response = data.response
    feedback.version = data.version + 1
    await db.flush()
    _audit(db, AuditAction.FEEDBACK_RESPONDED, feedback_id, actor, {
        "status": FeedbackStatus.PENDING.value, "previous_version": data.version, "version": data.version + 1,
        "replaced_response": replaced,
    })
    await db.commit()  # the response is saved from here on
    notification = await _notify(db, feedback_id, FeedbackNotice.RESPONSE, actor, mailer)
    return FeedbackActionResult(feedback=await get_feedback(db, feedback_id), notification_status=notification)


async def resolve(
    db: AsyncSession, feedback_id: int, data: FeedbackResolution, actor: Actor, *, mailer: MailSender
) -> FeedbackActionResult:
    feedback = await _lock(db, feedback_id, data.version)
    feedback.resolution_note = data.resolution_note
    feedback.status = FeedbackStatus.RESOLVED
    feedback.resolved_at = utc_now()
    feedback.version = data.version + 1
    await db.flush()
    _audit(db, AuditAction.FEEDBACK_RESOLVED, feedback_id, actor, {
        "previous_status": FeedbackStatus.PENDING.value, "status": FeedbackStatus.RESOLVED.value,
        "previous_version": data.version, "version": data.version + 1,
        "with_response": feedback.admin_response is not None,
    })
    await db.commit()
    notification = await _notify(db, feedback_id, FeedbackNotice.RESOLUTION, actor, mailer)
    return FeedbackActionResult(feedback=await get_feedback(db, feedback_id), notification_status=notification)


# --- List ----------------------------------------------------------------------------------------------------


def _sort_keys(field: FeedbackSortField) -> tuple[Any, ...]:
    # Never built from the client's text: each whitelisted value maps to expressions.
    match field:
        case FeedbackSortField.CREATED_AT:
            return (Feedback.created_at,)
        case FeedbackSortField.RESIDENT:
            return (User.full_name,)
        case FeedbackSortField.SUBJECT:
            return (Feedback.subject,)
        case FeedbackSortField.STATUS:  # 'PENDING' < 'RESOLVED': Pendiente, then Resuelto
            return (Feedback.status,)


def _filtered(statement: Select[Any], query: FeedbackListQuery) -> Select[Any]:
    conditions: list[ColumnElement[bool]] = []
    if query.status is not None:
        conditions.append(Feedback.status == query.status)
    if query.search:
        # Resident's name or subject; % and _ are literal.
        conditions.append(
            User.full_name.contains(query.search, autoescape=True)
            | Feedback.subject.contains(query.search, autoescape=True)
        )
    return statement.join(User, User.id == Feedback.resident_id).where(*conditions)


async def list_feedback(db: AsyncSession, query: FeedbackListQuery) -> FeedbackPage:
    total = int(await db.scalar(_filtered(select(func.count(Feedback.id)).select_from(Feedback), query)) or 0)
    total_pages = math.ceil(total / query.page_size)
    items: list[FeedbackListItem] = []
    if query.page <= total_pages:
        descending = query.sort_order == SortOrder.DESC
        keys: list[Any] = []
        if query.sort_by == FeedbackSortField.SUBJECT:
            keys.append(case((Feedback.subject.is_(None), 1), else_=0))  # without subject: last, both directions
        keys += [k.desc() if descending else k.asc() for k in (*_sort_keys(query.sort_by), Feedback.id)]
        rows = (
            await db.execute(
                _filtered(
                    select(Feedback.id, Feedback.resident_id, User.full_name, Feedback.subject, Feedback.status,
                           Feedback.created_at, Feedback.resolved_at).select_from(Feedback),
                    query,
                )
                .order_by(*keys)
                .limit(query.page_size)
                .offset((query.page - 1) * query.page_size)
            )
        ).all()
        items = [
            FeedbackListItem(
                id=row.id, resident=ResidentRef(id=row.resident_id, full_name=row.full_name), subject=row.subject,
                status=FeedbackStatus(row.status), created_at=_utc(row.created_at),  # type: ignore[arg-type]
                resolved_at=_utc(row.resolved_at),
            )
            for row in rows
        ]
    return FeedbackPage(items=items, page=query.page, page_size=query.page_size, total=total, total_pages=total_pages)
