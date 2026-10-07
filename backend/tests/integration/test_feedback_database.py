"""HU-005: the real structure of feedback and the rules MySQL enforces itself."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import feedback as feedback_service
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio


async def rows(db: AsyncSession, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in (await db.execute(text(sql), params)).all()]


async def test_columns(db_session: AsyncSession) -> None:
    assert await rows(
        db_session,
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'feedback' ORDER BY ORDINAL_POSITION",
    ) == [
        ("id", "bigint", "NO", None),
        ("resident_id", "bigint", "NO", None),
        ("subject", "varchar(150)", "YES", None),
        ("message", "varchar(2000)", "NO", None),
        # HU-009: NULL only for the feedback stored before it (no default).
        ("category", "varchar(20)", "YES", None),  # utf8mb4_0900_bin: exact codes only
        ("urgency", "varchar(20)", "YES", None),
        ("status", "varchar(20)", "NO", "PENDING"),
        ("admin_response", "varchar(2000)", "YES", None),
        ("resolution_note", "varchar(2000)", "YES", None),
        ("version", "int unsigned", "NO", "1"),
        ("created_at", "datetime(6)", "NO", "CURRENT_TIMESTAMP(6)"),
        ("updated_at", "datetime(6)", "NO", "CURRENT_TIMESTAMP(6)"),
        ("resolved_at", "datetime(6)", "YES", None),
    ]


async def test_constraints_indexes_and_foreign_key(db_session: AsyncSession) -> None:
    assert await rows(
        db_session,
        "SELECT cc.CONSTRAINT_NAME FROM information_schema.CHECK_CONSTRAINTS cc "
        "JOIN information_schema.TABLE_CONSTRAINTS tc ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA "
        "AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME WHERE cc.CONSTRAINT_SCHEMA = DATABASE() "
        "AND tc.TABLE_NAME = 'feedback' AND tc.ENFORCED = 'YES' ORDER BY 1",
    ) == [(name,) for name in (
        "ck_feedback_category_urgency_together", "ck_feedback_category_valid", "ck_feedback_message_not_blank",
        "ck_feedback_resolved_after_creation", "ck_feedback_resolved_has_note", "ck_feedback_resolved_has_timestamp",
        "ck_feedback_status_valid", "ck_feedback_subject_not_blank", "ck_feedback_urgency_valid",
        "ck_feedback_version_positive")]
    assert await rows(
        db_session,
        "SELECT INDEX_NAME, NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'feedback' GROUP BY 1, 2 ORDER BY 1",
    ) == [
        ("ix_feedback_created_at", 1, "created_at"),
        ("ix_feedback_resident_id", 1, "resident_id"),
        ("ix_feedback_status_created_at", 1, "status,created_at"),
        ("PRIMARY", 0, "id"),
    ]
    assert await rows(
        db_session,
        "SELECT CONSTRAINT_NAME, REFERENCED_TABLE_NAME, DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'feedback'",
    ) == [("fk_feedback_resident_id_users", "users", "RESTRICT")]


async def rejects(db: AsyncSession, sql: str, code: int, **params: Any) -> None:
    async with db.begin_nested():
        with pytest.raises((IntegrityError, DBAPIError)) as error:
            await db.execute(text(sql), params)
    assert error.value.orig.args[0] == code, error.value.orig  # type: ignore[union-attr]


async def test_rules_enforced_by_mysql(db_session: AsyncSession, make_user: Any) -> None:
    admin = await make_user("admin@example.com", password_hash="x")
    resident = await Community(db_session, admin.id).resident("Laura Gómez", "1012345678", (1, 1, 101))
    feedback_id = await feedback_service.create_feedback(db_session, resident, subject=None, message="Hola",
                                                         category="COMPLAINT", urgency="MEDIUM")
    check, fk_missing, fk_referenced = 3819, 1452, 1451
    await rejects(db_session, "UPDATE feedback SET status = 'OPEN' WHERE id = :f", check, f=feedback_id)
    await rejects(db_session, "UPDATE feedback SET status = 'RESOLVED', resolution_note = 'x' WHERE id = :f", check, f=feedback_id)
    await rejects(db_session, "UPDATE feedback SET status = 'RESOLVED', resolved_at = UTC_TIMESTAMP(6) WHERE id = :f", check, f=feedback_id)
    await rejects(db_session, "UPDATE feedback SET resolved_at = UTC_TIMESTAMP(6) WHERE id = :f", check, f=feedback_id)
    await rejects(db_session, "UPDATE feedback SET message = '   ' WHERE id = :f", check, f=feedback_id)
    await rejects(db_session, "UPDATE feedback SET subject = '' WHERE id = :f", check, f=feedback_id)
    await rejects(db_session, "UPDATE feedback SET version = 0 WHERE id = :f", check, f=feedback_id)
    await rejects(db_session, "INSERT INTO feedback (resident_id, message) VALUES (999999, 'x')", fk_missing)
    await rejects(db_session, "DELETE FROM users WHERE id = :u", fk_referenced, u=resident)
