"""HU-010: the fictitious test instruments of the internal demo gateway, and what each one simulates.

A catalog, not a payment processor: every instrument is invented for the academic demo (no real
card, account, number, security code or credential exists behind it) and no money moves. The
resident will choose one by its stable id; resolve() says which result the demo gateway simulates
for it, always the same for the same id. It never decides whether a payment can be applied: the
amount, the obligation, the residence, the expiry and the payment itself stay with
services/demo_payments.py, which may still end an "approved" simulation as FAILED.

Ids are stable and never reused. An instrument that is withdrawn leaves the catalog and its id is
listed in RETIRED_INSTRUMENT_IDS for good, so it can never be resolved again. An unknown or
retired id is a FieldValidationError on "instrument_id" (422). Pure: no database, network or
mutable state.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from app.core.exceptions import FieldValidationError
from app.models import DemoPaymentTransactionStatus

FIELD = "instrument_id"
UNKNOWN_MESSAGE = "Selecciona un instrumento de prueba válido."
RETIRED_MESSAGE = "Ese instrumento de prueba ya no está disponible. Selecciona otro."


class DemoInstrumentKind(StrEnum):
    CARD = "CARD"  # a fictitious test card
    ACCOUNT = "ACCOUNT"  # a fictitious test account


class SimulatedReason(StrEnum):
    """Why the demo gateway simulates a result other than an approval."""

    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"  # simulated rejection
    TECHNICAL_ERROR = "TECHNICAL_ERROR"  # simulated processing error


@dataclass(frozen=True, slots=True)
class DemoInstrument:
    id: str
    label: str
    kind: DemoInstrumentKind
    ending: str | None  # fictitious last digits shown to tell the cards apart; never a card number
    result: DemoPaymentTransactionStatus  # what the demo gateway answers: APPROVED, REJECTED or FAILED
    reason: SimulatedReason | None  # None only for an approval
    description: str


@dataclass(frozen=True, slots=True)
class DemoInstrumentResolution:
    instrument_id: str
    status: DemoPaymentTransactionStatus
    reason: SimulatedReason | None


INSTRUMENTS: tuple[DemoInstrument, ...] = (  # the order the checkout will show
    DemoInstrument(
        id="TEST_CARD_APPROVE",
        label="Tarjeta de prueba ficticia terminada en 0001",
        kind=DemoInstrumentKind.CARD,
        ending="0001",
        result=DemoPaymentTransactionStatus.APPROVED,
        reason=None,
        description="Instrumento ficticio de demostración: simula un pago aprobado. No es una tarjeta real.",
    ),
    DemoInstrument(
        id="TEST_CARD_INSUFFICIENT_FUNDS",
        label="Tarjeta de prueba ficticia terminada en 0002",
        kind=DemoInstrumentKind.CARD,
        ending="0002",
        result=DemoPaymentTransactionStatus.REJECTED,
        reason=SimulatedReason.INSUFFICIENT_FUNDS,
        description="Instrumento ficticio de demostración: simula un rechazo por fondos insuficientes. "
                    "No es una tarjeta real.",
    ),
    DemoInstrument(
        id="TEST_ACCOUNT_TECHNICAL_ERROR",
        label="Cuenta de prueba ficticia",
        kind=DemoInstrumentKind.ACCOUNT,
        ending=None,
        result=DemoPaymentTransactionStatus.FAILED,
        reason=SimulatedReason.TECHNICAL_ERROR,
        description="Instrumento ficticio de demostración: simula un error técnico de procesamiento. "
                    "No es una cuenta real.",
    ),
)

# Ids that once existed and were withdrawn: never resolved again and never reused.
RETIRED_INSTRUMENT_IDS: frozenset[str] = frozenset()

CATALOG: Mapping[str, DemoInstrument] = MappingProxyType({instrument.id: instrument for instrument in INSTRUMENTS})

if len(CATALOG) != len(INSTRUMENTS) or not CATALOG.keys().isdisjoint(RETIRED_INSTRUMENT_IDS):
    raise RuntimeError("Demo instrument ids must be unique and never reuse a retired id.")


def get_instrument(instrument_id: Any) -> DemoInstrument:
    """The available instrument with exactly this id (case-sensitive, never trimmed), or a 422."""
    if isinstance(instrument_id, str) and instrument_id in RETIRED_INSTRUMENT_IDS:
        raise FieldValidationError(FIELD, RETIRED_MESSAGE)
    instrument = CATALOG.get(instrument_id) if isinstance(instrument_id, str) else None
    if instrument is None:
        raise FieldValidationError(FIELD, UNKNOWN_MESSAGE)
    return instrument


def resolve(instrument_id: Any) -> DemoInstrumentResolution:
    """What the demo gateway simulates for this instrument; nothing else is taken or decided here."""
    instrument = get_instrument(instrument_id)
    return DemoInstrumentResolution(instrument_id=instrument.id, status=instrument.result, reason=instrument.reason)
