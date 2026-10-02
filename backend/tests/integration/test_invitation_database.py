"""HU-003: the real structure of resident_invitations / resident_units in MySQL and
the rules MySQL enforces by itself (bypassing the service)."""

import secrets
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio


async def rows(db: AsyncSession, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in (await db.execute(text(sql), params)).all()]


# --- Structure (information_schema) ------------------------------------------------------------


async def test_columns_of_resident_invitations(db_session: AsyncSession) -> None:
    columns = await rows(
        db_session,
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA, GENERATION_EXPRESSION "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'resident_invitations' "
        "ORDER BY ORDINAL_POSITION",
    )
    assert columns == [
        ("id", "bigint", "NO", None, "auto_increment", ""),
        ("token_hash", "char(64)", "NO", None, "", ""),
        ("user_id", "bigint", "NO", None, "", ""),
        ("unit_id", "bigint", "NO", None, "", ""),
        ("invited_by_user_id", "bigint", "NO", None, "", ""),
        ("validity_days", "tinyint unsigned", "NO", None, "", ""),
        ("status", "varchar(20)", "NO", "PENDING", "", ""),
        ("delivery_status", "varchar(20)", "NO", "PENDING", "", ""),
        ("send_attempts", "smallint unsigned", "NO", "0", "", ""),
        ("created_at", "datetime(6)", "NO", "CURRENT_TIMESTAMP(6)", "DEFAULT_GENERATED", ""),
        ("expires_at", "datetime(6)", "NO", None, "", ""),
        ("used_at", "datetime(6)", "YES", None, "", ""),
        ("last_sent_at", "datetime(6)", "YES", None, "", ""),
        (
            "pending_unit_marker", "bigint", "YES", None, "VIRTUAL GENERATED",
            "(case when (`status` = _utf8mb4\\'PENDING\\') then `unit_id` end)",
        ),
        (
            "pending_user_marker", "bigint", "YES", None, "VIRTUAL GENERATED",
            "(case when (`status` = _utf8mb4\\'PENDING\\') then `user_id` end)",
        ),
    ]


async def test_columns_of_resident_units(db_session: AsyncSession) -> None:
    columns = await rows(
        db_session,
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, EXTRA, GENERATION_EXPRESSION "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'resident_units' "
        "ORDER BY ORDINAL_POSITION",
    )
    assert columns == [
        ("id", "bigint", "NO", "auto_increment", ""),
        ("user_id", "bigint", "NO", "", ""),
        ("unit_id", "bigint", "NO", "", ""),
        ("invitation_id", "bigint", "NO", "", ""),
        ("started_at", "datetime(6)", "NO", "", ""),
        ("ended_at", "datetime(6)", "YES", "", ""),
        ("active_unit_marker", "bigint", "YES", "VIRTUAL GENERATED", "(case when (`ended_at` is null) then `unit_id` end)"),
        ("active_user_marker", "bigint", "YES", "VIRTUAL GENERATED", "(case when (`ended_at` is null) then `user_id` end)"),
    ]


async def test_check_constraints(db_session: AsyncSession) -> None:
    checks = dict(
        await rows(
            db_session,
            "SELECT cc.CONSTRAINT_NAME, tc.ENFORCED FROM information_schema.CHECK_CONSTRAINTS cc "
            "JOIN information_schema.TABLE_CONSTRAINTS tc "
            "  ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME "
            "WHERE cc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME IN ('resident_invitations', 'resident_units')",
        )
    )
    assert checks == {
        "ck_resident_invitations_validity_days_range": "YES",
        "ck_resident_invitations_status_valid": "YES",
        "ck_resident_invitations_delivery_status_valid": "YES",
        "ck_resident_invitations_send_attempts_non_negative": "YES",
        "ck_resident_invitations_expires_after_creation": "YES",
        "ck_resident_invitations_used_at_matches_status": "YES",
        "ck_resident_invitations_sent_has_timestamp": "YES",
        "ck_resident_units_ended_after_start": "YES",
    }


