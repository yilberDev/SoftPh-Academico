"""Integration test infrastructure.

Tests run against a dedicated MySQL database (TEST_DB_NAME, must end in
"_test"), never the development one. The schema is created with Alembic.
Each test gets a session inside a transaction that is rolled back afterwards;
service commits only release a savepoint inside that transaction.
"""

import asyncio
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

from app.core.clock import utc_now
from app.core.config import Settings, get_settings
from app.core.passwords import hash_password
from app.core.sessions import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, derive_csrf_token
from app.db.session import create_engine, get_db, get_session_factory
from app.main import app, include_demo_payments
from app.models import User, UserRole, UserStatus
from app.services.mail import OutgoingEmail, get_mail_sender
from app.services.sessions import create_session

BACKEND_DIR = Path(__file__).resolve().parents[2]

# HU-010: the demo payment gateway is off by default (DEMO_PAYMENTS_ENABLED, never in production).
# The integration tests publish its routes explicitly on the application under test, with test
# Settings built (and validated) from the current ones plus the flag; .env is not changed. A
# production configuration fails here. Its absence when disabled is tested with create_app().
DEMO_TEST_SETTINGS = Settings(**{**get_settings().model_dump(), "demo_payments_enabled": True})
include_demo_payments(app, DEMO_TEST_SETTINGS)


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
            # HU-010 demo transactions first: they reference users, payments and any charge (late fees too).
            await connection.execute(text("DELETE FROM demo_payment_transactions"))
            # Late fees before their origins (self-referencing foreign key of charges).
            await connection.execute(text("DELETE FROM charges WHERE source_charge_id IS NOT NULL"))
            # Children before parents (foreign keys).
            for table in (
                "feedback", "payment_allocations", "payments", "charges",
                "password_reset_tokens", "resident_units", "resident_invitations", "units", "towers", "blocks",
                "sessions", "login_throttles", "audit_logs", "users",
            ):
                await connection.execute(text(f"DELETE FROM {table}"))
        await engine.dispose()


@pytest.fixture
def session_factory(db_session: AsyncSession) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    """Session factory for code that opens its own session (e.g. the CLI)."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield db_session

    return factory


class FakeMailSender:
    """Captures emails instead of sending them. Tests can make it fail or wait."""

    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []
        self.fail_with: Exception | None = None
        self.gate: asyncio.Event | None = None

    async def send(self, email: OutgoingEmail) -> None:
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(email)


@pytest.fixture
def mailer() -> FakeMailSender:
    return FakeMailSender()


@pytest.fixture
async def client(
    db_session: AsyncSession,
    mailer: FakeMailSender,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
) -> AsyncIterator[AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    # Background work (e.g. HU-016 email delivery) uses the same test transaction.
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    # API tests never reach a real SMTP server (not even Mailpit).
    app.dependency_overrides[get_mail_sender] = lambda: mailer
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


async def sign_in(client: AsyncClient, db: AsyncSession, user: User) -> dict[str, str]:
    """Creates a session for the user directly (no Argon2) and returns the
    headers of a state-changing request from the public origin.

    The setup is committed so that a service rollback (which only rolls back
    the current savepoint) cannot undo it.
    """
    token = await create_session(db, user, get_settings(), utc_now())
    await db.commit()
    client.cookies.set(SESSION_COOKIE_NAME, token)
    return {"Origin": get_settings().app_origin, CSRF_HEADER_NAME: derive_csrf_token(token)}


@pytest.fixture
async def admin_headers(
    client: AsyncClient, db_session: AsyncSession, make_user: Callable[..., Awaitable[User]]
) -> dict[str, str]:
    """Signed-in ADMINISTRADOR: Origin + CSRF headers for state-changing requests."""
    admin = await make_user(password_hash="not-a-real-hash")
    return await sign_in(client, db_session, admin)
