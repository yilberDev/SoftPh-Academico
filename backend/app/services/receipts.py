"""Payment receipt (HU-004 criterion 3): PDF and email, pure composition (no I/O).

HU-010 adds the receipt of an approved demo payment (DemoReceiptData,
build_demo_receipt_pdf): a separate composition, downloaded by the resident and
never emailed. The manual receipt of HU-004 is not changed by it.

The receipt is built only from what the payment stored (snapshots of the
resident's name and the apartment label), so a receipt issued today looks the
same tomorrow even if the resident or the apartment changes. It never
contains passwords, tokens, emails or internal identifiers other than the
receipt number.

Standard PDF fonts (Helvetica, WinAnsi): every Spanish character is covered;
a character outside that set is replaced by the font, never an error.
"""

import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from app.core.money import format_cop
from app.models.finance import FinancialConcept, PaymentMethod
from app.services.invitation_email import _MONTHS, format_bogota, format_long_date
from app.services.mail import Attachment, OutgoingEmail

METHOD_LABELS = {
    PaymentMethod.CASH: "Efectivo",
    PaymentMethod.CARD: "Tarjeta",
    PaymentMethod.TRANSFER: "Transferencia",
    PaymentMethod.DEMO_GATEWAY: "Pasarela de demostración",
}
CONCEPT_LABELS = {
    FinancialConcept.MONTHLY_FEE: "Mensualidad",
    FinancialConcept.LATE_FEE: "Mora",
    FinancialConcept.PENALTY: "Penalización",
    FinancialConcept.OTHER: "Otro",
}
COMMUNITY = "Urbanización San Joaquín"
PRODUCT = "SoftPh-Académico"


@dataclass(frozen=True)
class ReceiptData:
    receipt_number: str
    resident_name: str
    unit_label: str
    amount: Decimal
    method: PaymentMethod
    concept: FinancialConcept
    concept_description: str | None
    payment_date: date
    registered_at: datetime  # naive UTC


def concept_text(concept: FinancialConcept, description: str | None) -> str:
    label = CONCEPT_LABELS[concept]
    return f"{label}: {description}" if description else label


def receipt_lines(data: ReceiptData) -> list[tuple[str, str]]:
    """The fields of the receipt, in order (criterion 3.1 plus number and registration time)."""
    return [
        ("Número de recibo", data.receipt_number),
        ("Residente", data.resident_name),
        ("Unidad", data.unit_label),
        ("Concepto", concept_text(data.concept, data.concept_description)),
        ("Método de pago", METHOD_LABELS[data.method]),
        ("Monto", format_cop(data.amount)),
        ("Fecha del pago", format_long_date(data.payment_date)),
        ("Fecha y hora de registro", format_bogota(data.registered_at)),
    ]


def receipt_filename(receipt_number: str) -> str:
    return f"recibo-{receipt_number}.pdf"


def build_receipt_pdf(data: ReceiptData) -> bytes:
    """One-page PDF. Deterministic for the same data (invariant), uncompressed text."""
    buffer = io.BytesIO()
    pdf = Canvas(buffer, pagesize=LETTER, invariant=1, pageCompression=0)
    pdf.setTitle(f"Recibo de pago {data.receipt_number}")
    pdf.setAuthor(PRODUCT)
    width, height = LETTER
    left, top = 25 * mm, height - 25 * mm

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(left, top, PRODUCT)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(left, top - 7 * mm, COMMUNITY)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawRightString(width - left, top, "Recibo de pago")
    pdf.setFont("Helvetica", 12)
    pdf.drawRightString(width - left, top - 7 * mm, data.receipt_number)
    pdf.setLineWidth(0.5)
    pdf.line(left, top - 12 * mm, width - left, top - 12 * mm)

    y = top - 24 * mm
    for label, value in receipt_lines(data):
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(left, y, label)
        pdf.setFont("Helvetica", 11)
        pdf.drawString(left + 55 * mm, y, value)
        y -= 9 * mm

    pdf.line(left, y + 3 * mm, width - left, y + 3 * mm)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(left, y - 4 * mm, f"Recibo generado por {PRODUCT} para la administración de la {COMMUNITY}.")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def receipt_subject(receipt_number: str) -> str:
    # No amounts or other financial data in the subject.
    return f"Recibo de pago {receipt_number} — {PRODUCT}"


def build_receipt_email(*, to: str, data: ReceiptData, pdf: bytes) -> OutgoingEmail:
    body = (
        f"Hola, {data.resident_name}:\n"
        "\n"
        f"La administración de la {COMMUNITY} registró tu pago. Adjuntamos el recibo "
        f"{data.receipt_number} en formato PDF.\n"
        "\n"
        f"Concepto: {concept_text(data.concept, data.concept_description)}\n"
        f"Fecha del pago: {format_long_date(data.payment_date)}\n"
        "\n"
        "Si tienes dudas sobre este pago, comunícate con la administración.\n"
        "\n"
        f"{PRODUCT}\n"
    )
    return OutgoingEmail(
        to=to,
        subject=receipt_subject(data.receipt_number),
        body=body,
        attachments=(Attachment(receipt_filename(data.receipt_number), "application/pdf", pdf),),
    )


