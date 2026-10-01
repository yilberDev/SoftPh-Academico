"""HU-013 migration (522bf629af33): upgrade, protected downgrade and pre-checks.

Only ever runs on the dedicated *_test database, and always leaves it at head.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import pool, text
from sqlalchemy.engine import URL

from app.db.session import create_engine
from tests.integration.conftest import alembic_config

REVISION = "522bf629af33"
PREVIOUS = "6236c5b88ac2"
TABLES = {"blocks", "towers", "units"}


def run_sql(url: URL, *statements: str) -> list[Any]:
    async def run() -> list[Any]:
        engine = create_engine(url, poolclass=pool.NullPool)
        results = []
        async with engine.begin() as connection:
            for statement in statements:
                result = await connection.execute(text(statement))
                results.append(result.all() if result.returns_rows else None)
        await engine.dispose()
        return results

    return asyncio.run(run())


def structure_tables(url: URL) -> set[str]:
    [rows] = run_sql(
        url,
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN ('blocks', 'towers', 'units')",
    )
    return {row[0] for row in rows}


def current_revision(url: URL) -> str:
    [rows] = run_sql(url, "SELECT version_num FROM alembic_version")
    return rows[0][0]


@pytest.fixture
def database(migrated_test_database: URL) -> Iterator[URL]:
    assert migrated_test_database.database is not None
    assert migrated_test_database.database.endswith("_test")
    config = alembic_config(migrated_test_database)
    try:
        yield migrated_test_database
    finally:
        # Leave the database empty and at head for the other tests.
        if current_revision(migrated_test_database) == REVISION:
            run_sql(migrated_test_database, "DELETE FROM units", "DELETE FROM towers", "DELETE FROM blocks")
        else:
            # Below this revision these tables can only be leftovers of a failed test.
            for table in ("units", "towers", "blocks"):
                if table in structure_tables(migrated_test_database):
                    run_sql(migrated_test_database, f"DROP TABLE {table}")
            command.upgrade(config, "head")


def test_downgrade_and_upgrade(database: URL) -> None:
    config = alembic_config(database)

    command.downgrade(config, PREVIOUS)
    assert structure_tables(database) == set()
    assert current_revision(database) == PREVIOUS

    command.upgrade(config, REVISION)
    assert structure_tables(database) == TABLES
    assert current_revision(database) == REVISION


@pytest.mark.parametrize("deleted", [False, True], ids=["active-row", "only-soft-deleted-rows"])
def test_downgrade_refuses_to_lose_rows(database: URL, deleted: bool) -> None:
    deleted_at = "UTC_TIMESTAMP(6)" if deleted else "NULL"
    run_sql(database, f"INSERT INTO blocks (number, deleted_at) VALUES (1, {deleted_at})")

    with pytest.raises(RuntimeError, match="rows would be lost.*Nothing was changed"):
        command.downgrade(alembic_config(database), PREVIOUS)

    # Nothing was changed: tables, data and revision are intact.
    assert structure_tables(database) == TABLES
    assert run_sql(database, "SELECT COUNT(*) FROM blocks")[0][0][0] == 1
    assert current_revision(database) == REVISION


def test_downgrade_refuses_with_rows_in_a_child_table(database: URL) -> None:
    run_sql(
        database,
        "INSERT INTO blocks (number, deleted_at) VALUES (1, UTC_TIMESTAMP(6))",
        "INSERT INTO towers (block_id, number, deleted_at) SELECT id, 1, UTC_TIMESTAMP(6) FROM blocks",
        "INSERT INTO units (tower_id, number, deleted_at) SELECT id, 1, UTC_TIMESTAMP(6) FROM towers",
    )

    with pytest.raises(RuntimeError, match="'units': 1"):
        command.downgrade(alembic_config(database), PREVIOUS)
    assert structure_tables(database) == TABLES


def test_upgrade_refuses_when_a_table_already_exists(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, "CREATE TABLE blocks (id BIGINT PRIMARY KEY)")

    with pytest.raises(RuntimeError, match=r"\['blocks'\] already exist.*Nothing was changed"):
        command.upgrade(config, REVISION)

    # The existing table was not touched and nothing else was created.
    assert structure_tables(database) == {"blocks"}
    assert current_revision(database) == PREVIOUS
    run_sql(database, "DROP TABLE blocks")
    command.upgrade(config, REVISION)
    assert structure_tables(database) == TABLES
