"""HU-019: activation input, token format and user states (no database)."""

import json
from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.sessions import generate_session_token, hash_token
from app.core.validation import error_details
from app.models import User, UserRole, UserStatus
from app.schemas.activation import InvitationPreview, InvitationTokenRequest, ResidentActivation
from app.services.activation import _token_hash, is_pending_resident

TOKEN = generate_session_token()
VALID: dict[str, Any] = {
    "token": TOKEN,
    "document_number": "cc1234567",
    "phone": "+57 300-123-4567",
    "password": "una clave segura 2026",
    "password_confirmation": "una clave segura 2026",
}


def details(model: Any, data: dict[str, Any]) -> list[dict[str, str]]:
    with pytest.raises(ValidationError) as info:
        model.model_validate_json(json.dumps(data))
    return error_details(info.value.errors(include_input=False))


# --- Token ---------------------------------------------------------------------------------


def test_well_formed_token_is_hashed() -> None:
    assert _token_hash(TOKEN) == hash_token(TOKEN)


@pytest.mark.parametrize(
    "token",
    ["", " ", "abc", TOKEN[:-1], TOKEN + "x", TOKEN[:-1] + "!", TOKEN[:-1] + "=", f" {TOKEN}", TOKEN.replace(TOKEN[0], "?", 1)],
)
def test_malformed_token_is_never_hashed(token: str) -> None:
    # Answered like an unknown token (404), without touching the database.
    assert _token_hash(token) is None


@pytest.mark.parametrize("token", [123, None, ["x"], "a" * 513])
def test_token_request_shape(token: Any) -> None:
    assert details(InvitationTokenRequest, {"token": token})[0]["field"] == "token"


def test_token_request_rejects_extra_fields() -> None:
    assert details(InvitationTokenRequest, {"token": TOKEN, "user_id": 1}) == [
        {"field": "user_id", "message": "Este campo no está permitido."}
    ]


def test_token_is_never_echoed_in_validation_errors() -> None:
    with pytest.raises(ValidationError) as info:
        ResidentActivation.model_validate({**VALID, "password_confirmation": "otra"})
    assert TOKEN not in str(info.value)
    assert "una clave segura 2026" not in str(info.value)


# --- Activation input ---------------------------------------------------------------------------


def test_valid_activation_is_normalized_like_hu001() -> None:
    data = ResidentActivation.model_validate(VALID)
    assert (data.document_number, data.phone) == ("CC1234567", "+573001234567")
    assert data.password.get_secret_value() == "una clave segura 2026"


@pytest.mark.parametrize("phone", [None, "", "   "])
def test_phone_is_optional(phone: Any) -> None:
    assert ResidentActivation.model_validate({**VALID, "phone": phone}).phone is None


def test_phone_may_be_omitted() -> None:
    data = {key: value for key, value in VALID.items() if key != "phone"}
    assert ResidentActivation.model_validate(data).phone is None


@pytest.mark.parametrize(
    ("overrides", "field", "message"),
    [
        ({"document_number": ""}, "document_number", "El número de documento es obligatorio."),
        ({"document_number": None}, "document_number", "El número de documento es obligatorio."),
        ({"document_number": "1234"}, "document_number", "El número de documento debe tener entre 5 y 20 letras o dígitos, sin espacios ni símbolos."),
        ({"document_number": "CC 123456"}, "document_number", "El número de documento debe tener entre 5 y 20 letras o dígitos, sin espacios ni símbolos."),
        ({"phone": "123"}, "phone", "El teléfono debe tener entre 7 y 15 dígitos, con un «+» inicial opcional."),
        ({"password": "a" * 11, "password_confirmation": "a" * 11}, "password", "La contraseña debe tener entre 12 y 128 caracteres."),
        ({"password": "a" * 129, "password_confirmation": "a" * 129}, "password", "La contraseña debe tener entre 12 y 128 caracteres."),
        ({"password": "", "password_confirmation": ""}, "password", "La contraseña es obligatoria."),
        ({"password_confirmation": "una clave segura 2027"}, "password_confirmation", "La confirmación no coincide con la contraseña."),
        ({"user_id": 1}, "user_id", "Este campo no está permitido."),
        ({"unit_id": 1}, "unit_id", "Este campo no está permitido."),
        ({"invitation_id": 1}, "invitation_id", "Este campo no está permitido."),
        ({"email": "a@example.com"}, "email", "Este campo no está permitido."),
    ],
)
def test_invalid_activation(overrides: dict[str, Any], field: str, message: str) -> None:
    assert details(ResidentActivation, {**VALID, **overrides}) == [{"field": field, "message": message}]


@pytest.mark.parametrize("length", [12, 128])
def test_password_limits(length: int) -> None:
    password = "a" * length
    data = ResidentActivation.model_validate({**VALID, "password": password, "password_confirmation": password})
    assert len(data.password.get_secret_value()) == length


def test_all_required_fields() -> None:
    assert {d["field"] for d in details(ResidentActivation, {})} == {
        "token", "document_number", "password", "password_confirmation"
    }


def test_preview_has_no_ids_or_secrets() -> None:
    assert set(InvitationPreview.model_fields) == {"email", "full_name", "unit_code", "expires_at"}


# --- User state ------------------------------------------------------------------------------------


def user(**overrides: Any) -> User:
    values = {"role": UserRole.RESIDENTE, "status": UserStatus.PENDING_ACTIVATION, "deleted_at": None, **overrides}
    return User(email="r@example.com", full_name="Laura Gómez", **values)


def test_pending_resident_can_activate() -> None:
    assert is_pending_resident(user())


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": UserStatus.ACTIVE},
        {"status": UserStatus.DISABLED},
        {"deleted_at": datetime(2026, 1, 1)},
        {"role": UserRole.ADMINISTRADOR},
    ],
    ids=["active", "disabled", "deleted", "administrator"],
)
def test_other_users_cannot_activate(overrides: dict[str, Any]) -> None:
    assert not is_pending_resident(user(**overrides))


def test_missing_user_cannot_activate() -> None:
    assert not is_pending_resident(None)
