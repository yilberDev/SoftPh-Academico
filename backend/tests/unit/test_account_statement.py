"""HU-011 criterion 3.1: the account status comes from the obligation statuses only (no database)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models import FinancialConcept
from app.services.account_statement import AccountStatus, account_status
from app.services.allocation import ChargeStatus
from app.services.charges import ChargeSummary, ChargeUnit


def obligation(status: ChargeStatus, outstanding: str) -> ChargeSummary:
    amount = Decimal("100000.00")
    return ChargeSummary(
        id=1, unit=ChargeUnit(1, "B1-TO2-APTO-104", False), concept=FinancialConcept.PENALTY, description="Ruido",
        billing_period=None, due_date=date(2026, 10, 10), amount=amount, applied_amount=amount - Decimal(outstanding),
        outstanding_amount=Decimal(outstanding), status=status, source_charge_id=None,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(("obligations", "expected"), [
    ([], AccountStatus.UP_TO_DATE),
    ([obligation(ChargeStatus.PAID, "0.00")], AccountStatus.UP_TO_DATE),
    ([obligation(ChargeStatus.PENDING, "100000.00")], AccountStatus.PENDING),
    ([obligation(ChargeStatus.PARTIAL, "40000.00"), obligation(ChargeStatus.PAID, "0.00")], AccountStatus.PENDING),
    ([obligation(ChargeStatus.PENDING, "100000.00"), obligation(ChargeStatus.OVERDUE, "1.00")], AccountStatus.OVERDUE),
    ([obligation(ChargeStatus.OVERDUE, "100000.00")], AccountStatus.OVERDUE),
])
def test_pending_or_en_mora(obligations: list[ChargeSummary], expected: AccountStatus) -> None:
    assert account_status(obligations) == expected
