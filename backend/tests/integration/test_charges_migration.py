"""Obligations extension migration (f3c8fc28babe): head, round trip from
00d8bbdd687b, protected upgrade and downgrade, and no drift (alembic check).

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

from app.db.session import create_engine
from tests.integration.conftest import alembic_config

REVISION = "f3c8fc28babe"
PREVIOUS = "00d8bbdd687b"
NEW_COLUMNS = {"billing_period", "source_charge_id", "created_by_user_id", "can_originate_late_fee", "late_fee_source_link"}
STRUCTURE = (
    "INSERT INTO users (email, full_name, role, status, password_hash) "
    "VALUES ('admin@example.com', 'Admin Uno', 'ADMINISTRADOR', 'ACTIVE', 'x')",
    "INSERT INTO blocks (number) VALUES (1)",
    "INSERT INTO towers (block_id, number) SELECT id, 1 FROM blocks",
    "INSERT INTO units (tower_id, number) SELECT id, 101 FROM towers",
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


def columns(url: URL) -> set[str]:
    [found] = run_sql(url, "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                           "AND TABLE_NAME = 'charges'")
    return {row[0] for row in found}


def checks(url: URL) -> set[str]:
    [found] = run_sql(url, "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                           "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'charges' AND CONSTRAINT_TYPE = 'CHECK'")
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
        found = columns(migrated_test_database)
        # Late fees before their origins (self-referencing foreign key), then the rest.
        late_fees_first = ("DELETE FROM charges WHERE source_charge_id IS NOT NULL",) if "source_charge_id" in found else ()
        run_sql(migrated_test_database, *late_fees_first, "DELETE FROM charges", "DELETE FROM units",
                "DELETE FROM towers", "DELETE FROM blocks", "DELETE FROM users")
        if current_revision(migrated_test_database) == PREVIOUS and found & NEW_COLUMNS == {"billing_period"}:
            run_sql(migrated_test_database, "ALTER TABLE charges DROP COLUMN billing_period")  # leftover of a failed test
        command.upgrade(config, "head")


def test_revision_is_the_head_after_hu005(migrated_test_database: URL) -> None:
    script = ScriptDirectory.from_config(alembic_config(migrated_test_database))
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS
    assert current_revision(migrated_test_database) == REVISION


def test_models_match_the_database(migrated_test_database: URL) -> None:
    # Same as `alembic check`: raises if autogenerate finds any difference.
    command.check(alembic_config(migrated_test_database))


def test_upgrade_downgrade_upgrade(database: URL) -> None:
    config = alembic_config(database)
    assert NEW_COLUMNS <= columns(database)
    command.downgrade(config, PREVIOUS)
    assert not columns(database) & NEW_COLUMNS and current_revision(database) == PREVIOUS
    # The HU-004 rule for OTHER is back; the new rules are gone.
    assert checks(database) == {"ck_charges_amount_positive", "ck_charges_concept_valid", "ck_charges_other_has_description"}
    command.upgrade(config, REVISION)
    assert NEW_COLUMNS <= columns(database) and current_revision(database) == REVISION
    assert "ck_charges_other_has_description" not in checks(database)


def test_downgrade_refuses_to_lose_obligations(database: URL) -> None:
    run_sql(
        database, *STRUCTURE,
        "INSERT INTO charges (unit_id, concept, amount, due_date, billing_period, created_by_user_id) "
        "SELECT units.id, 'MONTHLY_FEE', 100000, '2026-10-05', '2026-10-01', users.id FROM units, users",
    )
    with pytest.raises(RuntimeError, match="1 rows in charges would lose data.*Nothing was changed"):
        command.downgrade(alembic_config(database), PREVIOUS)
    assert NEW_COLUMNS <= columns(database) and current_revision(database) == REVISION


def test_upgrade_refuses_existing_charges_without_an_author(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, *STRUCTURE,
            "INSERT INTO charges (unit_id, concept, amount, due_date) SELECT id, 'MONTHLY_FEE', 100000, '2026-10-05' FROM units")
    with pytest.raises(RuntimeError, match="1 rows in charges cannot get an author.*Nothing was changed"):
        command.upgrade(config, REVISION)
    assert current_revision(database) == PREVIOUS and not columns(database) & NEW_COLUMNS
    # Never deleted or backfilled automatically: the rows are still there.
    assert run_sql(database, "SELECT COUNT(*) FROM charges") == [[(1,)]]


def test_upgrade_refuses_when_a_column_exists(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, "ALTER TABLE charges ADD COLUMN billing_period DATE NULL")
    with pytest.raises(RuntimeError, match="already exist.*billing_period.*Nothing was changed"):
        command.upgrade(config, REVISION)
    assert current_revision(database) == PREVIOUS
    run_sql(database, "ALTER TABLE charges DROP COLUMN billing_period")
    command.upgrade(config, REVISION)
    assert NEW_COLUMNS <= columns(database)
