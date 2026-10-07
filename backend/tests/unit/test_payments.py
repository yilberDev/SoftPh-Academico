"""HU-004: pure rules (schema, FIFO, status, receipt number, receipt and email)."""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.clock import bogota_today
from app.core.money import format_cop
from app.core.validation import error_details
from app.models.finance import FinancialConcept, PaymentMethod
from app.schemas.payment import PaymentCreate, PaymentListQuery
from app.services.allocation import (
    Allocation,
    PaymentStatus,
    PendingCharge,
    allocate_fifo,
    payment_status,
    receipt_number,
)
from app.services.receipts import ReceiptData, build_receipt_email, build_receipt_pdf, receipt_lines

TODAY = bogota_today()
BASE: dict[str, Any] = {"resident_id": 7, "amount": "150000", "method": "TRANSFER", "concept": "MONTHLY_FEE",
                        "concept_description": None, "payment_date": TODAY.isoformat()}


def errors(**fields: Any) -> list[dict[str, str]]:
    with pytest.raises(ValidationError) as error:
        PaymentCreate.model_validate({**BASE, **fields})
    return error_details(error.value.errors())


# --- Schema --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(150000, "150000"), ("150000", "150000"), ("150000.5", "150000.5"), ("150000.50", "150000.50"),
     (150000.5, "150000.5"), ("0.01", "0.01"), (" 42 ", "42"), ("9999999999.99", "9999999999.99")],
)
def test_valid_amounts_are_exact(raw: Any, expected: str) -> None:
    assert PaymentCreate.model_validate({**BASE, "amount": raw}).amount == Decimal(expected)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (0, "El monto debe ser mayor que cero."), ("0.00", "El monto debe ser mayor que cero."),
        (-1, "El monto debe ser mayor que cero."), ("-150000", "El monto debe ser mayor que cero."),
        ("", "El monto es obligatorio."), (None, "El monto es obligatorio."),
        ("12.345", None), (0.1 + 0.2, None), ("1e3", None), ("150.000,00", None), ("$150000", None), ("abc", None),
        (True, None), ([1], None), ("NaN", None), ("Infinity", None),
        ("10000000000", "El monto no puede superar $ 9.999.999.999,99."),
    ],
)
def test_invalid_amounts_are_rejected_never_rounded(raw: Any, message: str | None) -> None:
    [detail] = errors(amount=raw)
    assert detail["field"] == "amount"
    assert detail["message"] == (message or "El monto debe ser un número con hasta 2 decimales (p. ej., 150000 o 150000.50).")


@pytest.mark.parametrize("method", ["CASH", "CARD", "TRANSFER"])
def test_allowed_methods(method: str) -> None:
    assert PaymentCreate.model_validate({**BASE, "method": method}).method == method


@pytest.mark.parametrize("method", ["cash", "CHECK", "PSE", "", None, 1])
def test_invalid_method(method: Any) -> None:
    assert errors(method=method) == [{"field": "method", "message": "Selecciona un método de pago: efectivo, tarjeta o transferencia."}]


@pytest.mark.parametrize("concept", ["RENT", "monthly_fee", "", None])
def test_invalid_concept(concept: Any) -> None:
    assert errors(concept=concept)[0] == {"field": "concept", "message": "Selecciona un concepto: mensualidad, mora, penalización u otro."}


def test_other_requires_a_description() -> None:
    for description in (None, "", "   "):
        assert errors(concept="OTHER", concept_description=description) == [
            {"field": "concept_description", "message": "Describe el concepto del pago."}
        ]


def test_other_with_description_is_normalized() -> None:
    payment = PaymentCreate.model_validate({**BASE, "concept": "OTHER", "concept_description": "  Arreglo   de puerta "})
    assert (payment.concept, payment.concept_description) == (FinancialConcept.OTHER, "Arreglo de puerta")


def test_description_only_for_other_and_limited() -> None:
    assert errors(concept_description="Algo")[0]["field"] == "concept_description"
    assert errors(concept="OTHER", concept_description="x" * 201) == [
        {"field": "concept_description", "message": "La descripción no puede superar 200 caracteres."}
    ]
    assert PaymentCreate.model_validate({**BASE, "concept_description": "   "}).concept_description is None


@pytest.mark.parametrize("raw", ["2026-13-01", "2026-02-30", "02/10/2026", "2026-1-5", "", None, 1_700_000_000, "2026-10-02T10:00"])
def test_invalid_dates(raw: Any) -> None:
    [detail] = errors(payment_date=raw)
    assert detail["field"] == "payment_date"


def test_future_date_is_rejected_today_is_not() -> None:
    assert errors(payment_date=(TODAY + timedelta(days=1)).isoformat()) == [
        {"field": "payment_date", "message": "La fecha del pago no puede ser posterior a hoy."}
    ]
    assert PaymentCreate.model_validate(BASE).payment_date == TODAY
    assert errors(payment_date="1999-12-31")[0]["message"] == "La fecha del pago no puede ser anterior al 1 de enero de 2000."


