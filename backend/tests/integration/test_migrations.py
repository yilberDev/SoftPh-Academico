import asyncio

from alembic import command
from sqlalchemy import inspect, pool, text
from sqlalchemy.engine import URL

from app.db.session import create_engine
from tests.integration.conftest import alembic_config

# HU-001: users, audit_logs. HU-002: sessions, login_throttles. HU-013: blocks, towers, units.
# HU-003: resident_invitations, resident_units. HU-016: password_reset_tokens.
# HU-004: charges, payments, payment_allocations.
APP_TABLES = {
    "users", "audit_logs", "sessions", "login_throttles", "blocks", "towers", "units",
    "resident_invitations", "resident_units", "password_reset_tokens",
    "charges", "payments", "payment_allocations",
}


def table_names(url: URL) -> set[str]:
    async def load() -> set[str]:
        engine = create_engine(url, poolclass=pool.NullPool)
        async with engine.connect() as connection:
            names = await connection.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
        await engine.dispose()
        return names

    return asyncio.run(load())


def email_collation(url: URL) -> str:
    async def load() -> str:
        engine = create_engine(url, poolclass=pool.NullPool)
        async with engine.connect() as connection:
            value = await connection.scalar(
                text(
                    "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'email'"
                )
            )
        await engine.dispose()
        return value

    return asyncio.run(load())


def test_downgrade_and_upgrade_round_trip(migrated_test_database: URL) -> None:
    # Only ever runs on the dedicated *_test database.
    assert migrated_test_database.database is not None
    assert migrated_test_database.database.endswith("_test")
    config = alembic_config(migrated_test_database)

    command.downgrade(config, "base")
    assert table_names(migrated_test_database) & APP_TABLES == set()

    command.upgrade(config, "head")
    assert APP_TABLES <= table_names(migrated_test_database)
    assert email_collation(migrated_test_database) == "utf8mb4_0900_bin"
