"""HU-010: the demo payment service against MySQL (start, complete, cancel, get, expiry), its
integration with payments and allocations, and the HU-004 / HU-008 regressions.

The clock of the service is fixed (utc_now patched): expiry is checked to the microsecond.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ChargeNotFoundError,
    ChargeNotPayableError,
    DemoPaymentInProgressError,
    DemoPaymentNotAppliedError,
    DemoTransactionExpiredError,
    DemoTransactionFinishedError,
    DemoTransactionNotFoundError,
    FieldValidationError,
    ForbiddenError,
)
from app.models import User
from app.schemas.payment import PaymentCreate, PaymentListQuery
from app.services import demo_instruments, demo_payments
from app.services import payments as payment_service
from app.services import resident_payments
from app.services.demo_payments import (
    cancel_demo_payment,
    complete_demo_payment,
    complete_demo_payment_with_instrument,
    get_demo_payment,
    start_demo_payment,
)
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender, sign_in
from tests.integration.test_payments_api import charge
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 9, 20, 0)  # naive UTC: 15:00 in Bogotá, 2026-10-09
TODAY = date(2026, 10, 9)
TTL = timedelta(minutes=15)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fixed = Clock()
    monkeypatch.setattr(demo_payments, "utc_now", fixed)
    return fixed


@dataclass
class World:
    admin: int
    laura: int
    laura_unit: int
    pedro: int
    pedro_unit: int
    fee: int  # Laura's October monthly fee, 100.000


@pytest.fixture
async def world(db_session: AsyncSession, make_user: Any) -> World:
    admin = await make_user("admin@example.com", password_hash="not-a-real-hash")
    community = Community(db_session, admin.id)
    laura = await community.resident("Laura Gómez", "1012345678", (1, 1, 101), email="laura@example.com")
    pedro = await community.resident("Pedro Ruiz", "1012345679", (1, 1, 102), email="pedro@example.com")
    laura_unit = await unit(db_session, laura)
    pedro_unit = await unit(db_session, pedro)
    fee = await charge(db_session, laura_unit, "100000", date(2026, 10, 5))
    return World(admin.id, laura, laura_unit, pedro, pedro_unit, fee)


async def unit(db: AsyncSession, user_id: int) -> int:
    return int(await db.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u AND ended_at IS NULL"),
                               {"u": user_id}))


async def rows(db: AsyncSession, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in (await db.execute(text(sql), params)).all()]


async def count(db: AsyncSession, table: str) -> int:
    return int(await db.scalar(text(f"SELECT COUNT(*) FROM {table}")))


async def outstanding(db: AsyncSession, charge_id: int) -> Decimal:
    return Decimal(await db.scalar(text(
        "SELECT c.amount - COALESCE((SELECT SUM(a.amount) FROM payment_allocations a WHERE a.charge_id = c.id), 0) "
        "FROM charges c WHERE c.id = :c"), {"c": charge_id}))


async def financial_state(db: AsyncSession) -> list[list[tuple[Any, ...]]]:
    """Everything a demo transaction must never touch unless it is approved."""
    return [
        await rows(db, "SELECT id, amount FROM charges ORDER BY id"),
        await rows(db, "SELECT id, amount, source FROM payments ORDER BY id"),
        await rows(db, "SELECT payment_id, charge_id, amount FROM payment_allocations ORDER BY id"),
    ]


async def audits(db: AsyncSession, action: str) -> list[tuple[Any, ...]]:
    return await rows(db, "SELECT entity_type, entity_id, actor_user_id, context FROM audit_logs WHERE action = :a "
                          "ORDER BY id", a=action)


async def manual_payment(db: AsyncSession, world: World, amount: str) -> Any:
    """A manual payment of HU-004 for Laura (FIFO over her apartment's charges)."""
    data = PaymentCreate.model_validate({"resident_id": world.laura, "amount": amount, "method": "TRANSFER",
                                         "concept": "MONTHLY_FEE", "payment_date": "2026-10-08"})
    return await payment_service.register_payment(db, data, Actor(world.admin, None), mailer=FakeMailSender())


# --- Start --------------------------------------------------------------------------------------------------


async def test_start_creates_an_initiated_transaction(db_session: AsyncSession, world: World, clock: Clock) -> None:
    before = await financial_state(db_session)
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"), ip_address="10.0.0.1")

    assert started.status == "INITIATED" and started.charge_id == world.fee and started.amount == Decimal("100000.00")
    assert started.created_at == NOW and started.expires_at == NOW + TTL  # exactly 15 minutes
    assert started.finished_at is None and started.payment_id is None and started.receipt_number is None
    assert started.reference.startswith("DEMO-")
    assert await rows(db_session, "SELECT resident_id, charge_id, amount, reference, status, created_at, expires_at, "
                                  "finished_at, payment_id FROM demo_payment_transactions") == [
        (world.laura, world.fee, Decimal("100000.00"), started.reference, "INITIATED", NOW, NOW + TTL, None, None)
    ]
    # Nothing financial yet.
    assert await financial_state(db_session) == before
    [(entity, entity_id, actor, context)] = await audits(db_session, "DEMO_PAYMENT_STARTED")
    assert (entity, entity_id, actor) == ("demo_payment_transaction", started.id, world.laura)
    assert '"reference": "' + started.reference + '"' in context and f'"unit_id": {world.laura_unit}' in context


@pytest.mark.parametrize("amount", [None, Decimal("0"), Decimal("-1"), 100000.0, "50000", Decimal("100000.01"),
                                    Decimal("1.005")])
async def test_invalid_amounts_are_rejected(db_session: AsyncSession, world: World, clock: Clock, amount: Any) -> None:
    with pytest.raises(FieldValidationError) as error:
        await start_demo_payment(db_session, world.laura, world.fee, amount)
    assert error.value.details is not None and error.value.details[0]["field"] == "amount"
    assert await count(db_session, "demo_payment_transactions") == 0


async def test_the_amount_is_limited_by_the_outstanding_balance(db_session: AsyncSession, world: World,
                                                                clock: Clock) -> None:
    with pytest.raises(FieldValidationError) as error:
        await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000.01"))
    assert error.value.details == [{"field": "amount", "message":
                                    "El monto no puede superar el saldo pendiente de la obligación ($ 100.000,00)."}]


async def test_only_an_own_existing_charge_can_be_paid(db_session: AsyncSession, world: World, clock: Clock) -> None:
    pedro_fee = await charge(db_session, world.pedro_unit, "90000", date(2026, 10, 5))
    for resident, charge_id in ((world.laura, pedro_fee), (world.pedro, world.fee), (world.laura, 999_999_999),
                                (world.laura, 0), (world.laura, True), (world.laura, "1")):
        with pytest.raises(ChargeNotFoundError):
            await start_demo_payment(db_session, resident, charge_id, Decimal("1000"))  # type: ignore[arg-type]
    assert await count(db_session, "demo_payment_transactions") == 0


async def test_a_paid_charge_is_not_payable(db_session: AsyncSession, world: World, clock: Clock) -> None:
    await manual_payment(db_session, world, "100000")
    assert await outstanding(db_session, world.fee) == 0
    with pytest.raises(ChargeNotPayableError):
        await start_demo_payment(db_session, world.laura, world.fee, Decimal("1000"))


async def test_a_former_resident_or_a_disabled_account_cannot_start(db_session: AsyncSession, world: World,
                                                                     clock: Clock) -> None:
    # Laura moved out: her old apartment's charge is no longer hers.
    community = Community(db_session, world.admin)
    former = await community.resident("Ana Antes", "1012345670", (2, 1, 103), email="ana@example.com", ended=True)
    old_unit = int(await db_session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": former}))
    old_fee = await charge(db_session, old_unit, "50000", date(2026, 10, 5))
    with pytest.raises(ChargeNotFoundError):
        await start_demo_payment(db_session, former, old_fee, Decimal("1000"))
    # A disabled account that still lives there.
    await db_session.execute(text("UPDATE users SET status = 'DISABLED' WHERE id = :u"), {"u": world.laura})
    with pytest.raises(ForbiddenError):
        await start_demo_payment(db_session, world.laura, world.fee, Decimal("1000"))
    assert await count(db_session, "demo_payment_transactions") == 0


async def test_one_live_transaction_per_charge(db_session: AsyncSession, world: World, clock: Clock) -> None:
    first = await start_demo_payment(db_session, world.laura, world.fee, Decimal("50000"))
    with pytest.raises(DemoPaymentInProgressError):
        await start_demo_payment(db_session, world.laura, world.fee, Decimal("10000"))
    # Another charge of the same apartment is independent.
    other = await charge(db_session, world.laura_unit, "20000", date(2026, 10, 20), concept="PENALTY",
                         description="Ruido")
    await start_demo_payment(db_session, world.laura, other, Decimal("20000"))

    # Once the first one has expired, a new one can start; the first becomes EXPIRED (ended when it expired).
    clock.now = first.expires_at
    second = await start_demo_payment(db_session, world.laura, world.fee, Decimal("10000"))
    assert await rows(db_session, "SELECT id, status, finished_at FROM demo_payment_transactions "
                                  "WHERE charge_id = :c ORDER BY id", c=world.fee) == [
        (first.id, "EXPIRED", first.expires_at), (second.id, "INITIATED", None)
    ]
    [(_, entity_id, actor, _)] = await audits(db_session, "DEMO_PAYMENT_EXPIRED")
    assert (entity_id, actor) == (first.id, None)  # the clock, not a user


# --- Approval -------------------------------------------------------------------------------------------------


async def test_approval_pays_only_the_selected_charge(db_session: AsyncSession, world: World, clock: Clock) -> None:
    older = await charge(db_session, world.laura_unit, "80000", date(2026, 9, 5))  # FIFO would pay this one first
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    clock.now = NOW + timedelta(minutes=3)

    approved = await complete_demo_payment(db_session, world.laura, started.id, "APPROVED")

    assert approved.status == "APPROVED" and approved.finished_at == clock.now
    assert approved.payment_id is not None and approved.receipt_number == f"RC-{approved.payment_id:08d}"
    assert await rows(db_session, "SELECT resident_id, unit_id, registered_by_user_id, amount, method, source, concept, "
                                  "concept_description, transaction_reference, payment_date, registered_at, "
                                  "resident_name_snapshot, unit_label_snapshot, notes FROM payments") == [
        (world.laura, world.laura_unit, world.laura, Decimal("100000.00"), "DEMO_GATEWAY", "DEMO_GATEWAY",
         "MONTHLY_FEE", None, started.reference, TODAY, clock.now, "Laura Gómez", "B1-TO1-APTO-101", None)
    ]
    # One allocation, to the selected charge only; the older charge is untouched.
    assert await rows(db_session, "SELECT payment_id, charge_id, amount FROM payment_allocations") == [
        (approved.payment_id, world.fee, Decimal("100000.00"))
    ]
    assert await outstanding(db_session, world.fee) == 0 and await outstanding(db_session, older) == Decimal("80000.00")
    assert await rows(db_session, "SELECT status, payment_id, finished_at FROM demo_payment_transactions") == [
        ("APPROVED", approved.payment_id, clock.now)
    ]
    [(entity, entity_id, actor, context)] = await audits(db_session, "PAYMENT_REGISTERED")
    assert (entity, entity_id, actor) == ("payment", approved.payment_id, world.laura)
    assert '"source": "DEMO_GATEWAY"' in context and f'"demo_transaction_id": {started.id}' in context
    assert [r[1:3] for r in await audits(db_session, "DEMO_PAYMENT_APPROVED")] == [(started.id, world.laura)]


async def test_a_partial_payment_leaves_the_rest_pending(db_session: AsyncSession, world: World, clock: Clock) -> None:
    first = await start_demo_payment(db_session, world.laura, world.fee, Decimal("40000"))
    await complete_demo_payment(db_session, world.laura, first.id, "APPROVED")
    assert await outstanding(db_session, world.fee) == Decimal("60000.00")
    with pytest.raises(FieldValidationError):
        await start_demo_payment(db_session, world.laura, world.fee, Decimal("60000.01"))
    rest = await start_demo_payment(db_session, world.laura, world.fee, Decimal("60000"))
    await complete_demo_payment(db_session, world.laura, rest.id, "APPROVED")
    assert await outstanding(db_session, world.fee) == 0
    assert await rows(db_session, "SELECT charge_id, amount FROM payment_allocations ORDER BY id") == [
        (world.fee, Decimal("40000.00")), (world.fee, Decimal("60000.00"))
    ]


@pytest.mark.parametrize(("concept", "description", "expected"), [
    ("OTHER", "Reparación de la puerta", "Reparación de la puerta"),
    ("PENALTY", "Ruido después de las 10", None),  # a payment only describes OTHER (ck_payments_description_only_for_other)
    ("LATE_FEE", None, None),
])
async def test_the_concept_comes_from_the_charge(db_session: AsyncSession, world: World, clock: Clock, concept: str,
                                                 description: str | None, expected: str | None) -> None:
    selected = await charge(db_session, world.laura_unit, "30000", date(2026, 10, 15), concept=concept,
                            description=description)
    started = await start_demo_payment(db_session, world.laura, selected, Decimal("30000"))
    await complete_demo_payment(db_session, world.laura, started.id, "APPROVED")
    assert await rows(db_session, "SELECT concept, concept_description FROM payments") == [(concept, expected)]


# --- Rejected, failed, cancelled, expired ------------------------------------------------------------------


@pytest.mark.parametrize(("outcome", "action", "reason"), [
    ("REJECTED", "DEMO_PAYMENT_REJECTED", None),
    ("FAILED", "DEMO_PAYMENT_FAILED", "SIMULATED"),
    ("CANCELLED", "DEMO_PAYMENT_CANCELLED", None),
])
async def test_an_unsuccessful_end_changes_nothing_financial(
    db_session: AsyncSession, world: World, clock: Clock, outcome: str, action: str, reason: str | None
) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    before = await financial_state(db_session)
    clock.now = NOW + timedelta(minutes=1)

    if outcome == "CANCELLED":
        ended = await cancel_demo_payment(db_session, world.laura, started.id)
    else:
        ended = await complete_demo_payment(db_session, world.laura, started.id, outcome)

    assert (ended.status, ended.finished_at, ended.payment_id, ended.receipt_number) == (outcome, clock.now, None, None)
    assert await financial_state(db_session) == before
    assert await outstanding(db_session, world.fee) == Decimal("100000.00")
    [(_, entity_id, actor, context)] = await audits(db_session, action)
    assert (entity_id, actor) == (started.id, world.laura)
    assert ('"reason": "SIMULATED"' in context) == (reason == "SIMULATED")


async def test_expiry_is_exact(db_session: AsyncSession, world: World, clock: Clock) -> None:
    # One microsecond before expires_at it can still be approved.
    on_time = await start_demo_payment(db_session, world.laura, world.fee, Decimal("10000"))
    clock.now = on_time.expires_at - timedelta(microseconds=1)
    assert (await complete_demo_payment(db_session, world.laura, on_time.id, "APPROVED")).status == "APPROVED"

    # At expires_at it is expired: EXPIRED is persisted and nothing is paid.
    clock.now = NOW
    late = await start_demo_payment(db_session, world.laura, world.fee, Decimal("10000"))
    clock.now = late.expires_at
    before = await financial_state(db_session)
    with pytest.raises(DemoTransactionExpiredError):
        await complete_demo_payment(db_session, world.laura, late.id, "APPROVED")
    assert await rows(db_session, "SELECT status, finished_at, payment_id FROM demo_payment_transactions WHERE id = :t",
                      t=late.id) == [("EXPIRED", late.expires_at, None)]
    assert await financial_state(db_session) == before
    # It stays expired whatever is asked afterwards.
    for attempt in (lambda: complete_demo_payment(db_session, world.laura, late.id, "APPROVED"),
                    lambda: complete_demo_payment(db_session, world.laura, late.id, "REJECTED"),
                    lambda: cancel_demo_payment(db_session, world.laura, late.id)):
        with pytest.raises(DemoTransactionExpiredError):
            await attempt()
    assert (await get_demo_payment(db_session, world.laura, late.id)).status == "EXPIRED"
    assert len(await audits(db_session, "DEMO_PAYMENT_EXPIRED")) == 1


async def test_reading_resolves_the_expiry(db_session: AsyncSession, world: World, clock: Clock) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("10000"))
    clock.now = started.expires_at - timedelta(microseconds=1)
    assert (await get_demo_payment(db_session, world.laura, started.id)).status == "INITIATED"
    clock.now = started.expires_at
    read = await get_demo_payment(db_session, world.laura, started.id)
    assert (read.status, read.finished_at) == ("EXPIRED", started.expires_at)
    assert await rows(db_session, "SELECT status FROM demo_payment_transactions") == [("EXPIRED",)]
    assert len(await audits(db_session, "DEMO_PAYMENT_EXPIRED")) == 1


# --- Repetitions and terminal transactions ------------------------------------------------------------------


async def test_a_repeated_approval_returns_the_same_payment(db_session: AsyncSession, world: World,
                                                            clock: Clock) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    first = await complete_demo_payment(db_session, world.laura, started.id, "APPROVED")
    clock.now = NOW + timedelta(hours=1)  # even after its expiry time: it is already finished
    again = await complete_demo_payment(db_session, world.laura, started.id, "APPROVED")
    assert again == first
    assert (await count(db_session, "payments"), await count(db_session, "payment_allocations")) == (1, 1)
    assert len(await audits(db_session, "DEMO_PAYMENT_APPROVED")) == 1


@pytest.mark.parametrize("ended_as", ["APPROVED", "REJECTED", "FAILED", "CANCELLED"])
async def test_a_finished_transaction_never_changes(db_session: AsyncSession, world: World, clock: Clock,
                                                    ended_as: str) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    if ended_as == "CANCELLED":
        ended = await cancel_demo_payment(db_session, world.laura, started.id)
    else:
        ended = await complete_demo_payment(db_session, world.laura, started.id, ended_as)
    state = await financial_state(db_session)

    attempts: dict[str, Callable[[], Any]] = {
        "APPROVED": lambda: complete_demo_payment(db_session, world.laura, started.id, "APPROVED"),
        "REJECTED": lambda: complete_demo_payment(db_session, world.laura, started.id, "REJECTED"),
        "FAILED": lambda: complete_demo_payment(db_session, world.laura, started.id, "FAILED"),
        "CANCELLED": lambda: cancel_demo_payment(db_session, world.laura, started.id),
    }
    for asked, attempt in attempts.items():
        if asked == ended_as:
            assert await attempt() == ended  # the same request again: the same answer, no effect
        else:
            with pytest.raises(DemoTransactionFinishedError):
                await attempt()
    assert await get_demo_payment(db_session, world.laura, started.id) == ended
    assert await financial_state(db_session) == state


@pytest.mark.parametrize("outcome", ["CANCELLED", "EXPIRED", "INITIATED", "approved", "", None])
async def test_only_a_gateway_outcome_completes(db_session: AsyncSession, world: World, clock: Clock,
                                                outcome: Any) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    with pytest.raises(FieldValidationError):
        await complete_demo_payment(db_session, world.laura, started.id, outcome)
    assert (await get_demo_payment(db_session, world.laura, started.id)).status == "INITIATED"


async def test_another_residents_transaction_does_not_exist(db_session: AsyncSession, world: World,
                                                            clock: Clock) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    for transaction_id, resident in ((started.id, world.pedro), (999_999_999, world.laura), (0, world.laura),
                                     ("1", world.laura)):
        for attempt in (lambda: complete_demo_payment(db_session, resident, transaction_id, "APPROVED"),
                        lambda: cancel_demo_payment(db_session, resident, transaction_id),
                        lambda: get_demo_payment(db_session, resident, transaction_id)):
            with pytest.raises(DemoTransactionNotFoundError):
                await attempt()
    assert (await get_demo_payment(db_session, world.laura, started.id)).status == "INITIATED"
    assert await count(db_session, "payments") == 0


# --- The obligation changed between start and approval -----------------------------------------------------


async def test_an_obligation_paid_meanwhile_is_never_charged_twice(db_session: AsyncSession, world: World,
                                                                   clock: Clock) -> None:
    other = await charge(db_session, world.laura_unit, "50000", date(2026, 11, 5))
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    await manual_payment(db_session, world, "30000")  # HU-004 FIFO: covers 30.000 of the fee meanwhile
    before = await financial_state(db_session)

    with pytest.raises(DemoPaymentNotAppliedError):
        await complete_demo_payment(db_session, world.laura, started.id, "APPROVED")

    # FAILED and committed; no payment, nothing moved to another charge.
    assert await rows(db_session, "SELECT status, payment_id, finished_at FROM demo_payment_transactions") == [
        ("FAILED", None, NOW)
    ]
    assert await financial_state(db_session) == before
    assert (await outstanding(db_session, world.fee), await outstanding(db_session, other)) == (
        Decimal("70000.00"), Decimal("50000.00"))
    [(_, _, actor, context)] = await audits(db_session, "DEMO_PAYMENT_FAILED")
    assert actor == world.laura and '"reason": "OUTSTANDING_CHANGED"' in context


@pytest.mark.parametrize("change", [
    "UPDATE users SET status = 'DISABLED' WHERE id = :u",
    "UPDATE resident_units SET ended_at = UTC_TIMESTAMP(6) WHERE user_id = :u",
])
async def test_a_resident_no_longer_eligible_is_not_charged(db_session: AsyncSession, world: World, clock: Clock,
                                                            change: str) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    await db_session.execute(text(change), {"u": world.laura})
    with pytest.raises(DemoPaymentNotAppliedError):
        await complete_demo_payment(db_session, world.laura, started.id, "APPROVED")
    assert await rows(db_session, "SELECT status, payment_id FROM demo_payment_transactions") == [("FAILED", None)]
    assert await count(db_session, "payments") == 0
    [(_, _, _, context)] = await audits(db_session, "DEMO_PAYMENT_FAILED")
    assert '"reason": "NOT_ELIGIBLE"' in context


# --- Fictitious test instruments (demo checkout) ------------------------------------------------------------

APPROVE, FUNDS, TECHNICAL = "TEST_CARD_APPROVE", "TEST_CARD_INSUFFICIENT_FUNDS", "TEST_ACCOUNT_TECHNICAL_ERROR"


async def stored(db: AsyncSession) -> list[tuple[Any, ...]]:
    return await rows(db, "SELECT status, payment_id, test_instrument FROM demo_payment_transactions ORDER BY id")


async def demo_audits(db: AsyncSession) -> list[tuple[Any, ...]]:
    return await rows(db, "SELECT action, context FROM audit_logs WHERE action LIKE 'DEMO_PAYMENT_%' "
                          "AND action <> 'DEMO_PAYMENT_STARTED' ORDER BY id")


async def test_the_approving_instrument_pays_once(db_session: AsyncSession, world: World, clock: Clock) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("40000"))
    clock.now = NOW + timedelta(minutes=2)

    approved = await complete_demo_payment_with_instrument(db_session, world.laura, started.id, APPROVE,
                                                           ip_address="10.0.0.9")

    assert (approved.status, approved.finished_at, approved.test_instrument) == ("APPROVED", clock.now, APPROVE)
    assert approved.payment_id is not None and approved.receipt_number == f"RC-{approved.payment_id:08d}"
    # The same accounting as ever: one payment, one allocation to the selected charge.
    assert await rows(db_session, "SELECT amount, method, source, transaction_reference FROM payments") == [
        (Decimal("40000.00"), "DEMO_GATEWAY", "DEMO_GATEWAY", started.reference)
    ]
    assert await rows(db_session, "SELECT payment_id, charge_id, amount FROM payment_allocations") == [
        (approved.payment_id, world.fee, Decimal("40000.00"))
    ]
    assert await outstanding(db_session, world.fee) == Decimal("60000.00")
    assert await stored(db_session) == [("APPROVED", approved.payment_id, APPROVE)]  # in the same UPDATE
    [(_, entity_id, actor, context)] = await audits(db_session, "DEMO_PAYMENT_APPROVED")
    assert (entity_id, actor) == (started.id, world.laura) and f'"instrument_id": "{APPROVE}"' in context
    assert len(await audits(db_session, "PAYMENT_REGISTERED")) == 1