async def test_indexes(db_session: AsyncSession) -> None:
    indexes = await rows(
        db_session,
        "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN ('resident_invitations', 'resident_units') "
        "GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE ORDER BY TABLE_NAME, INDEX_NAME",
    )
    assert indexes == [
        ("resident_invitations", "ix_resident_invitations_invited_by_user_id", 1, "invited_by_user_id"),
        ("resident_invitations", "ix_resident_invitations_unit_id", 1, "unit_id"),
        ("resident_invitations", "ix_resident_invitations_user_id", 1, "user_id"),
        ("resident_invitations", "PRIMARY", 0, "id"),
        ("resident_invitations", "uq_resident_invitations_pending_unit", 0, "pending_unit_marker"),
        ("resident_invitations", "uq_resident_invitations_pending_user", 0, "pending_user_marker"),
        ("resident_invitations", "uq_resident_invitations_token_hash", 0, "token_hash"),
        ("resident_units", "ix_resident_units_unit_id", 1, "unit_id"),
        ("resident_units", "ix_resident_units_user_id", 1, "user_id"),
        ("resident_units", "PRIMARY", 0, "id"),
        ("resident_units", "uq_resident_units_active_unit", 0, "active_unit_marker"),
        ("resident_units", "uq_resident_units_active_user", 0, "active_user_marker"),
        ("resident_units", "uq_resident_units_invitation_id", 0, "invitation_id"),
    ]


async def test_foreign_keys_restrict_deletes(db_session: AsyncSession) -> None:
    foreign_keys = await rows(
        db_session,
        "SELECT rc.CONSTRAINT_NAME, k.COLUMN_NAME, rc.REFERENCED_TABLE_NAME, rc.DELETE_RULE "
        "FROM information_schema.REFERENTIAL_CONSTRAINTS rc "
        "JOIN information_schema.KEY_COLUMN_USAGE k "
        "  ON k.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA AND k.CONSTRAINT_NAME = rc.CONSTRAINT_NAME "
        "WHERE rc.CONSTRAINT_SCHEMA = DATABASE() AND rc.TABLE_NAME IN ('resident_invitations', 'resident_units') "
        "ORDER BY rc.CONSTRAINT_NAME",
    )
    assert foreign_keys == [
        ("fk_resident_invitations_invited_by_user_id_users", "invited_by_user_id", "users", "RESTRICT"),
        ("fk_resident_invitations_unit_id_units", "unit_id", "units", "RESTRICT"),
        ("fk_resident_invitations_user_id_users", "user_id", "users", "RESTRICT"),
        ("fk_resident_units_invitation_id_resident_invitations", "invitation_id", "resident_invitations", "RESTRICT"),
        ("fk_resident_units_unit_id_units", "unit_id", "units", "RESTRICT"),
        ("fk_resident_units_user_id_users", "user_id", "users", "RESTRICT"),
    ]


async def test_no_plain_token_column(db_session: AsyncSession) -> None:
    names = await rows(
        db_session,
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'resident_invitations' AND COLUMN_NAME LIKE '%token%'",
    )
    assert names == [("token_hash",)]


# --- Rules enforced by MySQL ------------------------------------------------------------------------


@pytest.fixture
async def ids(db_session: AsyncSession) -> dict[str, int]:
    """Admin, two residents and two units inserted with SQL."""
    result: dict[str, int] = {}
    for key, email, role in (("admin", "a@example.com", "ADMINISTRADOR"), ("r1", "r1@example.com", "RESIDENTE"), ("r2", "r2@example.com", "RESIDENTE")):
        status = "ACTIVE" if role == "ADMINISTRADOR" else "PENDING_ACTIVATION"
        password = "'not-a-real-hash'" if role == "ADMINISTRADOR" else "NULL"
        await db_session.execute(
            text(f"INSERT INTO users (email, full_name, role, status, password_hash) VALUES (:e, 'X Y Z', :r, :s, {password})"),
            {"e": email, "r": role, "s": status},
        )
        result[key] = int(await db_session.scalar(text("SELECT LAST_INSERT_ID()")))
    await db_session.execute(text("INSERT INTO blocks (number) VALUES (1)"))
    await db_session.execute(text("INSERT INTO towers (block_id, number) SELECT id, 1 FROM blocks"))
    for key, number in (("u1", 101), ("u2", 102)):
        await db_session.execute(text("INSERT INTO units (tower_id, number) SELECT id, :n FROM towers"), {"n": number})
        result[key] = int(await db_session.scalar(text("SELECT LAST_INSERT_ID()")))
    return result


