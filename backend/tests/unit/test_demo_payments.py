"""HU-010: the pure rules of the demo payment service (no database) and the HU-004 manual form,
which must keep its three methods now that DEMO_GATEWAY exists."""

import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.exceptions import FieldValidationError
from app.models import DemoPaymentTransaction, PaymentMethod
from app.models.finance import MANUAL_PAYMENT_METHODS
from app.schemas.payment import PaymentCreate
from app.services import demo_payments
from app.services.demo_payments import DEMO_TRANSACTION_TTL, DemoOutcome, is_expired, new_reference


def test_a_transaction_lives_fifteen_minutes() -> None:
    assert DEMO_TRANSACTION_TTL == timedelta(minutes=15)


@pytest.mark.parametrize("amount", [Decimal("0.01"), Decimal("150000"), Decimal("150000.5"), Decimal("9999999999.99")])
def test_valid_amounts_are_exact_with_two_decimals(amount: Decimal) -> None:
    checked = demo_payments._checked_amount(amount)
    assert checked == amount and checked.as_tuple().exponent == -2


@pytest.mark.parametrize("amount", [
    None, 0, 150000, 150000.0, "150000", True,  # nothing, or not an exact Decimal (never a float)
    Decimal("0"), Decimal("-0.01"), Decimal("-150000"),  # zero or negative
    Decimal("0.001"), Decimal("150000.555"),  # more than 2 decimals: rejected, never rounded
    Decimal("10000000000.00"), Decimal("1E+30"),  # beyond DECIMAL(12,2)
    Decimal("NaN"), Decimal("Infinity"),
])
def test_invalid_amounts_are_rejected_on_the_amount_field(amount: Any) -> None:
    with pytest.raises(FieldValidationError) as error:
        demo_payments._checked_amount(amount)
    assert error.value.details is not None and error.value.details[0]["field"] == "amount"


@pytest.mark.parametrize("outcome", ["APPROVED", "REJECTED", "FAILED", DemoOutcome.APPROVED])
def test_the_gateway_outcomes(outcome: Any) -> None:
    assert demo_payments._checked_outcome(outcome) == DemoOutcome(outcome)


@pytest.mark.parametrize("outcome", ["INITIATED", "CANCELLED", "EXPIRED", "approved", " APPROVED", "", None, 1])
def test_any_other_outcome_is_rejected(outcome: Any) -> None:
    with pytest.raises(FieldValidationError) as error:
        demo_payments._checked_outcome(outcome)
    assert error.value.details is not None and error.value.details[0]["field"] == "outcome"


def test_references_are_demo_unique_and_fit_the_payment_reference() -> None:
    references = {new_reference() for _ in range(1000)}
    assert len(references) == 1000
    assert all(re.fullmatch(r"DEMO-[0-9A-F]{16}", reference) for reference in references)


def test_expiry_starts_exactly_at_expires_at() -> None:
    expires = datetime(2026, 10, 9, 20, 15)
    transaction = DemoPaymentTransaction(expires_at=expires)
    assert not is_expired(transaction, expires - timedelta(microseconds=1))
    assert is_expired(transaction, expires)
    assert is_expired(transaction, expires + timedelta(seconds=1))


@pytest.mark.parametrize("resident_id", [None, 0, -1, True, "7", 1.0])
def test_the_resident_is_a_positive_id_from_the_session(resident_id: Any) -> None:
    with pytest.raises(ValueError):
        demo_payments._check_resident_id(resident_id)


# --- HU-004 regression: the manual form keeps its three methods --------------------------------------------


def manual(method: str) -> dict[str, Any]:
    return {"resident_id": 1, "amount": "150000", "method": method, "concept": "MONTHLY_FEE",
            "payment_date": "2026-01-05"}


def test_manual_methods_are_unchanged() -> None:
    assert [m.value for m in MANUAL_PAYMENT_METHODS] == ["CASH", "CARD", "TRANSFER"]
    for method in MANUAL_PAYMENT_METHODS:
        assert PaymentCreate.model_validate(manual(method.value)).method == method


def test_a_manual_payment_never_uses_the_demo_gateway() -> None:
    with pytest.raises(ValidationError) as error:
        PaymentCreate.model_validate(manual(PaymentMethod.DEMO_GATEWAY.value))
    [problem] = error.value.errors()
    assert problem["loc"] == ("method",) and problem["type"] == "method_invalid"
    assert problem["msg"] == "Selecciona un método de pago: efectivo, tarjeta o transferencia."
