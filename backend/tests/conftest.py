"""Test infrastructure.

Tests run against a dedicated MySQL database (TEST_DB_NAME, must end in
"_test"), never the development one. The schema is created with Alembic.
Each test gets a session inside a transaction that is rolled back afterwards.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import create_engine, get_db
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def test_database_url() -> URL:
    # Raises if TEST_DB_NAME does not end in "_test" or equals DB_NAME.
    return get_settings().test_database_url()


@pytest.fixture(scope="session", autouse=True)
def migrated_test_database(test_database_url: URL) -> Iterator[None]:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = test_database_url
    command.upgrade(config, "head")
    yield


@pytest.fixture
async def db_session(test_database_url: URL) -> AsyncIterator[AsyncSession]:
    # NullPool: connections are bound to the event loop of each test.
    engine = create_engine(test_database_url, poolclass=pool.NullPool)
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
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http_client:
        yield http_client
    app.dependency_overrides.clear()
