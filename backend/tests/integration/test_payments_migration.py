"""HU-004 migration (6afae8f829c4): round trip, protected downgrade and pre-check.

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

REVISION = "6afae8f829c4"
PREVIOUS = "b1b38cfb7fe2"
TABLES = ("charges", "payments", "payment_allocations")


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


def present(url: URL) -> set[str]:
    [found] = run_sql(url, "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                           "AND TABLE_NAME IN ('charges', 'payments', 'payment_allocations')")
    return {row[0] for row in found}


def current_revision(url: URL) -> str:
    [rows] = run_sql(url, "SELECT version_num FROM alembic_version")
    return rows[0][0]


@pytest.fixture
def database(migrated_test_database: URL) -> Iterator[URL]:
    assert migrated_test_database.database is not None and migrated_test_database.database.endswith("_test")
    config = alembic_config(migrated_test_database)
    command.downgrade(config, REVISION)  # later revisions first, if any
    try:
        yield migrated_test_database
    finally:
        if current_revision(migrated_test_database) == REVISION:
            run_sql(migrated_test_database, *(f"DELETE FROM {t}" for t in reversed(TABLES)),
                    "DELETE FROM resident_units", "DELETE FROM resident_invitations", "DELETE FROM units",
                    "DELETE FROM towers", "DELETE FROM blocks", "DELETE FROM users")
        else:
            for table in reversed(TABLES):  # leftovers of a failed test
                if table in present(migrated_test_database):
                    run_sql(migrated_test_database, f"DROP TABLE {table}")
        command.upgrade(config, "head")


def test_upgrade_downgrade_upgrade(database: URL) -> None:
    config = alembic_config(database)
    assert present(database) == set(TABLES)
    command.downgrade(config, PREVIOUS)
    assert present(database) == set() and current_revision(database) == PREVIOUS
    command.upgrade(config, REVISION)
    assert present(database) == set(TABLES) and current_revision(database) == REVISION


def test_downgrade_refuses_to_lose_financial_rows(database: URL) -> None:
    run_sql(
        database,
        "INSERT INTO blocks (number) VALUES (1)",
        "INSERT INTO towers (block_id, number) SELECT id, 1 FROM blocks",
        "INSERT INTO units (tower_id, number) SELECT id, 101 FROM towers",
        "INSERT INTO charges (unit_id, concept, amount, due_date) SELECT id, 'MONTHLY_FEE', 100000, '2026-01-05' FROM units",
    )
    with pytest.raises(RuntimeError, match="rows in charges would be lost.*Nothing was changed"):
        command.downgrade(alembic_config(database), PREVIOUS)
    assert present(database) == set(TABLES) and current_revision(database) == REVISION


def test_upgrade_refuses_when_a_table_exists(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, "CREATE TABLE payments (id BIGINT PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="already exist.*Nothing was changed"):
        command.upgrade(config, REVISION)
    assert current_revision(database) == PREVIOUS and present(database) == {"payments"}
    run_sql(database, "DROP TABLE payments")
    command.upgrade(config, REVISION)
    assert present(database) == set(TABLES)
