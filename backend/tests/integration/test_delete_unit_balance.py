"""Obligations extension (D10): an apartment can be deleted (soft delete) only with
pending balance and credit (saldo a favor) at zero. Its financial history stays.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnitHasBalanceError, UnitInUseError, VersionConflictError
from app.models import AuditAction, AuditLog, Charge, Payment, PaymentAllocation, Unit, User, UserRole
from app.services import structure as structure_service
from app.services.charges import ChargeListQuery, get_charge, list_charges
from app.services.structure import Actor
from tests.integration.test_residents_api import Community
from tests.integration.test_structure_api import Structure, error

pytestmark = pytest.mark.anyio

TODAY = date(2026, 10, 15)
MESSAGE = ("No se puede eliminar B1-TO1-APTO-101 porque tiene obligaciones pendientes o saldo a favor. "
           "Su saldo debe quedar en cero.")


@pytest.fixture
async def admin(make_user: Any) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
async def resident(make_user: Any) -> User:
    # Owner of the payments of the scenarios (a former resident: no current residence).
    return await make_user("residente@example.com", role=UserRole.RESIDENTE, password_hash="x")


@pytest.fixture
async def community(db_session: AsyncSession, admin: User) -> Community:
    return Community(db_session, admin.id)


@pytest.fixture
async def unit(community: Community) -> int:
    return await community.unit(1, 1, 101)


class Finance:
    def __init__(self, db: AsyncSession, admin_id: int, resident_id: int) -> None:
        self.db, self.admin_id, self.resident_id = db, admin_id, resident_id

    async def charge(self, unit_id: int, amount: str, due: date = date(2026, 9, 5)) -> int:
        row = Charge(unit_id=unit_id, concept="OTHER", description="Cargo", amount=Decimal(amount), due_date=due,
                     created_by_user_id=self.admin_id)
        self.db.add(row)
        await self.db.flush()
        return row.id

    async def payment(self, unit_id: int, amount: str) -> int:
        row = Payment(resident_id=self.resident_id, unit_id=unit_id, registered_by_user_id=self.admin_id,
                      amount=Decimal(amount), method="CASH", concept="OTHER", concept_description="Pago",
                      payment_date=date(2026, 9, 10), registered_at=datetime(2026, 9, 10, 15),
                      resident_name_snapshot="Laura Gómez", unit_label_snapshot="B1-TO1-APTO-101")
        self.db.add(row)
        await self.db.flush()
        return row.id

    async def allocate(self, payment_id: int, charge_id: int, amount: str) -> None:
        self.db.add(PaymentAllocation(payment_id=payment_id, charge_id=charge_id, amount=Decimal(amount)))
        await self.db.flush()


@pytest.fixture
def finance(db_session: AsyncSession, admin: User, resident: User) -> Finance:
    return Finance(db_session, admin.id, resident.id)


async def delete(db: AsyncSession, unit_id: int, admin: User) -> None:
    version = await db.scalar(select(Unit.version).where(Unit.id == unit_id))
    await structure_service.delete_unit(db, unit_id, int(version or 0), Actor(admin.id, "203.0.113.7"))


async def snapshot(db: AsyncSession, unit_id: int) -> tuple[Any, ...]:
    """Everything a rejected deletion must leave untouched."""
    unit = (await db.execute(select(Unit.deleted_at, Unit.version).where(Unit.id == unit_id))).one()
    counts = [await db.scalar(select(func.count()).select_from(model)) for model in (Charge, Payment, PaymentAllocation)]
    amounts = (await db.execute(select(func.sum(Charge.amount), func.sum(PaymentAllocation.amount))
                                .select_from(Charge).outerjoin(PaymentAllocation, PaymentAllocation.charge_id == Charge.id)
                                )).one()
    deleted_audits = await db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == AuditAction.UNIT_DELETED))
    return (tuple(unit), *counts, tuple(amounts), deleted_audits)


async def rejected(db: AsyncSession, unit_id: int, admin: User) -> UnitHasBalanceError:
    before = await snapshot(db, unit_id)
    with pytest.raises(UnitHasBalanceError) as raised:
        await delete(db, unit_id, admin)
    assert await snapshot(db, unit_id) == before  # no deletion, no financial write, no audit
    return raised.value


# --- Allowed ---------------------------------------------------------------------------------------------


async def test_without_history(db_session: AsyncSession, unit: int, admin: User) -> None:  # 1
    await delete(db_session, unit, admin)
    assert await db_session.scalar(select(Unit.deleted_at).where(Unit.id == unit)) is not None
    [audit] = (await db_session.scalars(select(AuditLog).where(AuditLog.action == AuditAction.UNIT_DELETED))).all()
    assert audit.entity_id == unit


async def test_with_settled_history(db_session: AsyncSession, unit: int, admin: User, finance: Finance) -> None:  # 5, 6, 7
    first = await finance.charge(unit, "100000", date(2026, 8, 5))
    second = await finance.charge(unit, "50000")
    one = await finance.payment(unit, "120000")
    two = await finance.payment(unit, "30000")
    await finance.allocate(one, first, "100000")
    await finance.allocate(one, second, "20000")
    await finance.allocate(two, second, "30000")

    await delete(db_session, unit, admin)

    assert await db_session.scalar(select(Unit.deleted_at).where(Unit.id == unit)) is not None
    # The history stays: charges, payments and allocations untouched, still readable.
    assert [await db_session.scalar(select(func.count()).select_from(m)) for m in (Charge, Payment, PaymentAllocation)] == [2, 2, 3]
    page = await list_charges(db_session, ChargeListQuery(unit_id=unit), TODAY)
    assert [(i.status, i.unit.deleted) for i in page.items] == [("PAID", True), ("PAID", True)]
    detail = await get_charge(db_session, second, TODAY)
    assert [(a.payment_id, a.applied_amount) for a in detail.allocations] == [(one, Decimal("20000.00")), (two, Decimal("30000.00"))]


async def test_after_the_residence_ended_with_zero_balance(
    db_session: AsyncSession, community: Community, unit: int, admin: User, resident: User, finance: Finance
) -> None:  # 9
    await community.residence(resident.id, unit, ended=True)
    charge = await finance.charge(unit, "100000")
    await finance.allocate(await finance.payment(unit, "100000"), charge, "100000")
    await delete(db_session, unit, admin)
    assert await db_session.scalar(select(Unit.deleted_at).where(Unit.id == unit)) is not None


# --- Rejected --------------------------------------------------------------------------------------------


async def test_with_a_pending_charge(db_session: AsyncSession, unit: int, admin: User, finance: Finance) -> None:  # 2, 8
    await finance.charge(unit, "100000")
    exc = await rejected(db_session, unit, admin)
    assert (exc.status_code, exc.code, exc.message) == (409, "UNIT_HAS_BALANCE", MESSAGE)


async def test_with_a_partially_paid_charge(db_session: AsyncSession, unit: int, admin: User, finance: Finance) -> None:  # 3
    charge = await finance.charge(unit, "100000")
    await finance.allocate(await finance.payment(unit, "40000"), charge, "40000")
    assert (await rejected(db_session, unit, admin)).code == "UNIT_HAS_BALANCE"


async def test_with_a_charge_not_yet_due(db_session: AsyncSession, unit: int, admin: User, finance: Finance) -> None:
    await finance.charge(unit, "100000", date(2027, 1, 5))  # pending even if not overdue
    assert (await rejected(db_session, unit, admin)).code == "UNIT_HAS_BALANCE"


async def test_with_credit(db_session: AsyncSession, unit: int, admin: User, finance: Finance) -> None:  # 4
    await finance.payment(unit, "80000")  # nothing to apply: all of it is credit
    assert (await rejected(db_session, unit, admin)).code == "UNIT_HAS_BALANCE"


async def test_with_leftover_credit_after_paying(db_session: AsyncSession, unit: int, admin: User, finance: Finance) -> None:
    charge = await finance.charge(unit, "100000")
    await finance.allocate(await finance.payment(unit, "100000.01"), charge, "100000")  # pending 0, credit 0.01
    assert (await rejected(db_session, unit, admin)).code == "UNIT_HAS_BALANCE"


async def test_with_pending_and_credit(db_session: AsyncSession, unit: int, admin: User, finance: Finance) -> None:
    await finance.charge(unit, "100000")
    await finance.payment(unit, "30000")  # not applied (both balances other than zero): the same error
    assert (await rejected(db_session, unit, admin)).code == "UNIT_HAS_BALANCE"


async def test_other_apartments_balance_does_not_matter(
    db_session: AsyncSession, community: Community, unit: int, admin: User, finance: Finance
) -> None:
    neighbor = await community.unit(1, 1, 102)
    await finance.charge(neighbor, "100000")
    await finance.payment(neighbor, "5000")
    await delete(db_session, unit, admin)
    assert await db_session.scalar(select(Unit.deleted_at).where(Unit.id == unit)) is not None
    assert await db_session.scalar(select(Unit.deleted_at).where(Unit.id == neighbor)) is None


# --- The existing rules keep their order ------------------------------------------------------------------


async def test_a_resident_is_reported_first(
    db_session: AsyncSession, community: Community, unit: int, admin: User, resident: User, finance: Finance
) -> None:  # 9
    await community.residence(resident.id, unit)
    await finance.charge(unit, "100000")
    with pytest.raises(UnitInUseError):  # HU-003 rule, unchanged
        await delete(db_session, unit, admin)
    assert await db_session.scalar(select(Unit.deleted_at).where(Unit.id == unit)) is None


async def test_a_version_conflict_is_reported_first(db_session: AsyncSession, unit: int, admin: User, finance: Finance) -> None:
    await finance.charge(unit, "100000")
    with pytest.raises(VersionConflictError):
        await structure_service.delete_unit(db_session, unit, 99, Actor(admin.id, None))


# --- Through the existing HU-013 endpoint (its error format, unchanged) -----------------------------------


async def test_delete_endpoint_answers_409(
    client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession, make_user: Any
) -> None:
    api = Structure(client, admin_headers)
    block = await api.block(1)
    tower = await api.tower(block["id"], 1)
    apartment = await api.unit(tower["id"], 101)
    admin_id = await db_session.scalar(select(User.id).where(User.role == UserRole.ADMINISTRADOR))
    db_session.add(Charge(unit_id=apartment["id"], concept="OTHER", description="Cargo", amount=Decimal("1"),
                          due_date=date(2026, 9, 5), created_by_user_id=admin_id))
    await db_session.flush()

    body = error(await api.delete(f"/units/{apartment['id']}", 1), 409, "UNIT_HAS_BALANCE")
    assert body["message"] == MESSAGE
    assert (await api.get(f"/units/{apartment['id']}")).status_code == 200  # still there

    other = await api.unit(tower["id"], 102)
    response = await api.delete(f"/units/{other['id']}", 1)
    assert response.status_code == 204  # the successful answer is unchanged
