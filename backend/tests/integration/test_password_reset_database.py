"""HU-016: the real structure of password_reset_tokens and the rules MySQL enforces itself."""

import secrets
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio


async def rows(db: AsyncSession, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in (await db.execute(text(sql), params)).all()]


async def test_columns(db_session: AsyncSession) -> None:
    assert await rows(
        db_session,
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA, GENERATION_EXPRESSION "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'password_reset_tokens' "
        "ORDER BY ORDINAL_POSITION",
    ) == [
        ("id", "bigint", "NO", None, "auto_increment", ""),
        ("user_id", "bigint", "NO", None, "", ""),
        ("token_hash", "char(64)", "NO", None, "", ""),
        ("created_at", "datetime(6)", "NO", "CURRENT_TIMESTAMP(6)", "DEFAULT_GENERATED", ""),
        ("expires_at", "datetime(6)", "NO", None, "", ""),
        ("used_at", "datetime(6)", "YES", None, "", ""),
        ("revoked_at", "datetime(6)", "YES", None, "", ""),
        ("pending_user_marker", "bigint", "YES", None, "VIRTUAL GENERATED",
         "(case when ((`used_at` is null) and (`revoked_at` is null)) then `user_id` end)"),
    ]


async def test_constraints_and_indexes(db_session: AsyncSession) -> None:
    checks = await rows(
        db_session,
        "SELECT cc.CONSTRAINT_NAME, tc.ENFORCED FROM information_schema.CHECK_CONSTRAINTS cc "
        "JOIN information_schema.TABLE_CONSTRAINTS tc ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA "
        "AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME WHERE cc.CONSTRAINT_SCHEMA = DATABASE() "
        "AND tc.TABLE_NAME = 'password_reset_tokens' ORDER BY 1",
    )
    assert checks == [
        ("ck_password_reset_tokens_expires_after_creation", "YES"),
        ("ck_password_reset_tokens_used_or_revoked", "YES"),
    ]
    assert await rows(
        db_session,
        "SELECT INDEX_NAME, NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'password_reset_tokens' GROUP BY 1, 2 ORDER BY 1",
    ) == [
        ("ix_password_reset_tokens_user_id_created_at", 1, "user_id,created_at"),
        ("PRIMARY", 0, "id"),
        ("uq_password_reset_tokens_pending_user", 0, "pending_user_marker"),
        ("uq_password_reset_tokens_token_hash", 0, "token_hash"),
    ]
    assert await rows(
        db_session,
        "SELECT CONSTRAINT_NAME, REFERENCED_TABLE_NAME, DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'password_reset_tokens'",
    ) == [("fk_password_reset_tokens_user_id_users", "users", "RESTRICT")]


@pytest.fixture
async def user_id(db_session: AsyncSession) -> int:
    await db_session.execute(text(
        "INSERT INTO users (email, full_name, role, status, password_hash) "
        "VALUES ('r@example.com', 'X Y Z', 'RESIDENTE', 'ACTIVE', 'not-a-real-hash')"
    ))
    return int(await db_session.scalar(text("SELECT LAST_INSERT_ID()")))


async def insert(db: AsyncSession, user_id: int, **values: Any) -> None:
    data = {"hash": secrets.token_hex(32), "used": None, "revoked": None, "expires": "UTC_TIMESTAMP(6) + INTERVAL 1 HOUR", **values}
    await db.execute(
        text("INSERT INTO password_reset_tokens (user_id, token_hash, created_at, expires_at, used_at, revoked_at) "
             f"VALUES (:u, :hash, UTC_TIMESTAMP(6), {data.pop('expires')}, :used, :revoked)"),
        {"u": user_id, **data},
    )


async def test_one_pending_reset_per_user(db_session: AsyncSession, user_id: int) -> None:
    await insert(db_session, user_id, revoked="2026-01-01")
    await insert(db_session, user_id, used="2026-01-01")
    await insert(db_session, user_id)  # finished resets do not count
    with pytest.raises(IntegrityError, match="uq_password_reset_tokens_pending_user"):
        await insert(db_session, user_id)


async def test_token_hash_is_unique(db_session: AsyncSession, user_id: int) -> None:
    await insert(db_session, user_id, hash="f" * 64, revoked="2026-01-01")
    with pytest.raises(IntegrityError, match="uq_password_reset_tokens_token_hash"):
        await insert(db_session, user_id, hash="f" * 64)


@pytest.mark.parametrize(
    "values",
    [{"used": "2026-01-01", "revoked": "2026-01-01"}, {"expires": "UTC_TIMESTAMP(6) - INTERVAL 1 SECOND"}],
    ids=["used-and-revoked", "expires-before-creation"],
)
async def test_checks(db_session: AsyncSession, user_id: int, values: dict[str, Any]) -> None:
    with pytest.raises(DBAPIError, match="3819"):
        await insert(db_session, user_id, **values)


async def test_foreign_key(db_session: AsyncSession, user_id: int) -> None:
    with pytest.raises(IntegrityError, match="fk_password_reset_tokens_user_id_users"):
        await insert(db_session, 999_999_999)
    await insert(db_session, user_id)
    with pytest.raises(IntegrityError, match="fk_password_reset_tokens_user_id_users"):
        await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


async def test_marker_cannot_be_written(db_session: AsyncSession, user_id: int) -> None:
    await insert(db_session, user_id)
    with pytest.raises(DBAPIError, match="3105"):
        await db_session.execute(text("UPDATE password_reset_tokens SET pending_user_marker = 1"))
