"""Integration test infrastructure.

Tests run against a dedicated MySQL database (TEST_DB_NAME, must end in
"_test"), never the development one. The schema is created with Alembic.
Each test gets a session inside a transaction that is rolled back afterwards;
service commits only release a savepoint inside that transaction.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import create_engine, get_db
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parents[2]


def alembic_config(database_url: URL) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture(scope="session")
def test_database_url() -> URL:
    # Raises if TEST_DB_NAME does not end in "_test" or equals DB_NAME.
    return get_settings().test_database_url()


@pytest.fixture(scope="session")
def migrated_test_database(test_database_url: URL) -> URL:
    command.upgrade(alembic_config(test_database_url), "head")
    return test_database_url


@pytest.fixture
async def db_session(migrated_test_database: URL) -> AsyncIterator[AsyncSession]:
    # NullPool: connections are bound to the event loop of each test.
    engine = create_engine(migrated_test_database, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def committed_session_factory(migrated_test_database: URL) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Independent sessions whose commits are real (e.g. concurrency tests).

    Unlike db_session, nothing is rolled back automatically: the tables are
    emptied afterwards. Only ever used on the dedicated *_test database.
    """
    assert migrated_test_database.database is not None
    assert migrated_test_database.database.endswith("_test")
    engine = create_engine(migrated_test_database, poolclass=pool.NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM audit_logs"))
            await connection.execute(text("DELETE FROM users"))
        await engine.dispose()


@pytest.fixture
def session_factory(db_session: AsyncSession) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    """Session factory for code that opens its own session (e.g. the CLI)."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield db_session

    return factory


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http_client:
        yield http_client
    app.dependency_overrides.clear()
