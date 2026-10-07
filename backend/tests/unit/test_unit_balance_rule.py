"""Obligations extension (D10): the balance rule of delete_unit, without a database."""

from decimal import Decimal
from typing import Any

import pytest

from app.core.exceptions import ConflictError, UnitHasBalanceError
from app.schemas.payment import AccountBalance
from app.services import payments as payment_service
from app.services import structure as structure_service

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("pending", "credit", "blocked"),
    [
        ("0.00", "0.00", False),  # no balance (also with a settled history): allowed
        ("0.01", "0.00", True),  # debt
        ("100000.00", "0.00", True),
        ("0.00", "0.01", True),  # credit (saldo a favor)
        ("0.00", "80000.00", True),
        ("50000.00", "30000.00", True),  # both: the same rule
    ],
)
async def test_balance_rule(monkeypatch: pytest.MonkeyPatch, pending: str, credit: str, blocked: bool) -> None:
    seen: list[int] = []

    async def fake_balance(_db: Any, unit_id: int) -> AccountBalance:
        seen.append(unit_id)
        return AccountBalance(pending_balance=Decimal(pending), credit_balance=Decimal(credit))

    # The same derived balance as HU-004: delete_unit never computes its own.
    monkeypatch.setattr(payment_service, "unit_balance", fake_balance)
    assert await structure_service._has_balance(None, 7) is blocked  # type: ignore[arg-type]
    assert seen == [7]


def test_one_error_for_debt_and_credit() -> None:
    error = UnitHasBalanceError("B1-TO2-APTO-104")
    assert isinstance(error, ConflictError)
    assert (error.status_code, error.code) == (409, "UNIT_HAS_BALANCE")
    assert error.message == ("No se puede eliminar B1-TO2-APTO-104 porque tiene obligaciones pendientes o saldo a favor. "
                             "Su saldo debe quedar en cero.")
