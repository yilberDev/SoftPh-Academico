from typing import Any

import pytest
from pydantic import ValidationError

from app.core.validation import error_details
from app.schemas.user import AdminCreate, UserRead

VALID: dict[str, Any] = {
    "full_name": "Ana María Pérez",
    "email": "admin@example.com",
    "password": "correct horse battery",
    "password_confirmation": "correct horse battery",
}


def build(**overrides: Any) -> AdminCreate:
    return AdminCreate.model_validate({**VALID, **overrides})


def errors_for(**overrides: Any) -> dict[str, str]:
    with pytest.raises(ValidationError) as exc_info:
        build(**overrides)
    return {d["field"]: d["message"] for d in error_details(exc_info.value.errors())}


def test_valid_data_with_only_required_fields() -> None:
    data = build()

    assert data.full_name == "Ana María Pérez"
    assert data.email == "admin@example.com"
    assert data.document_number is None
    assert data.phone is None


def test_normalizes_email_name_document_and_phone() -> None:
    data = build(
        email="  Admin@Example.COM ",
        full_name="  Ana   María  Pérez ",
        document_number=" ab12345 ",
        phone="+57 300 123-4567",
    )

    assert data.email == "admin@example.com"
    assert data.full_name == "Ana María Pérez"
    assert data.document_number == "AB12345"
    assert data.phone == "+573001234567"


def test_blank_optional_fields_become_none() -> None:
    data = build(document_number="   ", phone="")

    assert data.document_number is None
    assert data.phone is None


@pytest.mark.parametrize("field", ["full_name", "email", "password", "password_confirmation"])
def test_required_fields(field: str) -> None:
    data = {key: value for key, value in VALID.items() if key != field}

    with pytest.raises(ValidationError) as exc_info:
        AdminCreate.model_validate(data)

    assert error_details(exc_info.value.errors()) == [{"field": field, "message": "Este campo es obligatorio."}]


@pytest.mark.parametrize(
    ("overrides", "field", "message"),
    [
        ({"full_name": "   "}, "full_name", "El nombre completo es obligatorio."),
        ({"full_name": "Al"}, "full_name", "El nombre completo debe tener entre 3 y 150 caracteres."),
        ({"full_name": "A" * 151}, "full_name", "El nombre completo debe tener entre 3 y 150 caracteres."),
        ({"full_name": "Ana123"}, "full_name", "El nombre completo solo puede contener letras, espacios, apóstrofos y guiones."),
        ({"email": ""}, "email", "El correo electrónico es obligatorio."),
        ({"email": "no-es-un-correo"}, "email", "Ingresa un correo electrónico válido."),
        ({"email": "josé@example.com"}, "email", "El correo electrónico no puede contener tildes, eñes ni otros caracteres especiales."),
        ({"email": "ａｄｍｉｎ@example.com"}, "email", "El correo electrónico no puede contener tildes, eñes ni otros caracteres especiales."),
        ({"email": "admin@exámple.com"}, "email", "El correo electrónico no puede contener tildes, eñes ni otros caracteres especiales."),
        ({"document_number": "12-34"}, "document_number", "El número de documento debe tener entre 5 y 20 letras o dígitos, sin espacios ni símbolos."),
        ({"phone": "12345"}, "phone", "El teléfono debe tener entre 7 y 15 dígitos, con un «+» inicial opcional."),
    ],
)
def test_field_validation_messages(overrides: dict[str, str], field: str, message: str) -> None:
    assert errors_for(**overrides)[field] == message


def test_accepts_names_with_accents_apostrophes_and_hyphens() -> None:
    assert build(full_name="José O'Neil Gómez-Ruiz").full_name == "José O'Neil Gómez-Ruiz"


@pytest.mark.parametrize("length", [12, 128])
def test_password_length_limits_are_inclusive(length: int) -> None:
    password = "p" * length
    build(password=password, password_confirmation=password)


@pytest.mark.parametrize("length", [11, 129])
def test_password_outside_length_limits_is_rejected(length: int) -> None:
    password = "p" * length

    errors = errors_for(password=password, password_confirmation=password)

    assert errors["password"] == "La contraseña debe tener entre 12 y 128 caracteres."


def test_password_of_only_spaces_is_rejected() -> None:
    errors = errors_for(password=" " * 12, password_confirmation=" " * 12)

    assert errors["password"] == "La contraseña no puede estar formada solo por espacios."


def test_password_equal_to_email_is_rejected() -> None:
    password = "admin.cuenta@example.com"

    errors = errors_for(email=password, password=password.upper(), password_confirmation=password.upper())

    assert errors["password"] == "La contraseña no puede ser igual al correo electrónico."


def test_password_confirmation_must_match() -> None:
    errors = errors_for(password_confirmation="another long password")

    assert errors["password_confirmation"] == "La confirmación no coincide con la contraseña."


def test_no_character_class_rules() -> None:
    # D2: only length rules, no uppercase/digit/symbol requirements.
    password = "solo letras minusculas"
    build(password=password, password_confirmation=password)


def test_validation_errors_never_include_the_password() -> None:
    secret = "secret-password-value"
    with pytest.raises(ValidationError) as exc_info:
        build(email="bad", password=secret, password_confirmation=secret + "x")

    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value.errors(include_input=False))
    assert secret not in repr(error_details(exc_info.value.errors()))


def test_password_is_not_exposed_in_repr() -> None:
    assert VALID["password"] not in repr(build())


def test_user_read_has_no_password_hash_field() -> None:
    assert "password_hash" not in UserRead.model_fields


@pytest.mark.parametrize("email", ["admin@example.com", "  ADMIN@Example.COM ", "Ana.Perez+admin@Sub.Example.co"])
def test_stored_email_is_always_canonical_ascii(email: str) -> None:
    from app.core.emails import normalize_email

    stored = build(email=email).email

    assert stored == normalize_email(stored)
    assert stored.isascii()
