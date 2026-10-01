"""HU-013: the real structure in MySQL and the rules it enforces by itself.

Alembic autogenerate does not compare CHECK constraints and only partially
handles generated columns, so the structure is read from information_schema
and every guarantee is exercised with direct SQL, bypassing the service.
"""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

TABLES = ("blocks", "towers", "units")
MARKER_EXPRESSION = "(case when (`deleted_at` is null) then `number` end)"


async def rows(db: AsyncSession, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in (await db.execute(text(sql), params)).all()]


async def insert(db: AsyncSession, table: str, number: int, parent_id: int | None = None, deleted: bool = False) -> int:
    parent = {"blocks": None, "towers": "block_id", "units": "tower_id"}[table]
    columns = "number, deleted_at" + (f", {parent}" if parent else "")
    values = ":number, :deleted_at" + (", :parent_id" if parent else "")
    await db.execute(
        text(f"INSERT INTO {table} ({columns}) VALUES ({values})"),
        {"number": number, "parent_id": parent_id, "deleted_at": "2026-01-01 00:00:00" if deleted else None},
    )
    return int(await db.scalar(text("SELECT LAST_INSERT_ID()")))


async def test_server_is_mysql_8_0(db_session: AsyncSession) -> None:
    version = await db_session.scalar(text("SELECT VERSION()"))
    assert version.startswith("8.0.")


# --- Structure -----------------------------------------------------------------


async def test_tables_use_innodb(db_session: AsyncSession) -> None:
    engines = await rows(
        db_session,
        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('blocks', 'towers', 'units') ORDER BY TABLE_NAME",
    )
    assert engines == [("blocks", "InnoDB"), ("towers", "InnoDB"), ("units", "InnoDB")]


@pytest.mark.parametrize(("table", "parent"), [("blocks", None), ("towers", "block_id"), ("units", "tower_id")])
async def test_columns(db_session: AsyncSession, table: str, parent: str | None) -> None:
    columns = await rows(
        db_session,
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA, GENERATION_EXPRESSION "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table "
        "ORDER BY ORDINAL_POSITION",
        table=table,
    )
    expected = [("id", "bigint", "NO", None, "auto_increment", "")]
    if parent:
        expected.append((parent, "bigint", "NO", None, "", ""))
    expected += [
        ("number", "int unsigned", "NO", None, "", ""),
        ("version", "int unsigned", "NO", "1", "", ""),
        ("created_at", "datetime(6)", "NO", "CURRENT_TIMESTAMP(6)", "DEFAULT_GENERATED", ""),
        (
            "updated_at",
            "datetime(6)",
            "NO",
            "CURRENT_TIMESTAMP(6)",
            "DEFAULT_GENERATED on update CURRENT_TIMESTAMP(6)",
            "",
        ),
        ("deleted_at", "datetime(6)", "YES", None, "", ""),
        # VIRTUAL (not STORED) generated column.
        ("active_number_marker", "int unsigned", "YES", None, "VIRTUAL GENERATED", MARKER_EXPRESSION),
    ]
    assert columns == expected


async def test_check_constraints(db_session: AsyncSession) -> None:
    checks = await rows(
        db_session,
        "SELECT tc.TABLE_NAME, cc.CONSTRAINT_NAME, cc.CHECK_CLAUSE, tc.ENFORCED "
        "FROM information_schema.CHECK_CONSTRAINTS cc "
        "JOIN information_schema.TABLE_CONSTRAINTS tc "
        "  ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME "
        "WHERE cc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME IN ('blocks', 'towers', 'units') "
        "ORDER BY cc.CONSTRAINT_NAME",
    )
    assert checks == [
        (table, f"ck_{table}_{name}", f"(`{column}` >= 1)", "YES")
        for table in TABLES
        for name, column in (("number_positive", "number"), ("version_positive", "version"))
    ]