async def insert_invitation(db: AsyncSession, ids: dict[str, int], user: str, unit: str, **overrides: Any) -> int:
    values = {
        "token_hash": secrets.token_hex(32),
        "status": "PENDING",
        "delivery_status": "PENDING",
        "validity_days": 7,
        "send_attempts": 0,
        "used_at": None,
        "last_sent_at": None,
        "expires": "UTC_TIMESTAMP(6) + INTERVAL 7 DAY",
        **overrides,
    }
    await db.execute(
        text(
            "INSERT INTO resident_invitations (token_hash, user_id, unit_id, invited_by_user_id, validity_days, "
            "status, delivery_status, send_attempts, created_at, expires_at, used_at, last_sent_at) VALUES "
            f"(:token_hash, :user_id, :unit_id, :admin, :validity_days, :status, :delivery_status, :send_attempts, "
            f"UTC_TIMESTAMP(6), {values.pop('expires')}, :used_at, :last_sent_at)"
        ),
        {**values, "user_id": ids[user], "unit_id": ids[unit], "admin": ids["admin"]},
    )
    return int(await db.scalar(text("SELECT LAST_INSERT_ID()")))


@pytest.mark.parametrize(
    "overrides",
    [
        {"validity_days": 0},
        {"validity_days": 31},
        {"status": "CANCELLED"},
        {"delivery_status": "BOUNCED"},
        {"status": "USED"},  # USED without used_at
        {"used_at": "2026-01-01 00:00:00"},  # used_at while PENDING
        {"delivery_status": "SENT"},  # SENT without last_sent_at
        {"expires": "UTC_TIMESTAMP(6) - INTERVAL 1 DAY"},  # expires before creation
    ],
    ids=["validity-0", "validity-31", "status", "delivery-status", "used-without-date", "date-without-used", "sent-without-date", "expires-before-creation"],
)
async def test_checks_reject_invalid_rows(db_session: AsyncSession, ids: dict[str, int], overrides: dict[str, Any]) -> None:
    # asyncmy reports CHECK violations (3819) as OperationalError: use DBAPIError.
    with pytest.raises(DBAPIError, match="3819"):
        await insert_invitation(db_session, ids, "r1", "u1", **overrides)


async def test_send_attempts_cannot_be_negative(db_session: AsyncSession, ids: dict[str, int]) -> None:
    with pytest.raises(DBAPIError, match="1264"):  # out of range for SMALLINT UNSIGNED
        await insert_invitation(db_session, ids, "r1", "u1", send_attempts=-1)


