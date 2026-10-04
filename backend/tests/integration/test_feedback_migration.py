"""HU-005 migration (00d8bbdd687b): round trip, protected downgrade and pre-check.

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

REVISION = "00d8bbdd687b"
PREVIOUS = "6afae8f829c4"


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


def table_exists(url: URL) -> bool:
    [found] = run_sql(url, "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                           "AND TABLE_NAME = 'feedback'")
    return bool(found[0][0])


def current_revision(url: URL) -> str:
    [found] = run_sql(url, "SELECT version_num FROM alembic_version")
    return found[0][0]


@pytest.fixture
def database(migrated_test_database: URL) -> Iterator[URL]:
    assert migrated_test_database.database is not None and migrated_test_database.database.endswith("_test")
    config = alembic_config(migrated_test_database)
    command.downgrade(config, REVISION)  # later revisions first, if any
    try:
        yield migrated_test_database
    finally:
        if current_revision(migrated_test_database) == REVISION:
            run_sql(migrated_test_database, "DELETE FROM feedback", "DELETE FROM users")
        elif table_exists(migrated_test_database):
            run_sql(migrated_test_database, "DROP TABLE feedback")  # leftover of a failed test
        command.upgrade(config, "head")


def test_upgrade_downgrade_upgrade(database: URL) -> None:
    config = alembic_config(database)
    assert table_exists(database)
    command.downgrade(config, PREVIOUS)
    assert not table_exists(database) and current_revision(database) == PREVIOUS
    command.upgrade(config, REVISION)
    assert table_exists(database) and current_revision(database) == REVISION


def test_downgrade_refuses_to_lose_feedback(database: URL) -> None:
    run_sql(
        database,
        "INSERT INTO users (email, full_name, role, status, password_hash) VALUES ('r@example.com', 'X Y Z', 'RESIDENTE', 'ACTIVE', 'x')",
        "INSERT INTO feedback (resident_id, message) SELECT id, 'Hola' FROM users",
    )
    with pytest.raises(RuntimeError, match="rows would be lost.*Nothing was changed"):
        command.downgrade(alembic_config(database), PREVIOUS)
    assert table_exists(database) and current_revision(database) == REVISION


def test_upgrade_refuses_when_the_table_exists(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, "CREATE TABLE feedback (id BIGINT PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="already exists.*Nothing was changed"):
        command.upgrade(config, REVISION)
    assert current_revision(database) == PREVIOUS
    run_sql(database, "DROP TABLE feedback")
    command.upgrade(config, REVISION)
    assert table_exists(database)
