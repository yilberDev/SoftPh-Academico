"""HU-003 migration (4cfaeb7a0be8): upgrade, protected downgrade and pre-checks.

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

REVISION = "4cfaeb7a0be8"
PREVIOUS = "522bf629af33"
TABLES = {"resident_invitations", "resident_units"}


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


def invitation_tables(url: URL) -> set[str]:
    [rows] = run_sql(
        url,
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN ('resident_invitations', 'resident_units')",
    )
    return {row[0] for row in rows}


def current_revision(url: URL) -> str:
    [rows] = run_sql(url, "SELECT version_num FROM alembic_version")
    return rows[0][0]


SEED = (
    "INSERT INTO users (email, full_name, role, status) VALUES ('m@example.com', 'X Y Z', 'RESIDENTE', 'PENDING_ACTIVATION')",
    "INSERT INTO blocks (number) VALUES (1)",
    "INSERT INTO towers (block_id, number) SELECT id, 1 FROM blocks",
    "INSERT INTO units (tower_id, number) SELECT id, 1 FROM towers",
    "INSERT INTO resident_invitations (token_hash, user_id, unit_id, invited_by_user_id, validity_days, status, "
    "created_at, expires_at) SELECT REPEAT('c', 64), u.id, n.id, u.id, 7, 'EXPIRED', UTC_TIMESTAMP(6), "
    "UTC_TIMESTAMP(6) + INTERVAL 1 DAY FROM users u, units n",
)
CLEANUP = (
    "DELETE FROM resident_units", "DELETE FROM resident_invitations", "DELETE FROM units",
    "DELETE FROM towers", "DELETE FROM blocks", "DELETE FROM users",
)


@pytest.fixture
def database(migrated_test_database: URL) -> Iterator[URL]:
    assert migrated_test_database.database is not None
    assert migrated_test_database.database.endswith("_test")
    config = alembic_config(migrated_test_database)
    command.downgrade(config, REVISION)  # later revisions (HU-016) first
    try:
        yield migrated_test_database
    finally:
        if current_revision(migrated_test_database) == REVISION:
            run_sql(migrated_test_database, *CLEANUP)
        else:
            # Below this revision these tables can only be leftovers of a failed test.
            for table in ("resident_units", "resident_invitations"):
                if table in invitation_tables(migrated_test_database):
                    run_sql(migrated_test_database, f"DROP TABLE {table}")
            run_sql(migrated_test_database, *CLEANUP[2:])
        command.upgrade(config, "head")


def test_downgrade_and_upgrade(database: URL) -> None:
    config = alembic_config(database)

    command.downgrade(config, PREVIOUS)
    assert invitation_tables(database) == set()
    assert current_revision(database) == PREVIOUS

    command.upgrade(config, REVISION)
    assert invitation_tables(database) == TABLES
    assert current_revision(database) == REVISION


def test_downgrade_refuses_to_lose_invitations(database: URL) -> None:
    run_sql(database, *SEED)

    with pytest.raises(RuntimeError, match="rows would be lost.*Nothing was changed"):
        command.downgrade(alembic_config(database), PREVIOUS)

    assert invitation_tables(database) == TABLES
    assert run_sql(database, "SELECT COUNT(*) FROM resident_invitations")[0][0][0] == 1
    assert current_revision(database) == REVISION


def test_upgrade_refuses_when_a_table_already_exists(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, "CREATE TABLE resident_units (id BIGINT PRIMARY KEY)")

    with pytest.raises(RuntimeError, match=r"\['resident_units'\] already exist.*Nothing was changed"):
        command.upgrade(config, REVISION)

    assert invitation_tables(database) == {"resident_units"}
    assert current_revision(database) == PREVIOUS
    run_sql(database, "DROP TABLE resident_units")
    command.upgrade(config, REVISION)
    assert invitation_tables(database) == TABLES
