"""HU-003: validation of the invitation form (CA-3, CA-8)."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.validation import error_details
from app.schemas.invitation import InvitationCreate

VALID = {"email": "residente@example.com", "full_name": "Laura Gómez", "unit_id": 15, "validity_days": 7}


def details(data: Any) -> list[dict[str, str]]:
    with pytest.raises(ValidationError) as info:
        InvitationCreate.model_validate_json(json.dumps(data))
    return error_details(info.value.errors(include_input=False))


def test_valid_invitation() -> None:
    data = InvitationCreate.model_validate(VALID)
    assert (data.email, data.full_name, data.unit_id, data.validity_days) == ("residente@example.com", "Laura Gómez", 15, 7)


def test_email_is_canonical_like_hu001() -> None:
    data = InvitationCreate.model_validate({**VALID, "email": "  Residente@Example.COM "})
    assert data.email == "residente@example.com"


def test_name_whitespace_is_collapsed() -> None:
    assert InvitationCreate.model_validate({**VALID, "full_name": "  Laura   Gómez "}).full_name == "Laura Gómez"


@pytest.mark.parametrize(
    ("email", "message"),
    [
        ("", "El correo electrónico es obligatorio."),
        ("sin-arroba", "Ingresa un correo electrónico válido."),
        ("a@b", "Ingresa un correo electrónico válido."),
        ("josé@example.com", "El correo electrónico no puede contener tildes, eñes ni otros caracteres especiales."),
        ("a@example.com\r\nBcc: x@example.com", "Ingresa un correo electrónico válido."),
    ],
)
def test_invalid_email(email: str, message: str) -> None:
    assert details({**VALID, "email": email}) == [{"field": "email", "message": message}]


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("", "El nombre completo es obligatorio."),
        ("Al", "El nombre completo debe tener entre 3 y 150 caracteres."),
        ("A" * 151, "El nombre completo debe tener entre 3 y 150 caracteres."),
        ("Laura <b>", "El nombre completo solo puede contener letras, espacios, apóstrofos y guiones."),
    ],
)
def test_invalid_name(name: str, message: str) -> None:
    assert details({**VALID, "full_name": name}) == [{"field": "full_name", "message": message}]


def test_line_breaks_in_the_name_are_collapsed() -> None:
    # No header injection is possible: the name never keeps a line break.
    assert InvitationCreate.model_validate({**VALID, "full_name": "Laura\r\nGómez"}).full_name == "Laura Gómez"


@pytest.mark.parametrize("days", [1, 30])
def test_validity_limits_are_accepted(days: int) -> None:
    assert InvitationCreate.model_validate({**VALID, "validity_days": days}).validity_days == days


@pytest.mark.parametrize(
    ("days", "message"),
    [
        (0, "La vigencia debe estar entre 1 y 30 días."),
        (31, "La vigencia debe estar entre 1 y 30 días."),
        (-1, "La vigencia debe estar entre 1 y 30 días."),
        ("7", "Debe ser un número entero."),
        (7.0, "Debe ser un número entero."),
        (True, "Debe ser un número entero."),
        (None, "Debe ser un número entero."),
    ],
)
def test_invalid_validity(days: Any, message: str) -> None:
    assert details({**VALID, "validity_days": days}) == [{"field": "validity_days", "message": message}]


@pytest.mark.parametrize(
    ("unit_id", "message"),
    [
        (0, "Selecciona un apartamento válido."),
        (-3, "Selecciona un apartamento válido."),
        (2**63, "Selecciona un apartamento válido."),
        ("15", "Debe ser un número entero."),
        (15.0, "Debe ser un número entero."),
        (None, "Debe ser un número entero."),
    ],
)
def test_invalid_unit_id(unit_id: Any, message: str) -> None:
    assert details({**VALID, "unit_id": unit_id}) == [{"field": "unit_id", "message": message}]


@pytest.mark.parametrize("extra", ["label", "user_id", "status", "token", "expires_at", "role"])
def test_extra_fields_are_rejected(extra: str) -> None:
    assert details({**VALID, extra: "x"}) == [{"field": extra, "message": "Este campo no está permitido."}]


def test_all_fields_are_required() -> None:
    assert {d["field"] for d in details({})} == {"email", "full_name", "unit_id", "validity_days"}
