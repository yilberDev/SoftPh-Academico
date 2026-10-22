"""HU-015: real concurrency, with independent transactions that really commit.

The resident's row is locked before anything is checked, and the deletion takes the same lock
order as HU-004 payments and HU-010 demo transactions (block S -> tower S -> apartment X ->
residence -> resident): two changes never both apply on the same token, and a deletion and a
payment (manual or demo) of the same apartment run one after the other, without a deadlock. The
balance and the demo transactions checked by the deletion are the ones committed before it.
"""

import asyncio
from collections.abc import Awaitable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utc_now
from app.core.exceptions import (
    AppError,
    ChargeNotFoundError,
    FieldValidationError,
    ResidenceChangedError,
    ResidentDeletionBlockedError,
    ResidentNotFoundError,
    VersionConflictError,
)
from app.core.sessions import generate_session_token, hash_token
from app.models import Block, Charge, ResidentInvitation, ResidentUnit, Tower, Unit, User, UserRole, UserStatus
from app.models.invitation import InvitationStatus
from app.schemas.payment import PaymentCreate
from app.schemas.resident import ResidentUpdate
from app.services import demo_payments as demo_service
from app.services import payments as payment_service
from app.services import residents as service
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]


async def attempt(factory: Factory, operation: Any, *args: Any, **kwargs: Any) -> Any:
    """Runs one service call in its own transaction; expected errors are returned."""
    async with factory() as session:
        try:
            return await operation(session, *args, **kwargs)
        except AppError as exc:
            return exc


async def gather(*coroutines: Awaitable[Any]) -> list[Any]:
    # A deadlock or a lock that is never released fails the test instead of hanging.
    return list(await asyncio.wait_for(asyncio.gather(*coroutines), timeout=30))


async def scalar(factory: Factory, sql: str, **params: Any) -> Any:
    async with factory() as session:
        return await session.scalar(text(sql), params)


@pytest.fixture
async def world(committed_session_factory: Factory) -> dict[str, int]:
    async with committed_session_factory() as session:
        admin = User(email="admin@example.com", full_name="Ana María Pérez", role=UserRole.ADMINISTRADOR,
                     status=UserStatus.ACTIVE, password_hash="not-a-real-hash")
        laura = User(email="laura@example.com", full_name="Laura Gómez", role=UserRole.RESIDENTE,
                     status=UserStatus.ACTIVE, password_hash="not-a-real-hash", document_number="1012345678")
        block = Block(number=1)
        session.add_all([admin, laura, block])
        await session.flush()
        tower = Tower(block_id=block.id, number=2)
        session.add(tower)
        await session.flush()
        unit = Unit(tower_id=tower.id, number=104)
        session.add(unit)
        await session.flush()
        now = utc_now()
        invitation = ResidentInvitation(
            token_hash=hash_token(generate_session_token()), user_id=laura.id, unit_id=unit.id,
            invited_by_user_id=admin.id, validity_days=7, status=InvitationStatus.USED,
            created_at=now - timedelta(days=2), expires_at=now + timedelta(days=5), used_at=now - timedelta(days=1),
        )
        session.add(invitation)
        await session.flush()
        session.add(ResidentUnit(user_id=laura.id, unit_id=unit.id, invitation_id=invitation.id,
                                 started_at=now - timedelta(days=1)))
        await session.commit()
        return {"admin": admin.id, "laura": laura.id, "unit": unit.id}


async def token(factory: Factory, resident_id: int) -> Any:
    async with factory() as session:
        return (await service.get_resident(session, resident_id)).updated_at


def update(updated_at: Any, **changes: Any) -> ResidentUpdate:
    data = {"full_name": "Laura Gómez", "document_number": "1012345678", "phone": None, "updated_at": updated_at}
    return ResidentUpdate.model_validate(data | changes)


async def test_two_updates_with_the_same_token_apply_once(
    committed_session_factory: Factory, world: dict[str, int]
) -> None:
    actor = Actor(user_id=world["admin"], ip_address="127.0.0.1")
    read = await token(committed_session_factory, world["laura"])

    results = await gather(
        attempt(committed_session_factory, service.update_resident, world["laura"],
                update(read, full_name="Laura Gómez Ruiz"), actor),
        attempt(committed_session_factory, service.update_resident, world["laura"],
                update(read, phone="3001234567"), actor),
    )

    conflicts = [result for result in results if isinstance(result, VersionConflictError)]
    applied = [result for result in results if not isinstance(result, AppError)]
    assert len(conflicts) == 1 and len(applied) == 1
    stored = await scalar(committed_session_factory, "SELECT CONCAT(full_name, '|', COALESCE(phone, '-')) FROM users "
                                                     "WHERE id = :id", id=world["laura"])
    assert stored == f"{applied[0].full_name}|{applied[0].phone or '-'}"  # only the one that won
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM audit_logs WHERE action = 'RESIDENT_UPDATED'") == 1