# --- HU-010: receipt of an approved demo payment (downloaded, never emailed) ---------------------------------

DEMO_RECEIPT_TITLE = "Comprobante de pago (demostración)"
DEMO_RECEIPT_STATUS = "Aprobado — aplicado a la obligación"
DEMO_RECEIPT_LEGEND = (
    "Comprobante de una transacción de demostración académica. "
    "No corresponde a un cobro real ni a una entidad o pasarela financiera."
)


@dataclass(frozen=True)
class DemoReceiptData:
    """Everything comes from persisted records: the payment (snapshots, amount, reference, dates),
    its single allocation (applied amount) and the obligation it covered."""

    receipt_number: str
    resident_name: str
    unit_label: str
    concept: FinancialConcept  # the obligation's
    concept_description: str | None
    billing_period: date | None  # monthly fees only (first day of the month)
    due_date: date
    amount: Decimal
    applied_amount: Decimal
    method: PaymentMethod
    reference: str  # DEMO-…, the demo transaction's
    payment_date: date
    registered_at: datetime  # naive UTC


def _month(period: date) -> str:
    return f"{_MONTHS[period.month - 1]} de {period.year}".capitalize()


def demo_receipt_lines(data: DemoReceiptData) -> list[tuple[str, str]]:
    """The fields of the demo receipt, in order (HU-010 criterion 4.2)."""
    lines = [
        ("Número de recibo", data.receipt_number),
        ("Residente", data.resident_name),
        ("Unidad", data.unit_label),
        ("Obligación", concept_text(data.concept, data.concept_description)),
    ]
    if data.billing_period is not None:
        lines.append(("Periodo", _month(data.billing_period)))
    lines += [
        ("Vencimiento", format_long_date(data.due_date)),
        ("Método de pago", METHOD_LABELS[data.method]),
        ("Monto", format_cop(data.amount)),
        ("Monto aplicado", format_cop(data.applied_amount)),
        ("Estado", DEMO_RECEIPT_STATUS),
        ("Referencia de la transacción", data.reference),
        ("Fecha del pago", format_long_date(data.payment_date)),
        ("Fecha y hora de registro", format_bogota(data.registered_at)),
    ]
    return lines


def _wrap(text: str, font: str, size: int, max_width: float) -> list[str]:
    """Lines that fit max_width: split at spaces, and a word longer than the line split by characters."""
    lines: list[str] = []
    for line in simpleSplit(text, font, size, max_width) or [""]:
        while stringWidth(line, font, size) > max_width and len(line) > 1:
            cut = len(line) - 1
            while cut > 1 and stringWidth(line[:cut], font, size) > max_width:
                cut -= 1
            lines.append(line[:cut])
            line = line[cut:]
        lines.append(line)
    return lines


def demo_receipt_filename(receipt_number: str) -> str:
    return f"comprobante-demo-{receipt_number}.pdf"


def build_demo_receipt_pdf(data: DemoReceiptData) -> bytes:
    """One-page PDF with the legend in the body and in the footer. Deterministic for the same data
    (invariant), uncompressed text; long values wrap inside their column."""
    buffer = io.BytesIO()
    pdf = Canvas(buffer, pagesize=LETTER, invariant=1, pageCompression=0)
    pdf.setTitle(f"{DEMO_RECEIPT_TITLE} {data.receipt_number}")
    pdf.setAuthor(PRODUCT)
    width, height = LETTER
    left, top = 25 * mm, height - 25 * mm
    value_x = left + 60 * mm

    def paragraph(text: str, x: float, y: float, font: str, size: int, max_width: float) -> float:
        pdf.setFont(font, size)
        for line in _wrap(text, font, size, max_width):
            pdf.drawString(x, y, line)
            y -= size * 0.45 * mm
        return y

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(left, top, PRODUCT)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(left, top - 7 * mm, COMMUNITY)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawRightString(width - left, top, DEMO_RECEIPT_TITLE)
    pdf.setFont("Helvetica", 12)
    pdf.drawRightString(width - left, top - 7 * mm, data.receipt_number)
    pdf.setLineWidth(0.5)
    pdf.line(left, top - 12 * mm, width - left, top - 12 * mm)

    y = paragraph(DEMO_RECEIPT_LEGEND, left, top - 20 * mm, "Helvetica-Bold", 10, width - 2 * left) - 4 * mm
    for label, value in demo_receipt_lines(data):
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(left, y, label)
        y = min(y - 9 * mm, paragraph(value, value_x, y, "Helvetica", 11, width - left - value_x) - 4 * mm)

    pdf.line(left, y + 3 * mm, width - left, y + 3 * mm)
    paragraph(DEMO_RECEIPT_LEGEND, left, y - 4 * mm, "Helvetica", 9, width - 2 * left)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
