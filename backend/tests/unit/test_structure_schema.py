"""HU-013: input validation of blocks, towers and apartments."""

import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.core.validation import error_details
from app.schemas.structure import (
    NUMBER_TECHNICAL_MAX,
    BlockUpdate,
    NumberCreate,
    TowerUpdate,
    UnitUpdate,
)


def details(model: type[BaseModel], data: Any) -> list[dict[str, str]]:
    with pytest.raises(ValidationError) as info:
        model.model_validate_json(json.dumps(data))
    return error_details(info.value.errors(include_input=False))


@pytest.mark.parametrize("number", [1, 2, 104, 1104, NUMBER_TECHNICAL_MAX])
def test_positive_integers_are_valid(number: int) -> None:
    assert NumberCreate.model_validate_json(f'{{"number": {number}}}').number == number


@pytest.mark.parametrize(
    ("number", "message"),
    [
        (0, "El número debe ser un entero positivo (1 o mayor)."),
        (-1, "El número debe ser un entero positivo (1 o mayor)."),
        (NUMBER_TECHNICAL_MAX + 1, "El número es demasiado grande para el sistema."),
        ("104", "Debe ser un número entero."),
        (104.0, "Debe ser un número entero."),
        (104.5, "Debe ser un número entero."),
        (True, "Debe ser un número entero."),
        (None, "Debe ser un número entero."),
        ("", "Debe ser un número entero."),
    ],
)
def test_invalid_numbers(number: Any, message: str) -> None:
    assert details(NumberCreate, {"number": number}) == [{"field": "number", "message": message}]


def test_technical_limit_is_not_presented_as_a_domain_rule() -> None:
    [detail] = details(NumberCreate, {"number": NUMBER_TECHNICAL_MAX + 1})
    assert "sistema" in detail["message"]
    assert str(NUMBER_TECHNICAL_MAX) not in detail["message"]


def test_number_is_required() -> None:
    assert details(NumberCreate, {}) == [{"field": "number", "message": "Este campo es obligatorio."}]


def test_unknown_fields_are_rejected() -> None:
    assert details(NumberCreate, {"number": 1, "label": "B1"}) == [
        {"field": "label", "message": "Este campo no está permitido."}
    ]


def test_create_never_accepts_a_parent_in_the_body() -> None:
    # The parent always comes from the URL.
    assert details(NumberCreate, {"number": 1, "block_id": 1})[0]["field"] == "block_id"


def test_update_requires_version() -> None:
    assert details(BlockUpdate, {"number": 4}) == [{"field": "version", "message": "Este campo es obligatorio."}]


@pytest.mark.parametrize("version", [0, -1, "1", 1.0, NUMBER_TECHNICAL_MAX + 1])
def test_invalid_versions(version: Any) -> None:
    [detail] = details(BlockUpdate, {"number": 4, "version": version})
    assert detail["field"] == "version"


def test_valid_update() -> None:
    data = UnitUpdate.model_validate({"number": 105, "version": 3})
    assert (data.number, data.version) == (105, 3)


@pytest.mark.parametrize("value", [2, None, "2"])
def test_tower_cannot_change_block(value: Any) -> None:
    assert details(TowerUpdate, {"number": 2, "version": 1, "block_id": value}) == [
        {"field": "block_id", "message": "El bloque de una torre no se puede cambiar."}
    ]


def test_unit_cannot_change_tower_or_block() -> None:
    assert details(UnitUpdate, {"number": 2, "version": 1, "tower_id": 9, "block_id": 3}) == [
        {"field": "block_id", "message": "El bloque de un apartamento no se puede cambiar."},
        {"field": "tower_id", "message": "La torre de un apartamento no se puede cambiar."},
    ]


def test_block_update_rejects_unknown_fields() -> None:
    assert details(BlockUpdate, {"number": 2, "version": 1, "parent_id": 1})[0]["field"] == "parent_id"


@pytest.mark.parametrize("model", [TowerUpdate, UnitUpdate])
def test_parent_fields_are_not_in_the_public_schema(model: type[BaseModel]) -> None:
    assert set(model.model_json_schema()["properties"]) == {"number", "version"}
