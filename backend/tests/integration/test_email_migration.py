"""Migration 6236c5b88ac2 (A-1): both directions validate before any DDL.

A failed validation must leave the structure exactly as it was. Only ever
runs on the dedicated *_test database. Each test starts at this revision (later
revisions are downgraded first) and the schema is restored to head afterwards.
"""

import asyncio
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import pool, text
from sqlalchemy.engine import URL

from app.db.session import create_engine
from tests.integration.conftest import alembic_config

REVISION = "6236c5b88ac2"
PREVIOUS = "5cde3a1e780c"


def sql(url: URL, statement: str, **params: object) -> list[tuple]:
    async def run() -> list[tuple]:
        engine = create_engine(url, poolclass=pool.NullPool)
        async with engine.begin() as connection:
            result = await connection.execute(text(statement), params)
            rows = [tuple(r) for r in result] if result.returns_rows else []
        await engine.dispose()
        return rows

    return asyncio.run(run())


def structure(url: URL) -> dict[str, object]:
    return {
        "revision": sql(url, "SELECT version_num FROM alembic_version")[0][0],
        "collation": sql(
            url,
            "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'email'",
        )[0][0],
        "checks": sorted(
            r[0]
            for r in sql(
                url,
                "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND CONSTRAINT_NAME LIKE 'ck_users_email_%'",
            )
        ),
    }


REVISION_STRUCTURE = {"revision": REVISION, "collation": "utf8mb4_0900_bin", "checks": ["ck_users_email_ascii", "ck_users_email_canonical"]}
PREVIOUS_STRUCTURE = {"revision": PREVIOUS, "collation": "utf8mb4_0900_ai_ci", "checks": []}


@pytest.fixture
def test_url(migrated_test_database: URL) -> Iterator[URL]:
    assert migrated_test_database.database is not None and migrated_test_database.database.endswith("_test")
    config = alembic_config(migrated_test_database)
    command.downgrade(config, REVISION)
    yield migrated_test_database
    # Always leave the test database empty and at head.
    sql(migrated_test_database, "DELETE FROM users")
    if structure(migrated_test_database)["revision"] == REVISION and "ck_users_email_ascii" not in structure(migrated_test_database)["checks"]:
        sql(migrated_test_database, "ALTER TABLE users ADD CONSTRAINT ck_users_email_ascii CHECK (CHAR_LENGTH(email) = LENGTH(email))")
    command.upgrade(config, "head")
    head = ScriptDirectory.from_config(config).get_current_head()
    assert structure(migrated_test_database) == {**REVISION_STRUCTURE, "revision": head}


def insert_user(url: URL, email: str) -> None:
    sql(url, "INSERT INTO users (email, full_name, role, status) VALUES (:email, 'X Y Z', 'RESIDENTE', 'DISABLED')", email=email)


def test_clean_downgrade_and_upgrade(test_url: URL) -> None:
    assert structure(test_url) == REVISION_STRUCTURE

    command.downgrade(alembic_config(test_url), PREVIOUS)
    assert structure(test_url) == PREVIOUS_STRUCTURE

    command.upgrade(alembic_config(test_url), REVISION)
    assert structure(test_url) == REVISION_STRUCTURE


@pytest.mark.parametrize("email", ["Admin@Example.com", " admin@example.com", "josé@example.com"], ids=["upper", "space", "non-ascii"])
def test_upgrade_aborts_on_non_canonical_email_without_changes(test_url: URL, email: str) -> None:
    command.downgrade(alembic_config(test_url), PREVIOUS)
    insert_user(test_url, email)

    with pytest.raises(RuntimeError, match="Nothing was changed"):
        command.upgrade(alembic_config(test_url), REVISION)

    assert structure(test_url) == PREVIOUS_STRUCTURE
    assert sql(test_url, "SELECT email FROM users") == [(email,)]  # data untouched


def test_downgrade_aborts_on_future_duplicates_without_changes(test_url: URL) -> None:
    # Duplicates under utf8mb4_0900_ai_ci need a non-ASCII email, which the ASCII
    # CHECK forbids; drop only that CHECK to build the scenario.
    sql(test_url, "ALTER TABLE users DROP CHECK ck_users_email_ascii")
    insert_user(test_url, "admin@example.com")
    insert_user(test_url, "ádmin@example.com")
    before = structure(test_url)

    with pytest.raises(RuntimeError, match="would be duplicates"):
        command.downgrade(alembic_config(test_url), PREVIOUS)

    # Validated first: the remaining CHECK and the collation are untouched.
    assert structure(test_url) == before == {**REVISION_STRUCTURE, "checks": ["ck_users_email_canonical"]}


def test_downgrade_aborts_on_missing_check_without_changes(test_url: URL) -> None:
    sql(test_url, "ALTER TABLE users DROP CHECK ck_users_email_ascii")
    before = structure(test_url)

    with pytest.raises(RuntimeError, match="are missing"):
        command.downgrade(alembic_config(test_url), PREVIOUS)

    assert structure(test_url) == before
