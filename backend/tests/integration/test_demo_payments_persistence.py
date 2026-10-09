"""HU-010: the terminal states committed before the service's error survive across connections.

EXPIRED (completing at expires_at) and FAILED (approving after the resident is no longer
eligible) are committed by the service and then its error is raised. These tests use real
commits (committed_session_factory, never db_session or a savepoint): every change is made in
its own session, the service runs in another one, and every check reads from a new session.
"""

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.core.exceptions import DemoPaymentNotAppliedError, DemoTransactionExpiredError
from app.services import demo_payments
from tests.integration.test_demo_payments_concurrency import Factory, add_charge, complete, scalar, setup, start

pytestmark = pytest.mark.anyio

__all__ = ["setup"]  # fixture shared with the concurrency tests

NOW = datetime(2026, 10, 9, 20, 0)  # naive UTC


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """The service's clock, fixed; monkeypatch restores the real one after the test."""
    fixed = Clock()
    monkeypatch.setattr(demo_payments, "utc_now", fixed)
    return fixed


async def transaction_row(factory: Factory, transaction_id: int) -> tuple[Any, ...]:
    async with factory() as session:  # a new session: only committed data
        row = (await session.execute(text(
            "SELECT status, created_at, expires_at, finished_at, payment_id FROM demo_payment_transactions "
            "WHERE id = :t"), {"t": transaction_id})).one()
    return tuple(row)


async def demo_payments_of(factory: Factory, reference: str) -> tuple[int, int]:
    """Payments and allocations that this transaction could have created."""
    payments = await scalar(factory, "SELECT COUNT(*) FROM payments WHERE transaction_reference = :r", r=reference)
    allocations = await scalar(factory, "SELECT COUNT(*) FROM payment_allocations a JOIN payments p ON p.id = a.payment_id "
                                        "WHERE p.transaction_reference = :r", r=reference)
    return int(payments), int(allocations)


async def test_expired_is_committed_before_the_error(committed_session_factory: Factory, setup: dict[str, int],
                                                     clock: Clock) -> None:
    fee = await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, 10, 5))
    started = await start(committed_session_factory, setup["resident"], fee, "100000")
    assert started.status == "INITIATED" and started.expires_at == NOW + timedelta(minutes=15)

    clock.now = started.expires_at  # exactly at expiry
    result = await complete(committed_session_factory, setup["resident"], started.id, "APPROVED")
    assert isinstance(result, DemoTransactionExpiredError)

    # Read back from new sessions: EXPIRED was committed, it ended when it expired, nothing was paid.
    assert await transaction_row(committed_session_factory, started.id) == (
        "EXPIRED", NOW, started.expires_at, started.expires_at, None)
    assert await demo_payments_of(committed_session_factory, started.reference) == (0, 0)
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments") == 0
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM audit_logs WHERE action = 'DEMO_PAYMENT_EXPIRED' "
                                                   "AND entity_type = 'demo_payment_transaction' AND entity_id = :t",
                        t=started.id) == 1
    # Another connection now acts on the committed state: still expired, never approved.
    again = await complete(committed_session_factory, setup["resident"], started.id, "APPROVED")
    assert isinstance(again, DemoTransactionExpiredError)
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments") == 0


@pytest.mark.parametrize("change", [
    "UPDATE users SET status = 'DISABLED' WHERE id = :u",
    "UPDATE resident_units SET ended_at = UTC_TIMESTAMP(6) WHERE user_id = :u AND ended_at IS NULL",
], ids=["account-disabled", "residence-ended"])
async def test_failed_not_eligible_is_committed_before_the_error(
    committed_session_factory: Factory, setup: dict[str, int], clock: Clock, change: str
) -> None:
    fee = await add_charge(committed_session_factory, setup["unit"], "100000", date(2026, 10, 5))
    started = await start(committed_session_factory, setup["resident"], fee, "100000")
    assert started.status == "INITIATED"

    # The resident stops being eligible, committed in its own session.
    async with committed_session_factory() as session:
        changed = (await session.execute(text(change), {"u": setup["resident"]})).rowcount
        await session.commit()
    assert changed == 1

    clock.now = NOW + timedelta(minutes=1)  # well before expires_at
    result = await complete(committed_session_factory, setup["resident"], started.id, "APPROVED")
    assert isinstance(result, DemoPaymentNotAppliedError)

    # Read back from new sessions: FAILED was committed, with its end, and nothing was paid.
    assert await transaction_row(committed_session_factory, started.id) == (
        "FAILED", NOW, started.expires_at, NOW + timedelta(minutes=1), None)
    assert await demo_payments_of(committed_session_factory, started.reference) == (0, 0)
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payments") == 0
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM payment_allocations") == 0
    assert await scalar(committed_session_factory, "SELECT JSON_UNQUOTE(JSON_EXTRACT(context, '$.reason')) "
                                                   "FROM audit_logs WHERE action = 'DEMO_PAYMENT_FAILED' "
                                                   "AND entity_id = :t", t=started.id) == "NOT_ELIGIBLE"
    assert await scalar(committed_session_factory, "SELECT COUNT(*) FROM audit_logs WHERE action = 'DEMO_PAYMENT_FAILED' "
                                                   "AND entity_id = :t", t=started.id) == 1