async def test_an_update_and_a_deletion_with_the_same_token(
    committed_session_factory: Factory, world: dict[str, int]
) -> None:
    actor = Actor(user_id=world["admin"], ip_address="127.0.0.1")
    read = await token(committed_session_factory, world["laura"])

    updated, deleted = await gather(
        attempt(committed_session_factory, service.update_resident, world["laura"],
                update(read, full_name="Laura Gómez Ruiz"), actor),
        attempt(committed_session_factory, service.delete_resident, world["laura"], read.replace(tzinfo=None), actor),
    )

    deleted_at = await scalar(committed_session_factory, "SELECT deleted_at FROM users WHERE id = :id", id=world["laura"])
    if deleted is None:  # the deletion won: the update found no registered resident
        assert isinstance(updated, ResidentNotFoundError) and deleted_at is not None
    else:  # the update won: the deletion saw another token
        assert isinstance(deleted, VersionConflictError) and deleted_at is None
        assert updated.full_name == "Laura Gómez Ruiz"
    ended = await scalar(committed_session_factory, "SELECT COUNT(*) FROM resident_units WHERE ended_at IS NOT NULL")
    assert ended == (1 if deleted is None else 0)


async def test_a_deletion_and_a_payment_of_the_same_apartment_run_one_after_the_other(
    committed_session_factory: Factory, world: dict[str, int]
) -> None:
    actor = Actor(user_id=world["admin"], ip_address="127.0.0.1")
    read = await token(committed_session_factory, world["laura"])
    payment = PaymentCreate(resident_id=world["laura"], amount="150000", method="CASH", concept="MONTHLY_FEE",
                            payment_date=date(2026, 10, 5))

    deleted, paid = await gather(
        attempt(committed_session_factory, service.delete_resident, world["laura"], read.replace(tzinfo=None), actor),
        attempt(committed_session_factory, payment_service.register_payment, payment, actor, mailer=FakeMailSender()),
    )

    payments = await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments")
    residences = await scalar(committed_session_factory, "SELECT COUNT(*) FROM resident_units WHERE ended_at IS NULL")
    if deleted is None:  # the deletion came first: the resident can no longer receive payments
        assert isinstance(paid, FieldValidationError | ResidenceChangedError) and payments == 0 and residences == 0
    else:  # the payment came first: with nothing to cover it is credit, so the deletion sees it and is refused
        assert not isinstance(paid, AppError) and payments == 1 and residences == 1
        assert isinstance(deleted, ResidentDeletionBlockedError)
        assert [detail["field"] for detail in deleted.details or []] == ["credit_balance"]


async def test_a_deletion_and_a_demo_payment_of_the_same_apartment_run_one_after_the_other(
    committed_session_factory: Factory, world: dict[str, int]
) -> None:
    async with committed_session_factory() as session:
        charge = Charge(unit_id=world["unit"], concept="PENALTY", description="Ruido", amount=Decimal("50000.00"),
                        due_date=date(2026, 10, 20), created_by_user_id=world["admin"])
        session.add(charge)
        await session.commit()
        charge_id = charge.id
    actor = Actor(user_id=world["admin"], ip_address="127.0.0.1")
    read = await token(committed_session_factory, world["laura"])

    deleted, started = await gather(
        attempt(committed_session_factory, service.delete_resident, world["laura"], read.replace(tzinfo=None), actor),
        attempt(committed_session_factory, demo_service.start_demo_payment, world["laura"], charge_id,
                Decimal("50000.00")),
    )

    # The charge is pending either way, so the deletion is refused; the demo transaction exists only if it
    # started on its own (never half created) and the resident is never deleted with it in progress.
    assert isinstance(deleted, ResidentDeletionBlockedError)
    assert "pending_balance" in [detail["field"] for detail in deleted.details or []]
    live = await scalar(committed_session_factory, "SELECT COUNT(*) FROM demo_payment_transactions "
                                                   "WHERE status = 'INITIATED'")
    assert live == (0 if isinstance(started, ChargeNotFoundError) else 1)
    assert await scalar(committed_session_factory, "SELECT deleted_at FROM users WHERE id = :id",
                        id=world["laura"]) is None
