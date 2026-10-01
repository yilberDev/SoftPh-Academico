"""Integration test infrastructure.

Tests run against a dedicated MySQL database (TEST_DB_NAME, must end in
"_test"), never the development one. The schema is created with Alembic.
Each test gets a session inside a transaction that is rolled back afterwards;
service commits only release a savepoint inside that transaction.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
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
from app.core.passwords import hash_password
from app.db.session import create_engine, get_db
from app.main import app
from app.models import User, UserRole, UserStatus

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
            for table in ("sessions", "login_throttles", "audit_logs", "users"):
                await connection.execute(text(f"DELETE FROM {table}"))
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


PASSWORD = "correct horse battery"


@pytest.fixture
def origin_headers() -> dict[str, str]:
    """Headers of a request coming from the single public origin."""
    return {"Origin": get_settings().app_origin}


@pytest.fixture
def make_user(db_session: AsyncSession) -> Callable[..., Awaitable[User]]:
    """Inserts a user (active administrator by default) in the test transaction."""

    async def factory(
        email: str = "admin@example.com",
        *,
        role: UserRole = UserRole.ADMINISTRADOR,
        status: UserStatus = UserStatus.ACTIVE,
        password: str | None = PASSWORD,
        password_hash: str | None = None,
        deleted: bool = False,
    ) -> User:
        user = User(
            email=email,
            full_name="Ana María Pérez",
            role=role,
            status=status,
            password_hash=password_hash or (hash_password(password) if password else None),
        )
        db_session.add(user)
        await db_session.flush()
        if deleted:
            await db_session.execute(text("UPDATE users SET deleted_at = UTC_TIMESTAMP(6) WHERE id = :id"), {"id": user.id})
            await db_session.refresh(user)
        return user

    return factory
