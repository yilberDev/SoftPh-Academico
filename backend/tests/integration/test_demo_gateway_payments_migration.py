"""HU-010 migration (f697625db563): head after 82e81b7ab23d, the new clauses of
ck_payments_method_valid and ck_payments_source_valid as MySQL enforces them, round trip with the
original clauses back, existing manual payments untouched and a protected downgrade.

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

REVISION = "f697625db563"
PREVIOUS = "82e81b7ab23d"
CHECKS = ("ck_payments_method_valid", "ck_payments_source_valid")
ORIGINAL = {
    "ck_payments_method_valid": "(`method` in (_utf8mb4\\'CASH\\',_utf8mb4\\'CARD\\',_utf8mb4\\'TRANSFER\\'))",
    # MySQL stores IN ('MANUAL') (as written by 6afae8f829c4 and by the downgrade) as an equality.
    "ck_payments_source_valid": "(`source` = _utf8mb4\\'MANUAL\\')",
}
NEW = {
    "ck_payments_method_valid": "(`method` in (_utf8mb4\\'CASH\\',_utf8mb4\\'CARD\\',_utf8mb4\\'TRANSFER\\',"
                                "_utf8mb4\\'DEMO_GATEWAY\\'))",
    "ck_payments_source_valid": "(((`source` = _utf8mb4\\'MANUAL\\') and (`method` in (_utf8mb4\\'CASH\\',"
                                "_utf8mb4\\'CARD\\',_utf8mb4\\'TRANSFER\\'))) or ((`source` = _utf8mb4\\'DEMO_GATEWAY\\') "
                                "and (`method` = _utf8mb4\\'DEMO_GATEWAY\\') and (`registered_by_user_id` = `resident_id`)))",
}
# The payment as a downgrade must leave it (run_sql opens a new connection every time).
PAYMENT_ROWS = (
    "SELECT id, resident_id, registered_by_user_id, amount, method, source, concept, transaction_reference, "
    "payment_date, registered_at FROM payments ORDER BY id"
)
SETUP = (
    "INSERT INTO users (email, full_name, role, status, password_hash) VALUES "
    "('admin@example.com', 'Admin Uno', 'ADMINISTRADOR', 'ACTIVE', 'x'), "
    "('laura@example.com', 'Laura Gómez', 'RESIDENTE', 'ACTIVE', 'x')",
    "INSERT INTO blocks (number) VALUES (1)",
    "INSERT INTO towers (block_id, number) SELECT id, 1 FROM blocks",
    "INSERT INTO units (tower_id, number) SELECT id, 101 FROM towers",
)


def payment(source: str, method: str, registered_by: str) -> str:
    """A payment of Laura, registered by the administrator ('a') or by Laura herself ('r')."""
    return (
        "INSERT INTO payments (resident_id, unit_id, registered_by_user_id, amount, method, concept, source, "
        "payment_date, registered_at, resident_name_snapshot, unit_label_snapshot) "
        f"SELECT r.id, units.id, {registered_by}.id, 50000, '{method}', 'MONTHLY_FEE', '{source}', '2026-10-09', "
        "UTC_TIMESTAMP(6), 'Laura Gómez', 'B1-TO1-APTO-101' FROM users r, users a, units "
        "WHERE r.role = 'RESIDENTE' AND a.role = 'ADMINISTRADOR'"
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


def clauses(url: URL) -> dict[str, str]:
    [found] = run_sql(url, "SELECT CONSTRAINT_NAME, CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
                           "WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME IN "
                           "('ck_payments_method_valid', 'ck_payments_source_valid')")
    return {row[0]: row[1] for row in found}


def current_revision(url: URL) -> str:
    [found] = run_sql(url, "SELECT version_num FROM alembic_version")
    return found[0][0]


def rejected(url: URL, statement: str) -> bool:
    try:
        run_sql(url, statement)
    except DBAPIError as error:
        assert error.orig.args[0] == 3819, error.orig  # type: ignore[union-attr]  # check constraint violated
        return True
    return False


@pytest.fixture
def database(migrated_test_database: URL) -> Iterator[URL]:
    assert migrated_test_database.database is not None and migrated_test_database.database.endswith("_test")
    config = alembic_config(migrated_test_database)
    command.downgrade(config, REVISION)  # later revisions first, if any
    try:
        run_sql(migrated_test_database, *SETUP)
        yield migrated_test_database
    finally:
        run_sql(migrated_test_database, "DELETE FROM payments", "DELETE FROM units", "DELETE FROM towers",
                "DELETE FROM blocks", "DELETE FROM users")
        command.upgrade(config, "head")


def test_revision_is_the_head_after_the_demo_transactions(migrated_test_database: URL) -> None:
    script = ScriptDirectory.from_config(alembic_config(migrated_test_database))
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS
    assert current_revision(migrated_test_database) == REVISION


def test_models_match_the_database(migrated_test_database: URL) -> None:
    # Same as `alembic check`: raises if autogenerate finds any difference.
    command.check(alembic_config(migrated_test_database))


def test_the_new_clauses(migrated_test_database: URL) -> None:
    assert clauses(migrated_test_database) == NEW


def test_what_mysql_accepts_and_rejects(database: URL) -> None:
    # Manual payments: unchanged (three methods, registered by the administrator).
    for method in ("CASH", "CARD", "TRANSFER"):
        assert not rejected(database, payment("MANUAL", method, "a"))
    # A demo gateway payment: DEMO_GATEWAY method, registered by its own resident.
    assert not rejected(database, payment("DEMO_GATEWAY", "DEMO_GATEWAY", "r"))
    # Never mixed, never attributed to someone else, never another value.
    assert rejected(database, payment("MANUAL", "DEMO_GATEWAY", "a"))
    assert rejected(database, payment("DEMO_GATEWAY", "CARD", "r"))
    assert rejected(database, payment("DEMO_GATEWAY", "DEMO_GATEWAY", "a"))
    assert rejected(database, payment("GATEWAY", "CARD", "a"))
    assert rejected(database, payment("MANUAL", "CHECK", "a"))
    assert run_sql(database, "SELECT source, method, COUNT(*) FROM payments GROUP BY 1, 2 ORDER BY 1, 2") == [[
        ("DEMO_GATEWAY", "DEMO_GATEWAY", 1), ("MANUAL", "CARD", 1), ("MANUAL", "CASH", 1), ("MANUAL", "TRANSFER", 1),
    ]]


def test_upgrade_downgrade_upgrade_keeps_manual_payments(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    assert clauses(database) == ORIGINAL
    run_sql(database, payment("MANUAL", "TRANSFER", "a"))
    [before] = run_sql(database, "SELECT id, amount, method, source, registered_by_user_id FROM payments")

    command.upgrade(config, REVISION)
    assert clauses(database) == NEW and current_revision(database) == REVISION
    assert run_sql(database, "SELECT id, amount, method, source, registered_by_user_id FROM payments") == [before]

    command.downgrade(config, PREVIOUS)
    assert clauses(database) == ORIGINAL and current_revision(database) == PREVIOUS
    assert run_sql(database, "SELECT id, amount, method, source, registered_by_user_id FROM payments") == [before]
    command.upgrade(config, REVISION)


def test_downgrade_refuses_while_a_demo_gateway_payment_exists(database: URL) -> None:
    run_sql(database, payment("DEMO_GATEWAY", "DEMO_GATEWAY", "r"))  # committed
    [before] = run_sql(database, PAYMENT_ROWS)
    with pytest.raises(RuntimeError, match="1 demo gateway payments would not fit.*Nothing was changed"):
        command.downgrade(alembic_config(database), PREVIOUS)
    assert clauses(database) == NEW and current_revision(database) == REVISION
    # Read again through a new connection: the demo gateway payment is exactly as it was.
    [after] = run_sql(database, PAYMENT_ROWS)
    assert after == before
    [(_, resident, registered_by, _, method, source, *_)] = after
    assert (method, source) == ("DEMO_GATEWAY", "DEMO_GATEWAY") and registered_by == resident