def test_bogota_today_is_not_utc_today() -> None:
    # 03:00 UTC on Oct 3 is still Oct 2 in Bogotá (UTC-5).
    assert bogota_today(datetime(2026, 10, 3, 3, 0)) == date(2026, 10, 2)
    assert bogota_today(datetime(2026, 10, 3, 5, 0)) == date(2026, 10, 3)


def test_reference_and_notes_are_optional() -> None:
    # HU-008: omitted, null or blank → None (never invented, never the receipt number).
    for fields in ({}, {"transaction_reference": None, "notes": None}, {"transaction_reference": "   ", "notes": " \r\n\t "}):
        payment = PaymentCreate.model_validate({**BASE, **fields})
        assert (payment.transaction_reference, payment.notes) == (None, None), fields


def test_reference_is_one_trimmed_line_and_notes_keep_their_line_breaks() -> None:
    payment = PaymentCreate.model_validate(
        {**BASE, "transaction_reference": "  TRF \n 2026-10-0042 ", "notes": "  Pagó en la portería.\r\nTrajo el soporte.\t "}
    )
    assert payment.transaction_reference == "TRF 2026-10-0042"
    assert payment.notes == "Pagó en la portería.\nTrajo el soporte."
    assert PaymentCreate.model_validate({**BASE, "transaction_reference": "x" * 100, "notes": "y" * 500}).notes == "y" * 500


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("transaction_reference", "x" * 101, "La referencia de transacción no puede superar 100 caracteres."),
        ("transaction_reference", 42, "La referencia de transacción debe ser un texto."),
        ("transaction_reference", "TRF\x00042", "La referencia de transacción contiene caracteres no válidos."),
        ("notes", "y" * 501, "Las notas no pueden superar 500 caracteres."),
        ("notes", ["nota"], "Las notas deben ser un texto."),
        ("notes", "nota\x07", "Las notas contienen caracteres no válidos."),
    ],
)
def test_invalid_reference_and_notes(field: str, value: Any, message: str) -> None:
    assert errors(**{field: value}) == [{"field": field, "message": message}]


@pytest.mark.parametrize("field", ["unit_id", "registered_at", "receipt_number", "status", "allocations", "credit_amount", "role"])
def test_unknown_fields_are_rejected(field: str) -> None:
    assert errors(**{field: "x"}) == [{"field": field, "message": "Este campo no está permitido."}]


@pytest.mark.parametrize("resident_id", ["7", 7.0, 0, -1, None, True])
def test_resident_id_is_a_strict_positive_integer(resident_id: Any) -> None:
    assert errors(resident_id=resident_id)[0]["field"] == "resident_id"


def test_list_query_whitelists() -> None:
    query = PaymentListQuery()
    assert (query.page, query.page_size, query.sort_by, query.sort_order) == (1, 10, "date", "desc")
    for fields in ({"sort_by": "users.password_hash"}, {"sort_order": "DESC"}, {"page_size": 101},
                   {"date_from": "2026-02-01", "date_to": "2026-01-01"}, {"unknown": 1}):
        with pytest.raises(ValidationError):
            PaymentListQuery.model_validate(fields)


# --- FIFO ------------------------------------------------------------------------------------------------------


def pending(charge_id: int, outstanding: str, due: date, created: datetime | None = None) -> PendingCharge:
    return PendingCharge(charge_id, due, created or datetime(2026, 1, 1), Decimal(outstanding))


JANUARY, LATE, FEBRUARY = pending(1, "100000", date(2026, 1, 5)), pending(2, "20000", date(2026, 1, 20)), pending(3, "100000", date(2026, 2, 5))


def test_fifo_example_150000() -> None:
    result = allocate_fifo(Decimal("150000"), [FEBRUARY, LATE, JANUARY])  # any input order
    assert result.allocations == (Allocation(1, Decimal("100000")), Allocation(2, Decimal("20000")), Allocation(3, Decimal("30000")))
    assert (result.applied, result.credit) == (Decimal("150000"), Decimal("0"))


def test_fifo_excess_250000_is_credit() -> None:
    result = allocate_fifo(Decimal("250000"), [JANUARY, LATE, FEBRUARY])
    assert sum(a.amount for a in result.allocations) == Decimal("220000")
    assert (result.applied, result.credit) == (Decimal("220000"), Decimal("30000"))


def test_fifo_orders_by_due_date_then_created_at_then_id() -> None:
    due = date(2026, 3, 5)
    charges = [
        pending(9, "10", due, datetime(2026, 3, 1, 12, 0, 1)),
        pending(8, "10", due, datetime(2026, 3, 1, 12, 0, 0)),
        pending(7, "10", due, datetime(2026, 3, 1, 12, 0, 0)),
        pending(10, "10", date(2026, 2, 5), datetime(2026, 3, 9)),
    ]
    assert [a.charge_id for a in allocate_fifo(Decimal("40"), charges).allocations] == [10, 7, 8, 9]