async def test_indexes(db_session: AsyncSession) -> None:
    indexes = await rows(
        db_session,
        "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN ('blocks', 'towers', 'units') "
        "GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE ORDER BY TABLE_NAME, INDEX_NAME",
    )
    assert indexes == [
        ("blocks", "PRIMARY", 0, "id"),
        ("blocks", "uq_blocks_active_number", 0, "active_number_marker"),
        # Own index for the foreign key (not the one that contains the generated column).
        ("towers", "ix_towers_block_id", 1, "block_id"),
        ("towers", "PRIMARY", 0, "id"),
        ("towers", "uq_towers_block_active_number", 0, "block_id,active_number_marker"),
        ("units", "ix_units_tower_id", 1, "tower_id"),
        ("units", "PRIMARY", 0, "id"),
        ("units", "uq_units_tower_active_number", 0, "tower_id,active_number_marker"),
    ]


async def test_foreign_keys_restrict_deletes(db_session: AsyncSession) -> None:
    foreign_keys = await rows(
        db_session,
        "SELECT rc.CONSTRAINT_NAME, rc.TABLE_NAME, k.COLUMN_NAME, rc.REFERENCED_TABLE_NAME, "
        "k.REFERENCED_COLUMN_NAME, rc.DELETE_RULE "
        "FROM information_schema.REFERENTIAL_CONSTRAINTS rc "
        "JOIN information_schema.KEY_COLUMN_USAGE k "
        "  ON k.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA AND k.CONSTRAINT_NAME = rc.CONSTRAINT_NAME "
        "WHERE rc.CONSTRAINT_SCHEMA = DATABASE() AND rc.TABLE_NAME IN ('towers', 'units') "
        "ORDER BY rc.CONSTRAINT_NAME",
    )
    assert foreign_keys == [
        ("fk_towers_block_id_blocks", "towers", "block_id", "blocks", "id", "RESTRICT"),
        ("fk_units_tower_id_towers", "units", "tower_id", "towers", "id", "RESTRICT"),
    ]


async def test_full_identifier_is_not_stored(db_session: AsyncSession) -> None:
    names = await rows(
        db_session,
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN ('blocks', 'towers', 'units') AND COLUMN_NAME REGEXP 'label|code|identifier'",
    )
    assert names == []


# --- CHECK, type and generated column --------------------------------------------


@pytest.mark.parametrize("table", TABLES)
async def test_check_rejects_zero(db_session: AsyncSession, table: str) -> None:
    parent_id = await _parent_for(db_session, table)
    # asyncmy reports CHECK violations (3819) as OperationalError: use DBAPIError.
    with pytest.raises(DBAPIError, match="3819"):
        await insert(db_session, table, 0, parent_id)


@pytest.mark.parametrize("table", TABLES)
async def test_unsigned_type_rejects_negative_numbers(db_session: AsyncSession, table: str) -> None:
    parent_id = await _parent_for(db_session, table)
    with pytest.raises(DBAPIError, match="1264"):  # Out of range value
        await insert(db_session, table, -1, parent_id)


@pytest.mark.parametrize("table", TABLES)
async def test_type_limit_is_technical(db_session: AsyncSession, table: str) -> None:
    parent_id = await _parent_for(db_session, table)
    await insert(db_session, table, 4_294_967_295, parent_id)
    with pytest.raises(DBAPIError, match="1264"):
        await insert(db_session, table, 4_294_967_296, parent_id)


async def test_check_rejects_version_zero(db_session: AsyncSession) -> None:
    block_id = await insert(db_session, "blocks", 1)
    with pytest.raises(DBAPIError, match="3819"):
        await db_session.execute(text("UPDATE blocks SET version = 0 WHERE id = :id"), {"id": block_id})


async def test_generated_column_cannot_be_written(db_session: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="3105"):
        await db_session.execute(text("INSERT INTO blocks (number, active_number_marker) VALUES (1, 1)"))


async def test_marker_follows_soft_delete(db_session: AsyncSession) -> None:
    block_id = await insert(db_session, "blocks", 7)
    assert await db_session.scalar(text("SELECT active_number_marker FROM blocks WHERE id = :id"), {"id": block_id}) == 7
    await db_session.execute(text("UPDATE blocks SET deleted_at = UTC_TIMESTAMP(6) WHERE id = :id"), {"id": block_id})
    assert await db_session.scalar(text("SELECT active_number_marker FROM blocks WHERE id = :id"), {"id": block_id}) is None


