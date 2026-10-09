"""HU-009 migration (3e565db40412): place in the chain, round trip from 4385fd452231, the feedback stored before
HU-009 kept as it was (category and urgency NULL), the database rules, protected downgrade.

Only ever runs on the dedicated *_test database, and always leaves it at head.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import pool, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from app.db.session import create_engine
from tests.integration.conftest import alembic_config

REVISION = "3e565db40412"
PREVIOUS = "4385fd452231"
NEW_COLUMNS = {"category", "urgency"}
NEW_CHECKS = {"ck_feedback_category_valid", "ck_feedback_urgency_valid", "ck_feedback_category_urgency_together"}
ALL_COLUMNS = ("id, resident_id, subject, message, status, admin_response, resolution_note, version, created_at, "
               "updated_at, resolved_at")
# Feedback as HU-005 stored it before this revision: one pending, one answered and resolved.
HISTORICAL = (
    "INSERT INTO users (email, full_name, role, status, password_hash) VALUES "
    "('laura@example.com', 'Laura Gómez', 'RESIDENTE', 'ACTIVE', 'x')",
    "INSERT INTO feedback (resident_id, subject, message) SELECT id, NULL, 'Hay ruido.\\nMucho.' FROM users",
    "INSERT INTO feedback (resident_id, subject, message, status, admin_response, resolution_note, version, resolved_at) "
    "SELECT id, 'Luz', 'No hay luz.', 'RESOLVED', 'Ya se arregló.', 'Se llamó al técnico.', 3, UTC_TIMESTAMP(6) FROM users",
)


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


def columns(url: URL) -> dict[str, tuple[Any, ...]]:
    [found] = run_sql(url, "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLLATION_NAME "
                           "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'feedback'")
    return {row[0]: tuple(row[1:]) for row in found}


def checks(url: URL) -> set[str]:
    [found] = run_sql(url, "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                           "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'feedback' AND CONSTRAINT_TYPE = 'CHECK'")
    return {row[0] for row in found}


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
        run_sql(migrated_test_database, "DELETE FROM feedback", "DELETE FROM users")
        found = set(columns(migrated_test_database))
        if current_revision(migrated_test_database) == PREVIOUS and found & NEW_COLUMNS:
            # Leftover of a failed test: only columns, never data (the table is empty here).
            run_sql(migrated_test_database, *(f"ALTER TABLE feedback DROP COLUMN {c}" for c in sorted(found & NEW_COLUMNS)))
        command.upgrade(config, "head")


def test_revision_follows_hu008(migrated_test_database: URL) -> None:
    script = ScriptDirectory.from_config(alembic_config(migrated_test_database))
    assert script.get_revision(REVISION).down_revision == PREVIOUS
    # HU-010 (82e81b7ab23d) comes after it; that migration's tests check the head.
    assert REVISION in {revision.revision for revision in script.walk_revisions()}


def test_models_match_the_database(migrated_test_database: URL) -> None:
    # Same as `alembic check`: raises if autogenerate finds any difference.
    command.check(alembic_config(migrated_test_database))


def test_upgrade_downgrade_upgrade(database: URL) -> None:
    config = alembic_config(database)
    found = columns(database)
    # Nullable, no default, exact (binary) comparison.
    assert found["category"] == found["urgency"] == ("varchar(20)", "YES", None, "utf8mb4_0900_bin")
    assert NEW_CHECKS <= checks(database)

    command.downgrade(config, PREVIOUS)
    assert not set(columns(database)) & NEW_COLUMNS and not checks(database) & NEW_CHECKS
    assert current_revision(database) == PREVIOUS

    command.upgrade(config, REVISION)
    assert NEW_COLUMNS <= set(columns(database)) and NEW_CHECKS <= checks(database)
    assert current_revision(database) == REVISION


def test_historical_feedback_keeps_every_value_and_gets_no_category_or_urgency(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, *HISTORICAL)
    [before] = run_sql(database, f"SELECT {ALL_COLUMNS} FROM feedback ORDER BY id")

    command.upgrade(config, REVISION)

    # Nothing invented (no backfill), and every other value unchanged.
    assert run_sql(database, "SELECT category, urgency FROM feedback ORDER BY id") == [[(None, None), (None, None)]]
    assert run_sql(database, f"SELECT {ALL_COLUMNS} FROM feedback ORDER BY id") == [before]
    # Feedback with only NULL in them can go back and forth.
    command.downgrade(config, PREVIOUS)
    assert run_sql(database, f"SELECT {ALL_COLUMNS} FROM feedback ORDER BY id") == [before]
    command.upgrade(config, REVISION)


def rejected(url: URL, statement: str) -> bool:
    try:
        run_sql(url, statement)
    except DBAPIError as error:
        assert error.orig.args[0] == 3819, error.orig  # type: ignore[union-attr]  # check constraint violated
        return True
    return False


def test_the_database_accepts_only_the_approved_values(database: URL) -> None:
    run_sql(database, *HISTORICAL)
    for category in ("COMPLAINT", "SUGGESTION", "REQUEST", "OTHER"):
        for urgency in ("HIGH", "MEDIUM", "LOW"):
            run_sql(database, f"UPDATE feedback SET category = '{category}', urgency = '{urgency}' WHERE subject IS NULL")
            assert run_sql(database, "SELECT category, urgency FROM feedback WHERE subject IS NULL") == [[(category, urgency)]]
    for category, urgency in (("complaint", "'HIGH'"), ("Queja", "'HIGH'"), ("OTHER", "'high'"), ("OTHER", "'URGENT'"),
                              ("OTHER", "NULL"), ("", "'LOW'")):
        statement = (f"INSERT INTO feedback (resident_id, message, category, urgency) "
                     f"SELECT id, 'x', '{category}', {urgency} FROM users")
        assert rejected(database, statement), (category, urgency)
    assert rejected(database, "INSERT INTO feedback (resident_id, message, category, urgency) SELECT id, 'x', NULL, 'LOW' FROM users")


def test_downgrade_refuses_to_lose_categories(database: URL) -> None:
    run_sql(database, *HISTORICAL, "UPDATE feedback SET category = 'REQUEST', urgency = 'LOW' WHERE subject = 'Luz'")
    with pytest.raises(RuntimeError, match="1 rows in feedback would lose data.*Nothing was changed"):
        command.downgrade(alembic_config(database), PREVIOUS)
    assert NEW_COLUMNS <= set(columns(database)) and current_revision(database) == REVISION
    assert run_sql(database, "SELECT category, urgency FROM feedback WHERE subject = 'Luz'") == [[("REQUEST", "LOW")]]


def test_upgrade_refuses_when_a_column_exists(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, "ALTER TABLE feedback ADD COLUMN urgency VARCHAR(20) NULL")
    with pytest.raises(RuntimeError, match="already exist.*urgency.*Nothing was changed"):
        command.upgrade(config, REVISION)
    assert current_revision(database) == PREVIOUS and "category" not in columns(database)
    run_sql(database, "ALTER TABLE feedback DROP COLUMN urgency")
    command.upgrade(config, REVISION)
    assert NEW_COLUMNS <= set(columns(database))
