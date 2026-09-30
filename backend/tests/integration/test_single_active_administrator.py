"""HU-001 / D3: at most one active administrator, guaranteed by the database.

uq_users_active_administrator_marker is a UNIQUE index on a generated column
that is 1 only for an active, non-deleted administrator (NULL otherwise).
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AdministratorAlreadyExistsError
from app.models import AuditLog, User, UserRole, UserStatus
from app.schemas.user import AdminCreate
from app.services import users as users_service

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
INSERT_USER = text(
    "INSERT INTO users (email, full_name, role, status, password_hash, deleted_at) "
    "VALUES (:email, 'Usuario Prueba', :role, :status, 'not-a-real-hash', :deleted_at)"
)


def admin_data(email: str) -> AdminCreate:
    return AdminCreate.model_validate(
        {"full_name": "Ana María Pérez", "email": email, "password": PASSWORD, "password_confirmation": PASSWORD}
    )


async def insert_user(session: AsyncSession, email: str, role: str, status: str, deleted: bool = False) -> None:
    await session.execute(
        INSERT_USER,
        {"email": email, "role": role, "status": status, "deleted_at": "2026-01-01 00:00:00" if deleted else None},
    )


# --- Invariant enforced by MySQL, bypassing the Python service -----------------


async def test_database_rejects_second_active_administrator(db_session: AsyncSession) -> None:
    await insert_user(db_session, "admin1@example.com", "ADMINISTRADOR", "ACTIVE")

    with pytest.raises(IntegrityError, match="uq_users_active_administrator_marker"):
        await insert_user(db_session, "admin2@example.com", "ADMINISTRADOR", "ACTIVE")


async def test_database_rejects_reactivating_administrator_while_another_is_active(db_session: AsyncSession) -> None:
    await insert_user(db_session, "admin1@example.com", "ADMINISTRADOR", "ACTIVE")
    await insert_user(db_session, "admin2@example.com", "ADMINISTRADOR", "DISABLED")

    with pytest.raises(IntegrityError, match="uq_users_active_administrator_marker"):
        await db_session.execute(text("UPDATE users SET status = 'ACTIVE' WHERE email = 'admin2@example.com'"))


async def test_inactive_deleted_and_resident_rows_do_not_count(db_session: AsyncSession) -> None:
    await insert_user(db_session, "admin@example.com", "ADMINISTRADOR", "ACTIVE")
    await insert_user(db_session, "disabled1@example.com", "ADMINISTRADOR", "DISABLED")
    await insert_user(db_session, "disabled2@example.com", "ADMINISTRADOR", "DISABLED")
    await insert_user(db_session, "deleted@example.com", "ADMINISTRADOR", "ACTIVE", deleted=True)
    await insert_user(db_session, "resident1@example.com", "RESIDENTE", "ACTIVE")
    await insert_user(db_session, "resident2@example.com", "RESIDENTE", "ACTIVE")

    markers = (await db_session.execute(text("SELECT email, active_administrator_marker FROM users ORDER BY email"))).all()
    assert [email for email, marker in markers if marker == 1] == ["admin@example.com"]


# --- Real concurrency: two transactions that both pass the Python check -------


async def test_concurrent_creations_produce_exactly_one_administrator(
    committed_session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_check = users_service.active_administrator_exists
    both_checked = asyncio.Barrier(2)
    check_results: list[bool] = []

    async def check_then_wait(session: AsyncSession) -> bool:
        # Both transactions read "no administrator" before either one inserts:
        # the exact race a Python-only `if` cannot prevent.
        result = await real_check(session)
        check_results.append(result)
        await both_checked.wait()
        return result

    monkeypatch.setattr(users_service, "active_administrator_exists", check_then_wait)

    async def attempt(email: str) -> Any:
        async with committed_session_factory() as session:
            try:
                return await users_service.create_first_administrator(session, admin_data(email), audit_source="cli")
            except AdministratorAlreadyExistsError as exc:
                return exc

    results = await asyncio.gather(attempt("first@example.com"), attempt("second@example.com"))

    assert check_results == [False, False]
    created = [r for r in results if isinstance(r, User)]
    rejected = [r for r in results if isinstance(r, AdministratorAlreadyExistsError)]
    assert len(created) == 1
    assert len(rejected) == 1

    async with committed_session_factory() as session:
        active_admins = await session.scalar(
            select(func.count()).select_from(User).where(
                User.role == UserRole.ADMINISTRADOR, User.status == UserStatus.ACTIVE, User.deleted_at.is_(None)
            )
        )
        audit_entries = await session.scalar(select(func.count()).select_from(AuditLog))
    assert active_admins == 1
    # The losing transaction was rolled back completely, audit entry included.
    assert audit_entries == 1
