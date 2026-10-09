"""HU-010 migration (82e81b7ab23d): head after HU-009, round trip from 3e565db40412 (the table and
every constraint, index and foreign key gone, then back), protected downgrade and upgrade, no drift
(alembic check), existing financial data untouched.

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

REVISION = "82e81b7ab23d"
PREVIOUS = "3e565db40412"
TABLE = "demo_payment_transactions"
CONSTRAINTS = {
    ("PRIMARY", "PRIMARY KEY"),
    ("uq_demo_payment_transactions_reference", "UNIQUE"),
    ("uq_demo_payment_transactions_payment_id", "UNIQUE"),
    ("fk_demo_payment_transactions_resident_id_users", "FOREIGN KEY"),
    ("fk_demo_payment_transactions_charge_id_charges", "FOREIGN KEY"),
    ("fk_demo_payment_transactions_payment_id_payments", "FOREIGN KEY"),
    ("ck_demo_payment_transactions_amount_positive", "CHECK"),
    ("ck_demo_payment_transactions_status_valid", "CHECK"),
    ("ck_demo_payment_transactions_reference_not_blank", "CHECK"),
    ("ck_demo_payment_transactions_expires_after_creation", "CHECK"),
    ("ck_demo_payment_transactions_finished_unless_initiated", "CHECK"),
    ("ck_demo_payment_transactions_approved_has_payment", "CHECK"),
}
INDEXES = {
    "PRIMARY", "uq_demo_payment_transactions_reference", "uq_demo_payment_transactions_payment_id",
    "ix_demo_payment_transactions_resident_id", "ix_demo_payment_transactions_charge_id",
}
# A resident, an obligation and a manual payment (HU-004), and a demo transaction of that obligation.
FINANCIAL = (
    "INSERT INTO users (email, full_name, role, status, password_hash) VALUES "
    "('admin@example.com', 'Admin Uno', 'ADMINISTRADOR', 'ACTIVE', 'x'), "
    "('laura@example.com', 'Laura Gómez', 'RESIDENTE', 'ACTIVE', 'x')",
    "INSERT INTO blocks (number) VALUES (1)",
    "INSERT INTO towers (block_id, number) SELECT id, 1 FROM blocks",
    "INSERT INTO units (tower_id, number) SELECT id, 101 FROM towers",
    "INSERT INTO charges (unit_id, concept, amount, due_date, billing_period, created_by_user_id) "
    "SELECT units.id, 'MONTHLY_FEE', 150000, '2026-10-05', '2026-10-01', a.id FROM units, users a "
    "WHERE a.role = 'ADMINISTRADOR'",
    "INSERT INTO payments (resident_id, unit_id, registered_by_user_id, amount, method, concept, payment_date, "
    "registered_at, resident_name_snapshot, unit_label_snapshot) "
    "SELECT r.id, units.id, a.id, 150000, 'TRANSFER', 'MONTHLY_FEE', '2026-10-01', UTC_TIMESTAMP(6), 'Laura Gómez', "
    "'B1-TO1-APTO-101' FROM users r, users a, units WHERE r.role = 'RESIDENTE' AND a.role = 'ADMINISTRADOR'",
)
TRANSACTION = (
    "INSERT INTO demo_payment_transactions (resident_id, charge_id, amount, reference, status, expires_at) "
    "SELECT users.id, charges.id, 50000, 'DEMO-0001', 'INITIATED', UTC_TIMESTAMP(6) + INTERVAL 15 MINUTE "
    "FROM users, charges WHERE users.role = 'RESIDENTE'"
)
FINANCIAL_ROWS = (
    "SELECT (SELECT COUNT(*) FROM charges), (SELECT COUNT(*) FROM payments), (SELECT COUNT(*) FROM payment_allocations), "
    "(SELECT GROUP_CONCAT(CONCAT_WS('|', id, amount, method, source, payment_date, registered_at)) FROM payments)"
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


def table_exists(url: URL) -> bool:
    [found] = run_sql(url, "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                           f"AND TABLE_NAME = '{TABLE}'")
    return bool(found[0][0])


def constraints(url: URL) -> set[tuple[str, str]]:
    [found] = run_sql(url, "SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE FROM information_schema.TABLE_CONSTRAINTS "
                           f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{TABLE}'")
    return {(row[0], row[1]) for row in found}


def leftovers(url: URL) -> list[list[Any]]:
    """Anything of the table left in the catalog: indexes, foreign keys (both sides) and CHECKs by name."""
    return run_sql(
        url,
        f"SELECT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{TABLE}'",
        "SELECT CONSTRAINT_NAME FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() "
        f"AND (TABLE_NAME = '{TABLE}' OR CONSTRAINT_NAME LIKE '%demo_payment_transactions%')",
        "SELECT CONSTRAINT_NAME FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() "
        "AND CONSTRAINT_NAME LIKE 'ck_demo_payment_transactions_%'",
    )


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
        if table_exists(migrated_test_database):
            run_sql(migrated_test_database, f"DELETE FROM {TABLE}")
        run_sql(migrated_test_database, "DELETE FROM payments", "DELETE FROM charges", "DELETE FROM units",
                "DELETE FROM towers", "DELETE FROM blocks", "DELETE FROM users")
        if current_revision(migrated_test_database) == PREVIOUS and table_exists(migrated_test_database):
            # Leftover of a failed test: an empty table, never data (emptied above).
            run_sql(migrated_test_database, f"DROP TABLE {TABLE}")
        command.upgrade(config, "head")


def test_revision_is_the_head_after_hu009(migrated_test_database: URL) -> None:
    script = ScriptDirectory.from_config(alembic_config(migrated_test_database))
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS
    assert current_revision(migrated_test_database) == REVISION


def test_models_match_the_database(migrated_test_database: URL) -> None:
    # Same as `alembic check`: raises if autogenerate finds any difference.
    command.check(alembic_config(migrated_test_database))


def test_upgrade_downgrade_upgrade(database: URL) -> None:
    config = alembic_config(database)
    assert table_exists(database) and constraints(database) == CONSTRAINTS
    [indexes, _, _] = leftovers(database)
    assert {row[0] for row in indexes} == INDEXES

    command.downgrade(config, PREVIOUS)
    # Nothing left behind: no table, index, foreign key or CHECK.
    assert not table_exists(database) and constraints(database) == set()
    assert leftovers(database) == [[], [], []]
    assert current_revision(database) == PREVIOUS

    command.upgrade(config, REVISION)
    assert table_exists(database) and constraints(database) == CONSTRAINTS
    assert current_revision(database) == REVISION


def test_existing_financial_data_is_untouched(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, *FINANCIAL)
    [before] = run_sql(database, FINANCIAL_ROWS)

    command.upgrade(config, REVISION)
    assert run_sql(database, FINANCIAL_ROWS) == [before]
    assert run_sql(database, f"SELECT COUNT(*) FROM {TABLE}") == [[(0,)]]  # nothing invented

    # An empty table goes back, and the financial data stays as it was.
    command.downgrade(config, PREVIOUS)
    assert run_sql(database, FINANCIAL_ROWS) == [before]
    command.upgrade(config, REVISION)


def test_downgrade_refuses_to_lose_transactions(database: URL) -> None:
    run_sql(database, *FINANCIAL, TRANSACTION)
    with pytest.raises(RuntimeError, match=f"1 rows in {TABLE} would be lost.*Nothing was changed"):
        command.downgrade(alembic_config(database), PREVIOUS)
    assert table_exists(database) and current_revision(database) == REVISION
    assert run_sql(database, f"SELECT reference, status FROM {TABLE}") == [[("DEMO-0001", "INITIATED")]]


def test_upgrade_refuses_when_the_table_exists(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, f"CREATE TABLE {TABLE} (id BIGINT NOT NULL PRIMARY KEY)")
    with pytest.raises(RuntimeError, match=f"already exists: {TABLE}.*Nothing was changed"):
        command.upgrade(config, REVISION)
    assert current_revision(database) == PREVIOUS
    run_sql(database, f"DROP TABLE {TABLE}")
    command.upgrade(config, REVISION)
    assert constraints(database) == CONSTRAINTS
