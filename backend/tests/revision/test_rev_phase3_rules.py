"""Deep review, phase 3 A/B/E: money, dates and text rules, without a database.

IDs (R-xx) are those of docs/revision/revision-profunda-2026-11.md; xfail(strict=True) marks a defect.
"""

import unicodedata
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticCustomError
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

from app.core.clock import bogota_today
from app.core.exceptions import FieldValidationError
from app.core.money import MONEY_MAX
from app.models.finance import FinancialConcept, PaymentMethod
from app.schemas.payment import PaymentCreate
from app.schemas.user import clean_full_name
from app.services.allocation import (
    AvailableCredit,
    ChargeStatus,
    PendingCharge,
    allocate_credit,
    allocate_fifo,
    charge_status,
    parse_period,
)
from app.services.charges import ChargeInput, _validated, monthly_params
from app.services.receipts import ReceiptData, build_receipt_pdf, receipt_lines

T0 = datetime(2026, 1, 1)


def pending(charge_id: int, outstanding: str, due: date) -> PendingCharge:
    return PendingCharge(charge_id, due, T0, Decimal(outstanding))


# --- A. Money ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("amount", ["0", "-1", "0.001", "10.005", str(MONEY_MAX + Decimal("0.01")), "NaN", "Infinity"])
def test_a_invalid_payment_amounts_are_rejected(amount: str) -> None:
    with pytest.raises(ValidationError):
        PaymentCreate.model_validate({"resident_id": 1, "amount": amount, "method": "CASH", "concept": "MONTHLY_FEE",
                                      "payment_date": bogota_today().isoformat()})


@pytest.mark.parametrize("amount", ["0", "-1", "0.001", str(MONEY_MAX + Decimal("0.01"))])
def test_a_invalid_charge_amounts_are_rejected(amount: str) -> None:
    with pytest.raises(FieldValidationError):
        _validated(ChargeInput(1, FinancialConcept.PENALTY, Decimal(amount), date(2026, 1, 1), "Ruido"))


def test_a_money_max_is_accepted_and_never_rounded() -> None:
    data = _validated(ChargeInput(1, FinancialConcept.PENALTY, MONEY_MAX, date(2026, 1, 1), "Ruido"))
    assert data.amount == Decimal("9999999999.99")


def test_a_fifo_partial_and_credit() -> None:
    result = allocate_fifo(Decimal("250.50"), [pending(2, "100.25", date(2026, 2, 1)),
                                               pending(1, "100.25", date(2026, 1, 1))])
    assert [(a.charge_id, a.amount) for a in result.allocations] == [(1, Decimal("100.25")), (2, Decimal("100.25"))]
    assert result.credit == Decimal("50.00")


def test_a_credit_is_applied_oldest_first_and_exactly() -> None:
    credits = [AvailableCredit(10, date(2026, 1, 2), T0, Decimal("0.01")),
               AvailableCredit(9, date(2026, 1, 1), T0, Decimal("99.99"))]
    allocations = allocate_credit(credits, [pending(1, "100.00", date(2026, 1, 1))])
    assert [(a.payment_id, a.amount) for a in allocations] == [(9, Decimal("99.99")), (10, Decimal("0.01"))]


# --- B. Dates (Bogotá) ---------------------------------------------------------------------------------


def test_b_overdue_starts_at_midnight_in_bogota_not_utc() -> None:
    due = date(2026, 1, 5)
    late_evening = datetime(2026, 1, 6, 4, 59)  # 23:59 of the 5th in Bogotá, already the 6th in UTC
    midnight = datetime(2026, 1, 6, 5, 0)  # 00:00 of the 6th in Bogotá
    assert charge_status(Decimal(1), Decimal(0), due, bogota_today(late_evening)) == ChargeStatus.PENDING
    assert charge_status(Decimal(1), Decimal(0), due, bogota_today(midnight)) == ChargeStatus.OVERDUE


