"""HU-017 migration (d33ea6140186, the head): place in the chain after HU-010 (41602195f69f), round trip
(the table and every constraint, index and foreign key gone, then back), protected downgrade and
upgrade, no drift (alembic check), the rules MySQL enforces itself, existing data untouched.

Only ever runs on the dedicated *_test database, and always leaves it at head.
"""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from tests.integration.conftest import alembic_config
from tests.integration.test_demo_payment_transactions_migration import (
    FINANCIAL,
    FINANCIAL_ROWS,
    current_revision,
    run_sql,
)
from tests.integration.test_payments_database import CHECK_VIOLATED, DUPLICATE, FK_PARENT_MISSING, FK_ROW_REFERENCED

REVISION = "d33ea6140186"
PREVIOUS = "41602195f69f"
TABLE = "notifications"
CONSTRAINTS = {
    ("PRIMARY", "PRIMARY KEY"),
    ("uq_notifications_event_key", "UNIQUE"),
    ("fk_notifications_resident_id_users", "FOREIGN KEY"),
    ("ck_notifications_event_type_valid", "CHECK"),
    ("ck_notifications_email_status_valid", "CHECK"),
    ("ck_notifications_sent_has_timestamp", "CHECK"),
    ("ck_notifications_error_only_when_failed", "CHECK"),
    ("ck_notifications_title_not_blank", "CHECK"),
}
INDEXES = {"PRIMARY", "uq_notifications_event_key", "ix_notifications_resident_id_created_at"}


def notification(key: str, email: str = "laura@example.com", **columns: str) -> str:
    """A valid PENDING notification of the user with that email (FINANCIAL's resident), with the given SQL values."""
    values = {"event_type": "'PAYMENT_REGISTERED'", "title": "'Pago registrado'", "email_status": "'PENDING'",
              "email_error": "NULL", "sent_at": "NULL"} | columns
    return (
        "INSERT INTO notifications (resident_id, event_type, entity_type, entity_id, event_key, title, message, "
        f"email_status, email_error, sent_at) SELECT id, {values['event_type']}, 'payment', 1, '{key}', "
        f"{values['title']}, 'Mensaje', {values['email_status']}, {values['email_error']}, {values['sent_at']} "
        f"FROM users WHERE email = '{email}'"
    )


def table_exists(url: URL) -> bool:
    [found] = run_sql(url, "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                           f"AND TABLE_NAME = '{TABLE}'")
    return bool(found[0][0])


def constraints(url: URL) -> set[tuple[str, str]]:
    [found] = run_sql(url, "SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE FROM information_schema.TABLE_CONSTRAINTS "
                           f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{TABLE}'")
    return {(row[0], row[1]) for row in found}


def indexes(url: URL) -> set[str]:
    [found] = run_sql(url, "SELECT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
                           f"AND TABLE_NAME = '{TABLE}'")
    return {row[0] for row in found}


def error_code(url: URL, *statements: str) -> int:
    with pytest.raises(DBAPIError) as error:
        run_sql(url, *statements)
    return error.value.orig.args[0]  # type: ignore[union-attr]


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
            run_sql(migrated_test_database, f"DROP TABLE {TABLE}")  # leftover of a failed test: empty
        command.upgrade(config, "head")


def test_revision_is_the_head_after_hu010(migrated_test_database: URL) -> None:
    script = ScriptDirectory.from_config(alembic_config(migrated_test_database))
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS
    assert current_revision(migrated_test_database) == REVISION


def test_models_match_the_database(migrated_test_database: URL) -> None:
    # Same as `alembic check`: raises if autogenerate finds any difference.
    command.check(alembic_config(migrated_test_database))


def test_upgrade_downgrade_upgrade(database: URL) -> None:
    config = alembic_config(database)
    assert constraints(database) == CONSTRAINTS and indexes(database) == INDEXES

    command.downgrade(config, PREVIOUS)
    assert not table_exists(database) and constraints(database) == set() and indexes(database) == set()
    [checks] = run_sql(database, "SELECT CONSTRAINT_NAME FROM information_schema.CHECK_CONSTRAINTS "
                                 "WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME LIKE 'ck_notifications_%'")
    assert checks == [] and current_revision(database) == PREVIOUS

    command.upgrade(config, REVISION)
    assert constraints(database) == CONSTRAINTS and current_revision(database) == REVISION


def test_existing_data_is_untouched_and_nothing_is_invented(database: URL) -> None:
    config = alembic_config(database)
    command.downgrade(config, PREVIOUS)
    run_sql(database, *FINANCIAL)
    [before] = run_sql(database, FINANCIAL_ROWS)

    command.upgrade(config, REVISION)
    assert run_sql(database, FINANCIAL_ROWS) == [before]
    assert run_sql(database, f"SELECT COUNT(*) FROM {TABLE}") == [[(0,)]]  # no notification for past events


def test_downgrade_refuses_to_lose_notifications(database: URL) -> None:
    run_sql(database, *FINANCIAL, notification("payment:1:registered"))
    with pytest.raises(RuntimeError, match=f"1 rows in {TABLE} would be lost.*Nothing was changed"):
        command.downgrade(alembic_config(database), PREVIOUS)
    assert table_exists(database) and current_revision(database) == REVISION


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


def test_mysql_enforces_the_rules(database: URL) -> None:
    run_sql(database, *FINANCIAL, notification("payment:1:registered"))
    # One notification per event (also when retried).
    assert error_code(database, notification("payment:1:registered")) == DUPLICATE
    # Known events and delivery results only; a title; SENT exactly with its time; the error only when FAILED.
    for columns in ({"event_type": "'OTHER'"}, {"email_status": "'DELIVERED'"}, {"title": "'   '"},
                    {"email_status": "'SENT'"}, {"sent_at": "UTC_TIMESTAMP(6)"},
                    {"email_status": "'NO_CONTACT'", "email_error": "'NoContact'"}):
        assert error_code(database, notification("payment:2:registered", **columns)) == CHECK_VIOLATED, columns
    run_sql(database,
            notification("payment:3:registered", email_status="'SENT'", sent_at="UTC_TIMESTAMP(6)"),
            notification("payment:4:registered", email_status="'FAILED'", email_error="'SMTPException'"),
            notification("payment:5:registered", email_status="'NO_CONTACT'"),
            notification("payment:7:registered", email_status="'NOT_REQUIRED'"))  # a demo payment (HU-010)
    # Only an existing user; a user with notifications (and nothing else) is never deleted with them.
    assert error_code(database, notification("payment:6:registered").replace("SELECT id,", "SELECT 999999,")
                      ) == FK_PARENT_MISSING
    run_sql(database, "INSERT INTO users (email, full_name, role, status, password_hash) "
                      "VALUES ('pedro@example.com', 'Pedro Ruiz', 'RESIDENTE', 'ACTIVE', 'x')",
            notification("feedback:1:responded:v2", "pedro@example.com", event_type="'FEEDBACK_RESPONDED'"))
    assert error_code(database, "DELETE FROM users WHERE email = 'pedro@example.com'") == FK_ROW_REFERENCED
    assert run_sql(database, f"SELECT COUNT(*) FROM {TABLE}") == [[(6,)]]