# --- UNIQUE on generated columns ----------------------------------------------------


async def test_one_active_block_per_number(db_session: AsyncSession) -> None:
    await insert(db_session, "blocks", 1)
    with pytest.raises(IntegrityError, match="uq_blocks_active_number"):
        await insert(db_session, "blocks", 1)


async def test_deleted_blocks_do_not_block_the_number(db_session: AsyncSession) -> None:
    first = await insert(db_session, "blocks", 1, deleted=True)
    second = await insert(db_session, "blocks", 1, deleted=True)
    active = await insert(db_session, "blocks", 1)
    assert len({first, second, active}) == 3


async def test_restoring_a_deleted_duplicate_is_rejected(db_session: AsyncSession) -> None:
    deleted = await insert(db_session, "blocks", 1, deleted=True)
    await insert(db_session, "blocks", 1)
    with pytest.raises(IntegrityError, match="uq_blocks_active_number"):
        await db_session.execute(text("UPDATE blocks SET deleted_at = NULL WHERE id = :id"), {"id": deleted})


async def test_renumbering_onto_an_active_number_is_rejected(db_session: AsyncSession) -> None:
    await insert(db_session, "blocks", 4)
    block_id = await insert(db_session, "blocks", 1)
    with pytest.raises(IntegrityError, match="uq_blocks_active_number"):
        await db_session.execute(text("UPDATE blocks SET number = 4 WHERE id = :id"), {"id": block_id})


async def test_same_tower_number_in_different_blocks(db_session: AsyncSession) -> None:
    b1 = await insert(db_session, "blocks", 1)
    b2 = await insert(db_session, "blocks", 2)
    await insert(db_session, "towers", 2, b1)
    await insert(db_session, "towers", 2, b2)
    with pytest.raises(IntegrityError, match="uq_towers_block_active_number"):
        await insert(db_session, "towers", 2, b1)


async def test_same_unit_number_in_different_towers(db_session: AsyncSession) -> None:
    b1 = await insert(db_session, "blocks", 1)
    to2 = await insert(db_session, "towers", 2, b1)
    to3 = await insert(db_session, "towers", 3, b1)
    await insert(db_session, "units", 104, to2)
    await insert(db_session, "units", 104, to3)
    await insert(db_session, "units", 104, to2, deleted=True)
    with pytest.raises(IntegrityError, match="uq_units_tower_active_number"):
        await insert(db_session, "units", 104, to2)


# --- Foreign keys ----------------------------------------------------------------


async def test_child_requires_existing_parent(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="fk_towers_block_id_blocks"):
        await insert(db_session, "towers", 1, 999_999_999)


async def test_unit_requires_existing_tower(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="fk_units_tower_id_towers"):
        await insert(db_session, "units", 1, 999_999_999)


async def test_physical_delete_of_parent_is_restricted_even_with_soft_deleted_children(db_session: AsyncSession) -> None:
    block_id = await insert(db_session, "blocks", 1)
    await insert(db_session, "towers", 1, block_id, deleted=True)
    with pytest.raises(IntegrityError, match="fk_towers_block_id_blocks"):
        await db_session.execute(text("DELETE FROM blocks WHERE id = :id"), {"id": block_id})


async def test_physical_delete_of_tower_is_restricted(db_session: AsyncSession) -> None:
    block_id = await insert(db_session, "blocks", 1)
    tower_id = await insert(db_session, "towers", 1, block_id)
    await insert(db_session, "units", 1, tower_id, deleted=True)
    with pytest.raises(IntegrityError, match="fk_units_tower_id_towers"):
        await db_session.execute(text("DELETE FROM towers WHERE id = :id"), {"id": tower_id})


async def _parent_for(db: AsyncSession, table: str) -> int | None:
    if table == "blocks":
        return None
    block_id = await insert(db, "blocks", 1)
    if table == "towers":
        return block_id
    return await insert(db, "towers", 1, block_id)