def test_b_leap_year_and_month_ends() -> None:
    assert parse_period("2028-02") == date(2028, 2, 1)
    params = monthly_params("2028-02", Decimal(180000), date(2028, 2, 29))
    assert params.due_date == date(2028, 2, 29)
    with pytest.raises(FieldValidationError):
        monthly_params("2026-02", Decimal(180000), date(2026, 1, 31))  # due before the period
    for invalid in ("2026-13", "2026-00", "2026-2", "26-02", "2026-02-01", " 2026-02"):
        with pytest.raises(FieldValidationError):
            monthly_params(invalid, Decimal(1), date(2026, 3, 1))


def test_b_a_payment_dated_tomorrow_in_bogota_is_rejected() -> None:
    tomorrow = date.fromordinal(bogota_today().toordinal() + 1)
    with pytest.raises(ValidationError):
        PaymentCreate.model_validate({"resident_id": 1, "amount": "1", "method": "CASH", "concept": "MONTHLY_FEE",
                                      "payment_date": tomorrow.isoformat()})


def test_b_bogota_today_is_utc_minus_5() -> None:
    assert bogota_today(datetime(2026, 12, 31, 23, 0)) == date(2026, 12, 31)
    assert bogota_today(datetime(2027, 1, 1, 4, 59)) == date(2026, 12, 31)
    assert datetime(2027, 1, 1, 5, 0, tzinfo=UTC)  # no DST in Colombia: the offset is always -05:00


# --- E. Text ---------------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="R-17: an obligation description accepts control characters and line breaks")
def test_r17_charge_description_rejects_control_characters() -> None:
    with pytest.raises(FieldValidationError):
        _validated(ChargeInput(1, FinancialConcept.PENALTY, Decimal(1000), date(2026, 1, 1),
                               "Multa\x00\x1b[31m\nsegunda línea \u202e"))


def test_r17_payment_text_fields_do_reject_them() -> None:
    with pytest.raises(ValidationError):
        PaymentCreate.model_validate({"resident_id": 1, "amount": "1", "method": "CASH", "concept": "MONTHLY_FEE",
                                      "payment_date": "2026-01-01", "transaction_reference": "abc\x00def"})


@pytest.mark.xfail(strict=True, reason="R-18: a name typed in NFD (decomposed accents) is rejected, not normalized")
def test_r18_a_decomposed_name_is_normalized() -> None:
    assert clean_full_name(unicodedata.normalize("NFD", "José Pérez")) == "José Pérez"


def test_e_name_limits() -> None:
    with pytest.raises(PydanticCustomError):
        clean_full_name("Al")  # 1-2 characters are rejected (minimum 3)
    assert clean_full_name("Ñ" * 150) == "Ñ" * 150
    with pytest.raises(PydanticCustomError):
        clean_full_name("Ñ" * 151)
    with pytest.raises(PydanticCustomError):
        clean_full_name("Ana 😀")
    with pytest.raises(PydanticCustomError):
        clean_full_name("<script>alert(1)</script>")


# --- R-19: the manual receipt (HU-004) does not wrap long values: they run off the page ----------------


def _receipt(description: str, name: str = "Ana María Pérez") -> ReceiptData:
    return ReceiptData(receipt_number="RC-00000001", resident_name=name, unit_label="B1-TO1-APTO-101",
                       amount=Decimal(150000), method=PaymentMethod.CASH, concept=FinancialConcept.OTHER,
                       concept_description=description, payment_date=date(2026, 1, 5), registered_at=T0)


@pytest.mark.xfail(strict=True, reason="R-19: build_receipt_pdf draws each value on one line (no wrap)")
def test_r19_every_receipt_value_fits_in_the_page() -> None:
    data = _receipt("Reparación " * 18, name=("Maria " * 25).strip())
    width = LETTER[0]
    value_x = 25 * mm + 55 * mm
    assert build_receipt_pdf(data).startswith(b"%PDF")
    for _, value in receipt_lines(data):
        assert value_x + stringWidth(value, "Helvetica", 11) <= width - 25 * mm, value
