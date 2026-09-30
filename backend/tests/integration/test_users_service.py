from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AdministratorAlreadyExistsError,
    DocumentAlreadyRegisteredError,
    EmailAlreadyRegisteredError,
)
from app.core.passwords import verify_password
from app.models import AuditAction, AuditLog, User, UserRole, UserStatus
from app.schemas.user import AdminCreate
from app.services.users import active_administrator_exists, create_first_administrator

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"


def admin_data(**overrides: Any) -> AdminCreate:
    return AdminCreate.model_validate(
        {
            "full_name": "Ana María Pérez",
            "email": "admin@example.com",
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            "document_number": "1234567",
            "phone": "3001234567",
            **overrides,
        }
    )


async def insert_user(session: AsyncSession, **fields: Any) -> User:
    values: dict[str, Any] = {
        "email": "existing@example.com",
        "full_name": "Usuario Existente",
        "role": UserRole.RESIDENTE,
        "status": UserStatus.PENDING_ACTIVATION,
        **fields,
    }
    user = User(**values)
    session.add(user)
    await session.flush()
    return user


async def test_creates_active_administrator_with_argon2id_hash(db_session: AsyncSession) -> None:
    user = await create_first_administrator(db_session, admin_data(), audit_source="cli")

    stored = await db_session.scalar(select(User).where(User.id == user.id))
    assert stored is not None
    assert stored.role == UserRole.ADMINISTRADOR
    assert stored.status == UserStatus.ACTIVE
    assert stored.email == "admin@example.com"
    assert stored.document_number == "1234567"
    assert stored.phone == "3001234567"
    assert stored.deleted_at is None
    assert stored.created_at is not None
    assert stored.password_hash is not None
    assert stored.password_hash.startswith("$argon2id$")
    assert verify_password(stored.password_hash, PASSWORD)


async def test_optional_fields_can_be_omitted(db_session: AsyncSession) -> None:
    user = await create_first_administrator(
        db_session, admin_data(document_number=None, phone=None), audit_source="cli"
    )

    assert user.document_number is None
    assert user.phone is None


async def test_records_audit_entry_without_secrets(db_session: AsyncSession) -> None:
    user = await create_first_administrator(db_session, admin_data(), audit_source="cli")

    entries = (await db_session.scalars(select(AuditLog).where(AuditLog.entity_id == user.id))).all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == AuditAction.ADMIN_CREATED
    assert entry.entity_type == "user"
    assert entry.actor_user_id is None
    assert entry.context == {"source": "cli"}
    assert PASSWORD not in str(entry.context)


async def test_rejects_when_active_administrator_exists(db_session: AsyncSession) -> None:
    await create_first_administrator(db_session, admin_data(), audit_source="cli")

    with pytest.raises(AdministratorAlreadyExistsError):
        await create_first_administrator(
            db_session, admin_data(email="second@example.com", document_number=None), audit_source="cli"
        )


async def test_disabled_administrator_does_not_block_creation(db_session: AsyncSession) -> None:
    await insert_user(
        db_session,
        email="old-admin@example.com",
        role=UserRole.ADMINISTRADOR,
        status=UserStatus.DISABLED,
    )

    assert await active_administrator_exists(db_session) is False
    user = await create_first_administrator(db_session, admin_data(), audit_source="cli")
    assert user.status == UserStatus.ACTIVE


async def test_rejects_duplicate_email_case_insensitively(db_session: AsyncSession) -> None:
    await insert_user(db_session, email="admin@example.com")

    with pytest.raises(EmailAlreadyRegisteredError):
        await create_first_administrator(db_session, admin_data(email="ADMIN@Example.com"), audit_source="cli")


async def test_rejects_email_of_soft_deleted_user(db_session: AsyncSession) -> None:
    await insert_user(db_session, email="admin@example.com", deleted_at=text("CURRENT_TIMESTAMP(6)"))

    with pytest.raises(EmailAlreadyRegisteredError):
        await create_first_administrator(db_session, admin_data(), audit_source="cli")


async def test_rejects_duplicate_document_number(db_session: AsyncSession) -> None:
    await insert_user(db_session, document_number="1234567")

    with pytest.raises(DocumentAlreadyRegisteredError):
        await create_first_administrator(db_session, admin_data(), audit_source="cli")


async def test_rejected_creation_leaves_no_rows(db_session: AsyncSession) -> None:
    await insert_user(db_session, email="admin@example.com")

    with pytest.raises(EmailAlreadyRegisteredError):
        await create_first_administrator(db_session, admin_data(), audit_source="cli")

    admins = await db_session.scalar(select(User.id).where(User.role == UserRole.ADMINISTRADOR))
    audits = await db_session.scalar(select(AuditLog.id))
    assert admins is None
    assert audits is None


async def test_timestamps_are_stored_in_utc(db_session: AsyncSession) -> None:
    user = await create_first_administrator(db_session, admin_data(), audit_source="cli")

    offset = await db_session.scalar(text("SELECT TIMESTAMPDIFF(SECOND, created_at, UTC_TIMESTAMP(6)) FROM users WHERE id = :id"), {"id": user.id})
    assert offset is not None
    assert abs(offset) < 60


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO users (email, full_name, role, status) VALUES ('x@example.com', 'X Y Z', 'SUPERUSER', 'DISABLED')",
        "INSERT INTO users (email, full_name, role, status) VALUES ('x@example.com', 'X Y Z', 'RESIDENTE', 'UNKNOWN')",
        "INSERT INTO users (email, full_name, role, status) VALUES ('x@example.com', 'X Y Z', 'ADMINISTRADOR', 'ACTIVE')",
    ],
    ids=["invalid-role", "invalid-status", "active-without-password"],
)
async def test_database_check_constraints(db_session: AsyncSession, statement: str) -> None:
    with pytest.raises(DBAPIError, match="3819"):
        await db_session.execute(text(statement))