@pytest.mark.parametrize(("instrument", "status", "action", "reason", "simulated"), [
    (FUNDS, "REJECTED", "DEMO_PAYMENT_REJECTED", None, "INSUFFICIENT_FUNDS"),
    (TECHNICAL, "FAILED", "DEMO_PAYMENT_FAILED", "SIMULATED", "TECHNICAL_ERROR"),
])
async def test_an_instrument_that_does_not_approve_changes_nothing_financial(
    db_session: AsyncSession, world: World, clock: Clock, instrument: str, status: str, action: str,
    reason: str | None, simulated: str,
) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    before = await financial_state(db_session)

    ended = await complete_demo_payment_with_instrument(db_session, world.laura, started.id, instrument)

    assert (ended.status, ended.payment_id, ended.receipt_number, ended.test_instrument) == (status, None, None,
                                                                                               instrument)
    assert await financial_state(db_session) == before and await count(db_session, "payments") == 0
    assert await outstanding(db_session, world.fee) == Decimal("100000.00")
    assert await stored(db_session) == [(status, None, instrument)]
    [(_, _, actor, context)] = await audits(db_session, action)
    assert actor == world.laura
    assert f'"instrument_id": "{instrument}"' in context and f'"simulated_reason": "{simulated}"' in context
    assert ('"reason": "SIMULATED"' in context) == (reason == "SIMULATED")


