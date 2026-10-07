"""HU-008 migration (4385fd452231): head, round trip from f3c8fc28babe, existing
payments kept with NULL, protected downgrade and no drift (alembic check).

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

REVISION = "4385fd452231"
PREVIOUS = "f3c8fc28babe"
NEW_COLUMNS = {"transaction_reference", "notes"}
NEW_CHECKS = {"ck_payments_transaction_reference_not_blank", "ck_payments_notes_not_blank"}
# A payment as HU-004 stored it before this revision.
PAYMENT = (
    "INSERT INTO users (email, full_name, role, status, password_hash) VALUES "
    "('admin@example.com', 'Admin Uno', 'ADMINISTRADOR', 'ACTIVE', 'x'), "
    "('laura@example.com', 'Laura Gómez', 'RESIDENTE', 'ACTIVE', 'x')",
    "INSERT INTO blocks (number) VALUES (1)",
    "INSERT INTO towers (block_id, number) SELECT id, 1 FROM blocks",
    "INSERT INTO units (tower_id, number) SELECT id, 101 FROM towers",
    "INSERT INTO payments (resident_id, unit_id, registered_by_user_id, amount, method, concept, payment_date, "
    "registered_at, resident_name_snapshot, unit_label_snapshot) "
    "SELECT r.id, units.id, a.id, 150000, 'TRANSFER', 'MONTHLY_FEE', '2026-10-01', UTC_TIMESTAMP(6), 'Laura Gómez', "
    "'B1-TO1-APTO-101' FROM users r, users a, units WHERE r.role = 'RESIDENTE' AND a.role = 'ADMINISTRADOR'",
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
                           "AND TABLE_NAME = 'payments'")
    return {row[0] for row in found}


def checks(url: URL) -> set[str]:
    [found] = run_sql(url, "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                           "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payments' AND CONSTRAINT_TYPE = 'CHECK'")
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
        run_sql(migrated_test_database, "DELETE FROM payments", "DELETE FROM units", "DELETE FROM towers",
                "DELETE FROM blocks", "DELETE FROM users")
        found = columns(migrated_test_database)
        if current_revision(migrated_test_database) == PREVIOUS and found & NEW_COLUMNS:
            # Leftover of a failed test: only columns, never data (the table is empty here).
            run_sql(migrated_test_database, *(f"ALTER TABLE payments DROP COLUMN {c}" for c in sorted(found & NEW_COLUMNS)))
        command.upgrade(config, "head")


def test_revision_is_the_head_after_the_obligations_extension(migrated_test_database: URL) -> None:
    script = ScriptDirectory.from_config(alembic_config(migrated_test_database))
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS
    assert current_revision(migrated_test_database) == REVISION


def test_models_match_the_database(migrated_test_database: URL) -> None:
    # Same as `alembic check`: raises if autogenerate finds any difference.
    command.check(alembic_config(migrated_test_database))


def test_upgrade_downgrade_upgrade(database: URL) -> None:
    config = alembic_config(database)
    assert NEW_COLUMNS <= columns(database) and NEW_CHECKS <= checks(database)
    command.downgrade(config, PREVIOUS)
    assert not columns(database) & NEW_COLUMNS and not checks(database) & NEW_CHECKS
    assert current_revision(database) == PREVIOUS
    command.upgrade(config, REVISION)
    assert NEW_COLUMNS <= columns(database) and NEW_CHECKS <= checks(database)
    assert current_revision(database) == REVISION


def test_existing_payments_stay_valid_with_null(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, *PAYMENT)
    [before] = run_sql(database, "SELECT id, amount, method, concept, payment_date, registered_at FROM payments")

    command.upgrade(config, REVISION)

    # Nothing invented: no reference, no notes; every other value unchanged.
    assert run_sql(database, "SELECT transaction_reference, notes FROM payments") == [[(None, None)]]
    assert run_sql(database, "SELECT id, amount, method, concept, payment_date, registered_at FROM payments") == [before]
    # A payment with only NULL in them can go back and forth.
    command.downgrade(config, PREVIOUS)
    assert run_sql(database, "SELECT COUNT(*) FROM payments") == [[(1,)]]
    command.upgrade(config, REVISION)


@pytest.mark.parametrize("column", sorted(NEW_COLUMNS))
def test_downgrade_refuses_to_lose_references_or_notes(database: URL, column: str) -> None:
    run_sql(database, *PAYMENT, f"UPDATE payments SET {column} = 'TRF-0042'")
    with pytest.raises(RuntimeError, match="1 rows in payments would lose data.*Nothing was changed"):
        command.downgrade(alembic_config(database), PREVIOUS)
    assert NEW_COLUMNS <= columns(database) and current_revision(database) == REVISION
    assert run_sql(database, f"SELECT {column} FROM payments") == [[("TRF-0042",)]]


def test_upgrade_refuses_when_a_column_exists(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, "ALTER TABLE payments ADD COLUMN notes VARCHAR(500) NULL")
    with pytest.raises(RuntimeError, match="already exist.*notes.*Nothing was changed"):
        command.upgrade(config, REVISION)
    assert current_revision(database) == PREVIOUS and "transaction_reference" not in columns(database)
    run_sql(database, "ALTER TABLE payments DROP COLUMN notes")
    command.upgrade(config, REVISION)
    assert NEW_COLUMNS <= columns(database)