def test_fifo_partial_charge_and_no_charges() -> None:
    assert allocate_fifo(Decimal("30.55"), [JANUARY]).allocations == (Allocation(1, Decimal("30.55")),)
    result = allocate_fifo(Decimal("5000"), [])
    assert (result.allocations, result.applied, result.credit) == ((), Decimal("0"), Decimal("5000"))


def test_fifo_skips_covered_charges_and_never_rounds() -> None:
    result = allocate_fifo(Decimal("0.03"), [pending(1, "0", date(2026, 1, 1)), pending(2, "0.01", date(2026, 1, 2)),
                                             pending(3, "0.01", date(2026, 1, 3))])
    assert result.allocations == (Allocation(2, Decimal("0.01")), Allocation(3, Decimal("0.01")))
    assert result.credit == Decimal("0.01")


def test_fifo_rejects_non_positive_amounts() -> None:
    with pytest.raises(ValueError):
        allocate_fifo(Decimal("0"), [JANUARY])


# --- Status and receipt number -----------------------------------------------------------------------------------


def test_status_is_derived() -> None:
    assert payment_status(Decimal("100"), Decimal("100")) == PaymentStatus.APPLIED
    assert payment_status(Decimal("100"), Decimal("99.99")) == PaymentStatus.PARTIALLY_APPLIED
    assert payment_status(Decimal("100"), Decimal("0.01")) == PaymentStatus.PARTIALLY_APPLIED
    assert payment_status(Decimal("100"), Decimal("0")) == PaymentStatus.CREDIT


def test_receipt_number() -> None:
    assert receipt_number(1) == "RC-00000001"
    assert receipt_number(12345678) == "RC-12345678"
    assert receipt_number(123456789) == "RC-123456789"  # never truncated


def test_format_cop() -> None:
    assert [format_cop(Decimal(v)) for v in ("150000", "1234567.5", "0.01", "9999999999.99")] == [
        "$ 150.000,00", "$ 1.234.567,50", "$ 0,01", "$ 9.999.999.999,99"
    ]


# --- Receipt and email -------------------------------------------------------------------------------------------------


RECEIPT = ReceiptData(
    receipt_number="RC-00000042", resident_name="Laura Gómez Ñúñez", unit_label="B1-TO2-APTO-104",
    amount=Decimal("150000.00"), method=PaymentMethod.TRANSFER, concept=FinancialConcept.OTHER,
    concept_description="Arreglo de puerta", payment_date=date(2026, 10, 1), registered_at=datetime(2026, 10, 2, 20, 5),
)


def pdf_text(pdf: bytes) -> str:
    parts = re.findall(rb"\((.*?)(?<!\\)\) Tj", pdf)
    return "\n".join(re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m.group(1), 8)]), p).replace(b"\\(", b"(")
                     .replace(b"\\)", b")").decode("latin-1") for p in parts)


def test_receipt_fields() -> None:
    assert receipt_lines(RECEIPT) == [
        ("Número de recibo", "RC-00000042"),
        ("Residente", "Laura Gómez Ñúñez"),
        ("Unidad", "B1-TO2-APTO-104"),
        ("Concepto", "Otro: Arreglo de puerta"),
        ("Método de pago", "Transferencia"),
        ("Monto", "$ 150.000,00"),
        ("Fecha del pago", "1 de octubre de 2026"),
        ("Fecha y hora de registro", "2 de octubre de 2026, 3:05 p. m. (hora de Colombia)"),
    ]


def test_receipt_pdf() -> None:
    pdf = build_receipt_pdf(RECEIPT)
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    assert pdf == build_receipt_pdf(RECEIPT)  # deterministic
    text = pdf_text(pdf)
    for expected in ("SoftPh-Académico", "Urbanización San Joaquín", "Recibo de pago", "RC-00000042", "Laura Gómez Ñúñez",
                     "B1-TO2-APTO-104", "Otro: Arreglo de puerta", "Transferencia", "$ 150.000,00", "1 de octubre de 2026"):
        assert expected in text, expected


def test_receipt_pdf_tolerates_characters_outside_the_font() -> None:
    other = ReceiptData(**{**RECEIPT.__dict__, "resident_name": "Łukasz Żółć"})
    assert build_receipt_pdf(other).startswith(b"%PDF-")


def test_receipt_email() -> None:
    email = build_receipt_email(to="laura@example.com", data=RECEIPT, pdf=b"%PDF-1.4 test")
    assert email.to == "laura@example.com"
    assert email.subject == "Recibo de pago RC-00000042 — SoftPh-Académico"
    assert "$" not in email.subject and "150" not in email.subject
    assert "RC-00000042" in email.body and "Hola, Laura Gómez Ñúñez:" in email.body
    assert "Otro: Arreglo de puerta" in email.body and "1 de octubre de 2026" in email.body
    [attachment] = email.attachments
    assert (attachment.filename, attachment.content_type, attachment.data) == (
        "recibo-RC-00000042.pdf", "application/pdf", b"%PDF-1.4 test")
    for secret in ("password", "token", "contraseña"):
        assert secret not in email.body.lower()