@pytest.mark.parametrize("instrument_id", ["TEST_CARD_UNKNOWN", "APPROVED", "test_card_approve", "", None, 1])
async def test_an_unknown_instrument_changes_nothing(db_session: AsyncSession, world: World, clock: Clock,
                                                     instrument_id: Any) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    before = await financial_state(db_session)
    with pytest.raises(FieldValidationError) as error:
        await complete_demo_payment_with_instrument(db_session, world.laura, started.id, instrument_id)
    assert error.value.details is not None and error.value.details[0]["field"] == "instrument_id"
    assert await stored(db_session) == [("INITIATED", None, None)]
    assert await financial_state(db_session) == before and await demo_audits(db_session) == []
    # Still pending: a valid instrument completes it afterwards.
    assert (await complete_demo_payment_with_instrument(db_session, world.laura, started.id, FUNDS)).status == "REJECTED"


async def test_a_retired_instrument_cannot_complete(db_session: AsyncSession, world: World, clock: Clock,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    monkeypatch.setattr(demo_instruments, "RETIRED_INSTRUMENT_IDS", frozenset({APPROVE}))
    with pytest.raises(FieldValidationError) as error:
        await complete_demo_payment_with_instrument(db_session, world.laura, started.id, APPROVE)
    assert error.value.details == [{"field": "instrument_id", "message": demo_instruments.RETIRED_MESSAGE}]
    assert await stored(db_session) == [("INITIATED", None, None)] and await count(db_session, "payments") == 0


@pytest.mark.parametrize(("change", "reason"), [
    ("manual", "OUTSTANDING_CHANGED"),
    ("UPDATE users SET status = 'DISABLED' WHERE id = :u", "NOT_ELIGIBLE"),
    ("UPDATE resident_units SET ended_at = UTC_TIMESTAMP(6) WHERE user_id = :u", "NOT_ELIGIBLE"),
])
async def test_an_approval_that_can_no_longer_be_applied_keeps_its_instrument(
    db_session: AsyncSession, world: World, clock: Clock, change: str, reason: str
) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    if change == "manual":
        await manual_payment(db_session, world, "30000")  # covers part of the fee meanwhile
    else:
        await db_session.execute(text(change), {"u": world.laura})
    before = await financial_state(db_session)

    with pytest.raises(DemoPaymentNotAppliedError):
        await complete_demo_payment_with_instrument(db_session, world.laura, started.id, APPROVE)

    # The backend's result is the authority: FAILED (committed), with the instrument that was used.
    assert await stored(db_session) == [("FAILED", None, APPROVE)]
    assert await financial_state(db_session) == before
    [(action, context)] = await demo_audits(db_session)
    assert action == "DEMO_PAYMENT_FAILED" and f'"reason": "{reason}"' in context
    assert f'"instrument_id": "{APPROVE}"' in context and "simulated_reason" not in context  # a real failure
    # The same instrument again: the stored result, nothing new; another instrument: a conflict.
    again = await complete_demo_payment_with_instrument(db_session, world.laura, started.id, APPROVE)
    assert (again.status, again.test_instrument, again.payment_id) == ("FAILED", APPROVE, None)
    with pytest.raises(DemoTransactionFinishedError):
        await complete_demo_payment_with_instrument(db_session, world.laura, started.id, FUNDS)
    assert len(await demo_audits(db_session)) == 1 and await count(db_session, "payments") == (
        1 if change == "manual" else 0)


async def test_the_same_instrument_again_returns_the_stored_result(db_session: AsyncSession, world: World,
                                                                   clock: Clock) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    first = await complete_demo_payment_with_instrument(db_session, world.laura, started.id, APPROVE)
    clock.now = NOW + timedelta(hours=1)  # even after its expiry time: it is already finished

    again = await complete_demo_payment_with_instrument(db_session, world.laura, started.id, APPROVE)

    assert again == first
    assert (await count(db_session, "payments"), await count(db_session, "payment_allocations")) == (1, 1)
    assert [action for action, _ in await demo_audits(db_session)] == ["DEMO_PAYMENT_APPROVED"]
    # Another instrument never changes a finished transaction.
    for other in (FUNDS, TECHNICAL):
        with pytest.raises(DemoTransactionFinishedError):
            await complete_demo_payment_with_instrument(db_session, world.laura, started.id, other)
    assert await stored(db_session) == [("APPROVED", first.payment_id, APPROVE)]
    assert await get_demo_payment(db_session, world.laura, started.id) == first


@pytest.mark.parametrize("ended_as", ["APPROVED", "REJECTED", "FAILED", "CANCELLED"])
async def test_a_transaction_finished_without_an_instrument_never_gets_one(
    db_session: AsyncSession, world: World, clock: Clock, ended_as: str
) -> None:
    # As every transaction finished before instruments existed (test_instrument NULL).
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    if ended_as == "CANCELLED":
        ended = await cancel_demo_payment(db_session, world.laura, started.id)
    else:
        ended = await complete_demo_payment(db_session, world.laura, started.id, ended_as)
    state, trail = await financial_state(db_session), await demo_audits(db_session)
    assert ended.test_instrument is None

    for instrument in (APPROVE, FUNDS, TECHNICAL):
        with pytest.raises(DemoTransactionFinishedError):
            await complete_demo_payment_with_instrument(db_session, world.laura, started.id, instrument)
    assert await stored(db_session) == [(ended_as, ended.payment_id, None)]
    assert await financial_state(db_session) == state and await demo_audits(db_session) == trail
    assert await get_demo_payment(db_session, world.laura, started.id) == ended


async def test_expiry_cancellation_and_reading_with_instruments(db_session: AsyncSession, world: World,
                                                                clock: Clock) -> None:
    # Expired: EXPIRED (committed) without an instrument, then 409, whatever instrument is used.
    expired = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    clock.now = expired.expires_at
    with pytest.raises(DemoTransactionExpiredError):
        await complete_demo_payment_with_instrument(db_session, world.laura, expired.id, APPROVE)
    with pytest.raises(DemoTransactionExpiredError):
        await complete_demo_payment_with_instrument(db_session, world.laura, expired.id, APPROVE)
    assert await stored(db_session) == [("EXPIRED", None, None)]

    # Cancelled: no instrument, and none afterwards.
    cancelled = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    assert (await cancel_demo_payment(db_session, world.laura, cancelled.id)).test_instrument is None
    with pytest.raises(DemoTransactionFinishedError):
        await complete_demo_payment_with_instrument(db_session, world.laura, cancelled.id, FUNDS)

    # Reading shows the instrument of a resolved transaction.
    rejected = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    ended = await complete_demo_payment_with_instrument(db_session, world.laura, rejected.id, FUNDS)
    assert await get_demo_payment(db_session, world.laura, rejected.id) == ended
    assert await stored(db_session) == [("EXPIRED", None, None), ("CANCELLED", None, None), ("REJECTED", None, FUNDS)]
    assert await count(db_session, "payments") == 0
    # The CHECK holds for every row: an instrument only on APPROVED, REJECTED or FAILED.
    assert await rows(db_session, "SELECT COUNT(*) FROM demo_payment_transactions WHERE test_instrument IS NOT NULL "
                                  "AND status NOT IN ('APPROVED', 'REJECTED', 'FAILED')") == [(0,)]


async def test_an_instrument_never_opens_another_residents_transaction(db_session: AsyncSession, world: World,
                                                                       clock: Clock) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    for transaction_id, resident in ((started.id, world.pedro), (999_999_999, world.laura), (0, world.laura),
                                     ("1", world.laura)):
        with pytest.raises(DemoTransactionNotFoundError):
            await complete_demo_payment_with_instrument(db_session, resident, transaction_id, APPROVE)
    assert await stored(db_session) == [("INITIATED", None, None)] and await count(db_session, "payments") == 0


# --- Real errors -----------------------------------------------------------------------------------------------


async def test_a_real_error_rolls_everything_back(db_session: AsyncSession, world: World, clock: Clock,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    before = await financial_state(db_session)
    real = demo_payments.record_audit

    def broken(db: AsyncSession, **entry: Any) -> Any:
        # After the payment, its allocation and the APPROVED update were flushed.
        if entry["action"] == "DEMO_PAYMENT_APPROVED":
            raise RuntimeError("Lost connection to MySQL server")
        return real(db, **entry)

    monkeypatch.setattr(demo_payments, "record_audit", broken)
    with pytest.raises(RuntimeError, match="Lost connection"):
        await complete_demo_payment(db_session, world.laura, started.id, "APPROVED")
    monkeypatch.setattr(demo_payments, "record_audit", real)

    # Nothing of it is left: no payment, allocation, audit or APPROVED status.
    assert await financial_state(db_session) == before
    assert await rows(db_session, "SELECT status, payment_id, finished_at FROM demo_payment_transactions") == [
        ("INITIATED", None, None)
    ]
    assert await audits(db_session, "PAYMENT_REGISTERED") == []
    # The transaction is still usable: approving again works, once.
    assert (await complete_demo_payment(db_session, world.laura, started.id, "APPROVED")).status == "APPROVED"
    assert await count(db_session, "payments") == 1


async def test_the_guard_catches_a_stale_read(db_session: AsyncSession, world: World, clock: Clock,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if the outstanding balance were read from an old snapshot, the guard re-reads and rolls back."""
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("100000"))
    await manual_payment(db_session, world, "100000")
    before = await financial_state(db_session)

    async def stale(_db: AsyncSession, _charge: Any) -> Decimal:
        return Decimal("100000.00")  # as if the manual payment had not been seen

    monkeypatch.setattr(demo_payments, "_outstanding", stale)
    with pytest.raises(RuntimeError, match="invariant"):
        await complete_demo_payment(db_session, world.laura, started.id, "APPROVED")
    assert await financial_state(db_session) == before
    assert await rows(db_session, "SELECT status FROM demo_payment_transactions") == [("INITIATED",)]


# --- HU-004 and HU-008 regressions -------------------------------------------------------------------------


async def test_manual_payments_keep_fifo_and_their_methods(db_session: AsyncSession, world: World, clock: Clock,
                                                           client: AsyncClient) -> None:
    headers = await sign_in(client, db_session, await db_session.get(User, world.admin))  # type: ignore[arg-type]
    older = await charge(db_session, world.laura_unit, "80000", date(2026, 9, 5))
    registered = await manual_payment(db_session, world, "90000")
    # FIFO as before: the older charge first, then 10.000 of the fee; MANUAL, by the administrator.
    assert [(a.charge_id, a.amount) for a in registered.allocations] == [
        (older, Decimal("80000.00")), (world.fee, Decimal("10000.00"))]
    assert await rows(db_session, "SELECT source, method, registered_by_user_id FROM payments") == [
        ("MANUAL", "TRANSFER", world.admin)]
    # The administrator can never register a DEMO_GATEWAY payment.
    response = await client.post("/api/v1/admin/payments", headers=headers, json={
        "resident_id": world.laura, "amount": "1000", "method": "DEMO_GATEWAY", "concept": "MONTHLY_FEE",
        "payment_date": "2026-10-08"})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "method", "message": "Selecciona un método de pago: efectivo, tarjeta o transferencia."}]
    assert await count(db_session, "payments") == 1


async def test_an_approved_demo_payment_in_the_histories(db_session: AsyncSession, world: World, clock: Clock) -> None:
    started = await start_demo_payment(db_session, world.laura, world.fee, Decimal("40000"))
    approved = await complete_demo_payment(db_session, world.laura, started.id, "APPROVED")

    # HU-008: Laura sees it, with its reference, applied to the selected charge only.
    page = await resident_payments.list_resident_payments(db_session, world.laura, resident_payments.ResidentPaymentQuery())
    [item] = page.items
    assert (item.id, item.receipt_number, item.method, item.status, item.transaction_reference, item.amount) == (
        approved.payment_id, approved.receipt_number, "DEMO_GATEWAY", "APPLIED", started.reference, Decimal("40000.00"))
    detail = await resident_payments.get_resident_payment(db_session, world.laura, approved.payment_id)  # type: ignore[arg-type]
    assert (detail.applied_amount, detail.credit_amount) == (Decimal("40000.00"), Decimal("0.00"))
    assert [(a.charge_id, a.allocated_amount) for a in detail.allocations] == [(world.fee, Decimal("40000.00"))]
    # Pedro does not.
    assert (await resident_payments.list_resident_payments(
        db_session, world.pedro, resident_payments.ResidentPaymentQuery())).items == ()
    # HU-004: the administrator's list has it.
    listed = await payment_service.list_payments(db_session, PaymentListQuery())
    assert [(p.id, p.method, p.status) for p in listed.items] == [(approved.payment_id, "DEMO_GATEWAY", "APPLIED")]


def test_no_route_is_published_while_the_demo_gateway_is_disabled() -> None:
    # HU-010-C publishes the routes only with DEMO_PAYMENTS_ENABLED (see test_demo_payments_api.py).
    from app.core.config import get_settings
    from app.main import create_app

    disabled = create_app(get_settings().model_copy(update={"demo_payments_enabled": False}))
    assert not [path for path in disabled.openapi()["paths"] if "demo" in path or "gateway" in path]


def test_openapi_offers_only_the_manual_methods_for_a_manual_payment() -> None:
    """POST /admin/payments documents CASH, CARD and TRANSFER; the payment responses keep DEMO_GATEWAY."""
    from app.main import app

    spec = app.openapi()
    schemas = spec["components"]["schemas"]

    def resolve(schema: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in schema:
            schema = schemas[schema["$ref"].removeprefix("#/components/schemas/")]
        return schema

    def json_schema(content: dict[str, Any]) -> dict[str, Any]:
        return resolve(content["content"]["application/json"]["schema"])

    def methods(model: dict[str, Any]) -> list[str]:
        return resolve(model["properties"]["method"])["enum"]

    # The schema the manual route really receives.
    operation = spec["paths"]["/api/v1/admin/payments"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PaymentCreate"}
    assert methods(json_schema(operation["requestBody"])) == ["CASH", "CARD", "TRANSFER"]

    # What the payment routes answer can be a demo gateway payment.
    everything = ["CASH", "CARD", "TRANSFER", "DEMO_GATEWAY"]
    assert methods(json_schema(operation["responses"]["201"])) == everything  # PaymentRegistered
    for path, page in (("/api/v1/admin/payments", True), ("/api/v1/resident/payments", True),
                       ("/api/v1/resident/payments/{payment_id}", False)):
        answer = json_schema(spec["paths"][path]["get"]["responses"]["200"])
        assert methods(resolve(answer["properties"]["items"]["items"]) if page else answer) == everything
