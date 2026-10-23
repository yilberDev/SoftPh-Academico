"""HU-011 / HU-014: the lines and the totals of a statement come from one consistent snapshot.

Real transactions that commit (the *_test database). While a statement is being built, a payment of
the same apartment is registered and committed by HU-004 in another session, between two of the
statement's reads. The statement must not mix the two moments: neither the payment nor its effect
on the balances appears, and its lines still add up to its totals. The next statement shows it.
"""

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import bogota_today, utc_now
from app.core.sessions import generate_session_token, hash_token
from app.models import Block, Charge, ResidentInvitation, ResidentUnit, Tower, Unit, User, UserRole, UserStatus
from app.models.invitation import InvitationStatus
from app.schemas.payment import AccountBalance, PaymentCreate
from app.services import account_statement as service
from app.services import payments as payment_service
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]


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
        session.add(Charge(unit_id=unit.id, concept="PENALTY", description="Ruido", amount=Decimal("100000.00"),
                           due_date=bogota_today() + timedelta(days=10), created_by_user_id=admin.id))
        await session.commit()
        return {"admin": admin.id, "laura": laura.id, "unit": unit.id}


@pytest.fixture
def concurrent_payment(
    committed_session_factory: Factory, world: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[Any]]:
    """Registers (and commits) a payment in another session right before the statement reads the balance."""
    registered: list[Any] = []
    real_unit_balance = service.unit_balance

    async def unit_balance_after_a_payment(db: AsyncSession, unit_id: int) -> AccountBalance:
        if not registered:
            async with committed_session_factory() as other:
                registered.append(await payment_service.register_payment(
                    other,
                    PaymentCreate(resident_id=world["laura"], amount="60000", method="CASH", concept="PENALTY",
                                  payment_date=date(2026, 10, 5)),
                    Actor(user_id=world["admin"], ip_address="127.0.0.1"), mailer=FakeMailSender()))
        return await real_unit_balance(db, unit_id)

    monkeypatch.setattr(service, "unit_balance", unit_balance_after_a_payment)
    yield registered


@pytest.mark.parametrize("all_payers", [True, False], ids=["administrator", "resident"])
async def test_lines_and_totals_never_mix_two_moments(
    committed_session_factory: Factory, world: dict[str, int], concurrent_payment: list[Any], all_payers: bool
) -> None:
    async with committed_session_factory() as session:
        statement = await service.build_account_statement(session, world["laura"], all_payers=all_payers)

    assert len(concurrent_payment) == 1  # the payment really committed in the middle of the statement
    # The statement is the moment before it, entirely: lines and totals agree with each other.
    assert [(o.amount, o.applied_amount, o.outstanding_amount) for o in statement.obligations] == [
        (Decimal("100000.00"), Decimal("0.00"), Decimal("100000.00"))]
    assert statement.payments == () and statement.allocations == ()
    assert (statement.summary.pending_balance, statement.summary.credit_balance) == (Decimal("100000.00"),
                                                                                    Decimal("0.00"))

    async with committed_session_factory() as session:
        after = await service.build_account_statement(session, world["laura"], all_payers=all_payers)

    # The next statement shows the payment and its allocation, again consistently.
    assert [(o.applied_amount, o.outstanding_amount) for o in after.obligations] == [
        (Decimal("60000.00"), Decimal("40000.00"))]
    assert [p.id for p in after.payments] == [concurrent_payment[0].id]
    assert after.summary.pending_balance == Decimal("40000.00")
