from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AdministratorAlreadyExistsError,
    DocumentAlreadyRegisteredError,
    EmailAlreadyRegisteredError,
)
from app.core.passwords import hash_password
from app.models import AuditAction, User, UserRole, UserStatus
from app.schemas.user import AdminCreate
from app.services.audit import record_audit


async def active_administrator_exists(session: AsyncSession) -> bool:
    query = select(
        exists().where(
            User.role == UserRole.ADMINISTRADOR,
            User.status == UserStatus.ACTIVE,
            User.deleted_at.is_(None),
        )
    )
    return bool(await session.scalar(query))


async def create_first_administrator(session: AsyncSession, data: AdminCreate, *, audit_source: str) -> User:
    """Creates the first administrator (HU-001). Rejected if an active one exists.

    The checks below only give clear messages. The guarantees come from the
    database UNIQUE constraints: uq_users_active_administrator_marker (at most
    one active administrator, even under concurrency), uq_users_email and
    uq_users_document_number (including soft-deleted users).
    """
    if await active_administrator_exists(session):
        raise AdministratorAlreadyExistsError()
    if await session.scalar(select(exists().where(User.email == data.email))):
        raise EmailAlreadyRegisteredError()
    if data.document_number and await session.scalar(
        select(exists().where(User.document_number == data.document_number))
    ):
        raise DocumentAlreadyRegisteredError()

    user = User(
        email=data.email,
        password_hash=hash_password(data.password.get_secret_value()),
        full_name=data.full_name,
        document_number=data.document_number,
        phone=data.phone,
        role=UserRole.ADMINISTRADOR,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    try:
        await session.flush()
        record_audit(
            session,
            action=AuditAction.ADMIN_CREATED,
            entity_type="user",
            entity_id=user.id,
            context={"source": audit_source},
        )
        await session.commit()
    except IntegrityError as exc:
        # A concurrent insert won the race for a UNIQUE value.
        await session.rollback()
        if "uq_users_active_administrator_marker" in str(exc.orig):
            raise AdministratorAlreadyExistsError() from exc
        if "uq_users_email" in str(exc.orig):
            raise EmailAlreadyRegisteredError() from exc
        if "uq_users_document_number" in str(exc.orig):
            raise DocumentAlreadyRegisteredError() from exc
        raise
    await session.refresh(user)
    return user
