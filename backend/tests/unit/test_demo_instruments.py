"""HU-010: the catalog of fictitious test instruments of the demo gateway (pure: no database, no network)."""

import dataclasses
import inspect
import re
import socket
from typing import Any

import pytest

from app.core.exceptions import FieldValidationError
from app.models import DemoPaymentTransactionStatus as Status
from app.services import demo_instruments
from app.services.demo_instruments import (
    CATALOG,
    INSTRUMENTS,
    RETIRED_MESSAGE,
    UNKNOWN_MESSAGE,
    DemoInstrumentKind,
    DemoInstrumentResolution,
    SimulatedReason,
    get_instrument,
    resolve,
)

EXPECTED = [
    ("TEST_CARD_APPROVE", Status.APPROVED, None),
    ("TEST_CARD_INSUFFICIENT_FUNDS", Status.REJECTED, SimulatedReason.INSUFFICIENT_FUNDS),
    ("TEST_ACCOUNT_TECHNICAL_ERROR", Status.FAILED, SimulatedReason.TECHNICAL_ERROR),
]


def rejected_with(instrument_id: Any) -> str:
    with pytest.raises(FieldValidationError) as error:
        resolve(instrument_id)
    assert error.value.status_code == 422
    [detail] = error.value.details or []
    assert detail["field"] == "instrument_id"
    return str(detail["message"])


def test_the_three_instruments_with_unique_stable_ids() -> None:
    assert [instrument.id for instrument in INSTRUMENTS] == [instrument_id for instrument_id, _, _ in EXPECTED]
    assert list(CATALOG) == [instrument.id for instrument in INSTRUMENTS]  # one entry per id, same order
    assert [instrument.kind for instrument in INSTRUMENTS] == [
        DemoInstrumentKind.CARD, DemoInstrumentKind.CARD, DemoInstrumentKind.ACCOUNT,
    ]


def test_every_instrument_says_it_is_fictitious_and_carries_no_payment_data() -> None:
    for instrument in INSTRUMENTS:
        assert "ficticia" in instrument.label
        assert instrument.description.startswith("Instrumento ficticio de demostración: simula ")
        assert re.search(r"No es una (tarjeta|cuenta) real\.$", instrument.description)
        assert instrument.ending is None or re.fullmatch(r"\d{4}", instrument.ending)  # fictitious last digits only
        for text in (instrument.id, instrument.label, instrument.description):
            assert not re.search(r"\d{5,}", text)  # nothing that looks like a card or account number
            assert not re.search(r"cvv|cvc|clave|contraseña|pin\b|\$|cop\b", text, re.IGNORECASE)
    cards = [instrument for instrument in INSTRUMENTS if instrument.kind == DemoInstrumentKind.CARD]
    assert all(instrument.ending is not None for instrument in cards)


@pytest.mark.parametrize(("instrument_id", "status", "reason"), EXPECTED)
def test_each_instrument_resolves_to_its_simulated_result(instrument_id: str, status: Status,
                                                          reason: SimulatedReason | None) -> None:
    assert resolve(instrument_id) == DemoInstrumentResolution(instrument_id=instrument_id, status=status, reason=reason)
    instrument = get_instrument(instrument_id)
    assert (instrument.result, instrument.reason) == (status, reason)


@pytest.mark.parametrize("instrument_id", [
    "TEST_CARD_UNKNOWN", "", " ", "test_card_approve", " TEST_CARD_APPROVE", "TEST_CARD_APPROVE ",  # exact ids only
    "APPROVED", "REJECTED", "FAILED",  # a status is never an instrument
    "4242424242424242", None, 1, True, ["TEST_CARD_APPROVE"], {"id": "TEST_CARD_APPROVE"},
])
def test_an_unknown_id_is_rejected_on_the_instrument_field(instrument_id: Any) -> None:
    assert rejected_with(instrument_id) == UNKNOWN_MESSAGE


def test_a_retired_id_can_never_be_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_instruments, "RETIRED_INSTRUMENT_IDS", frozenset({"TEST_CARD_WITHDRAWN"}))
    assert rejected_with("TEST_CARD_WITHDRAWN") == RETIRED_MESSAGE
    with pytest.raises(FieldValidationError):
        get_instrument("TEST_CARD_WITHDRAWN")


def test_the_catalog_cannot_be_changed_from_outside() -> None:
    instrument = INSTRUMENTS[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        instrument.result = Status.REJECTED  # type: ignore[misc]
    with pytest.raises(TypeError):
        CATALOG["TEST_CARD_FREE_MONEY"] = instrument  # type: ignore[index]
    with pytest.raises(TypeError):
        del CATALOG["TEST_CARD_APPROVE"]  # type: ignore[attr-defined]
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolve("TEST_CARD_APPROVE").status = Status.APPROVED  # type: ignore[misc]
    assert isinstance(INSTRUMENTS, tuple)
    assert isinstance(demo_instruments.RETIRED_INSTRUMENT_IDS, frozenset)
    assert resolve("TEST_CARD_INSUFFICIENT_FUNDS").status == Status.REJECTED  # unchanged by the attempts


def test_repeated_lookups_give_the_same_answer() -> None:
    for instrument_id, _, _ in EXPECTED:
        assert resolve(instrument_id) == resolve(instrument_id)
        assert get_instrument(instrument_id) is get_instrument(instrument_id) is CATALOG[instrument_id]


def test_resolving_needs_only_the_id_no_database_or_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the catalog must not open a connection")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    for instrument_id, status, _ in EXPECTED:
        assert resolve(instrument_id).status == status
    # Nothing else can steer the result: no amount, resident, obligation, transaction or status.
    assert list(inspect.signature(resolve).parameters) == ["instrument_id"]
    assert list(inspect.signature(get_instrument).parameters) == ["instrument_id"]
    assert not inspect.iscoroutinefunction(resolve)