async def test_token_hash_is_unique(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await insert_invitation(db_session, ids, "r1", "u1", token_hash="b" * 64)
    with pytest.raises(IntegrityError, match="uq_resident_invitations_token_hash"):
        await insert_invitation(db_session, ids, "r2", "u2", token_hash="b" * 64)


async def test_one_pending_invitation_per_unit(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await insert_invitation(db_session, ids, "r1", "u1")
    with pytest.raises(IntegrityError, match="uq_resident_invitations_pending_unit"):
        await insert_invitation(db_session, ids, "r2", "u1")


async def test_one_pending_invitation_per_user(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await insert_invitation(db_session, ids, "r1", "u1")
    with pytest.raises(IntegrityError, match="uq_resident_invitations_pending_user"):
        await insert_invitation(db_session, ids, "r1", "u2")


async def test_expired_and_used_invitations_do_not_reserve(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await insert_invitation(db_session, ids, "r1", "u1", status="EXPIRED")
    await insert_invitation(db_session, ids, "r1", "u1", status="USED", used_at="2026-01-01 00:00:00")
    await insert_invitation(db_session, ids, "r1", "u1")  # a new PENDING one is accepted


async def test_one_active_residence_per_unit_and_per_user(db_session: AsyncSession, ids: dict[str, int]) -> None:
    first = await insert_invitation(db_session, ids, "r1", "u1", status="USED", used_at="2026-01-01 00:00:00")
    second = await insert_invitation(db_session, ids, "r2", "u1", status="USED", used_at="2026-01-01 00:00:00")
    third = await insert_invitation(db_session, ids, "r1", "u2", status="USED", used_at="2026-01-01 00:00:00")
    insert = text(
        "INSERT INTO resident_units (user_id, unit_id, invitation_id, started_at, ended_at) "
        "VALUES (:user, :unit, :inv, UTC_TIMESTAMP(6), :ended)"
    )
    await db_session.execute(insert, {"user": ids["r1"], "unit": ids["u1"], "inv": first, "ended": None})
    with pytest.raises(IntegrityError, match="uq_resident_units_active_unit"):
        await db_session.execute(insert, {"user": ids["r2"], "unit": ids["u1"], "inv": second, "ended": None})


async def test_one_active_unit_per_user(db_session: AsyncSession, ids: dict[str, int]) -> None:
    first = await insert_invitation(db_session, ids, "r1", "u1", status="USED", used_at="2026-01-01 00:00:00")
    second = await insert_invitation(db_session, ids, "r1", "u2", status="EXPIRED")
    insert = text(
        "INSERT INTO resident_units (user_id, unit_id, invitation_id, started_at) VALUES (:user, :unit, :inv, UTC_TIMESTAMP(6))"
    )
    await db_session.execute(insert, {"user": ids["r1"], "unit": ids["u1"], "inv": first})
    with pytest.raises(IntegrityError, match="uq_resident_units_active_user"):
        await db_session.execute(insert, {"user": ids["r1"], "unit": ids["u2"], "inv": second})


async def test_invitation_produces_at_most_one_residence(db_session: AsyncSession, ids: dict[str, int]) -> None:
    invitation = await insert_invitation(db_session, ids, "r1", "u1", status="USED", used_at="2026-01-01 00:00:00")
    insert = text(
        "INSERT INTO resident_units (user_id, unit_id, invitation_id, started_at, ended_at) "
        "VALUES (:user, :unit, :inv, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))"
    )
    await db_session.execute(insert, {"user": ids["r1"], "unit": ids["u1"], "inv": invitation})
    with pytest.raises(IntegrityError, match="uq_resident_units_invitation_id"):
        await db_session.execute(insert, {"user": ids["r1"], "unit": ids["u1"], "inv": invitation})


async def test_residence_cannot_end_before_it_starts(db_session: AsyncSession, ids: dict[str, int]) -> None:
    invitation = await insert_invitation(db_session, ids, "r1", "u1", status="USED", used_at="2026-01-01 00:00:00")
    with pytest.raises(DBAPIError, match="3819"):
        await db_session.execute(
            text(
                "INSERT INTO resident_units (user_id, unit_id, invitation_id, started_at, ended_at) "
                "VALUES (:user, :unit, :inv, '2026-02-01', '2026-01-01')"
            ),
            {"user": ids["r1"], "unit": ids["u1"], "inv": invitation},
        )


async def test_foreign_keys_block_physical_deletes(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await insert_invitation(db_session, ids, "r1", "u1")
    with pytest.raises(IntegrityError, match="fk_resident_invitations_unit_id_units"):
        await db_session.execute(text("DELETE FROM units WHERE id = :id"), {"id": ids["u1"]})


async def test_unknown_unit_is_rejected(db_session: AsyncSession, ids: dict[str, int]) -> None:
    ids = {**ids, "u9": 999_999_999}
    with pytest.raises(IntegrityError, match="fk_resident_invitations_unit_id_units"):
        await insert_invitation(db_session, ids, "r1", "u9")


async def test_generated_markers_cannot_be_written(db_session: AsyncSession, ids: dict[str, int]) -> None:
    invitation = await insert_invitation(db_session, ids, "r1", "u1")
    with pytest.raises(DBAPIError, match="3105"):
        await db_session.execute(
            text("UPDATE resident_invitations SET pending_unit_marker = 1 WHERE id = :id"), {"id": invitation}
        )
