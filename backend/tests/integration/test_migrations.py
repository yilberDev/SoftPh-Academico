import asyncio

from alembic import command
from sqlalchemy import inspect, pool
from sqlalchemy.engine import URL

from app.db.session import create_engine
from tests.integration.conftest import alembic_config

HU001_TABLES = {"users", "audit_logs"}


def table_names(url: URL) -> set[str]:
    async def load() -> set[str]:
        engine = create_engine(url, poolclass=pool.NullPool)
        async with engine.connect() as connection:
            names = await connection.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
        await engine.dispose()
        return names

    return asyncio.run(load())


def test_downgrade_and_upgrade_round_trip(migrated_test_database: URL) -> None:
    # Only ever runs on the dedicated *_test database.
    assert migrated_test_database.database is not None
    assert migrated_test_database.database.endswith("_test")
    config = alembic_config(migrated_test_database)

    command.downgrade(config, "base")
    assert table_names(migrated_test_database) & HU001_TABLES == set()

    command.upgrade(config, "head")
    assert HU001_TABLES <= table_names(migrated_test_database)
