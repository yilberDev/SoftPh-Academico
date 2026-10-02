"""HU-016: input, eligibility, token format, email and policy values (no database)."""

import json
import re
from datetime import datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.sessions import generate_session_token, hash_token, hash_well_formed_token
from app.core.validation import error_details
from app.models import User, UserRole, UserStatus
from app.schemas.password_reset import PasswordResetConfirm, PasswordResetRequest, PasswordResetTokenRequest
from app.services import password_reset as service
from app.services.password_reset_email import SUBJECT, build_password_reset_email, reset_link

TOKEN = generate_session_token()
PASSWORD = "una frase nueva y segura 2026"


def details(model: Any, data: dict[str, Any]) -> list[dict[str, str]]:
    with pytest.raises(ValidationError) as info:
        model.model_validate_json(json.dumps(data))
    return error_details(info.value.errors(include_input=False))


# --- Policy values ------------------------------------------------------------------------------


def test_policy_values() -> None:
    assert service.RESET_TTL == timedelta(hours=1)
    assert (service.MAX_REQUESTS_PER_WINDOW, service.REQUEST_WINDOW) == (3, timedelta(hours=1))


# --- Request -----------------------------------------------------------------------------------


def test_request_normalizes_the_email() -> None:
    assert PasswordResetRequest.model_validate({"email": "  Residente@Example.COM "}).email == "residente@example.com"


@pytest.mark.parametrize("extra", ["user_id", "role", "document_number", "token", "unit_id"])
def test_request_only_takes_the_email(extra: str) -> None:
    assert details(PasswordResetRequest, {"email": "a@example.com", extra: "x"}) == [
        {"field": extra, "message": "Este campo no está permitido."}
    ]


@pytest.mark.parametrize(("email", "message"), [("", "El correo electrónico es obligatorio."), ("  ", "El correo electrónico es obligatorio."),
                                                ("a" * 255, "El correo electrónico no puede superar 254 caracteres.")])
def test_request_email_rules(email: str, message: str) -> None:
    assert details(PasswordResetRequest, {"email": email}) == [{"field": "email", "message": message}]


# --- Eligibility --------------------------------------------------------------------------------


def user(**overrides: Any) -> User:
    values = {"role": UserRole.RESIDENTE, "status": UserStatus.ACTIVE, "deleted_at": None, **overrides}
    return User(email="r@example.com", full_name="Laura Gómez", **values)


def test_active_resident_is_eligible() -> None:
    assert service.is_eligible(user())


@pytest.mark.parametrize(
    "overrides",
    [{"role": UserRole.ADMINISTRADOR}, {"status": UserStatus.PENDING_ACTIVATION}, {"status": UserStatus.DISABLED},
     {"deleted_at": datetime(2026, 1, 1)}],
    ids=["administrator", "pending", "disabled", "deleted"],
)
def test_others_are_not_eligible(overrides: dict[str, Any]) -> None:
    assert not service.is_eligible(user(**overrides))
    assert not service.is_eligible(None)


# --- Token -----------------------------------------------------------------------------------------


def test_token_has_256_bits_and_only_its_sha256_is_kept() -> None:
    assert len(TOKEN) == 43 and len({generate_session_token() for _ in range(200)}) == 200
    assert hash_well_formed_token(TOKEN) == hash_token(TOKEN) != TOKEN


@pytest.mark.parametrize("token", ["", "abc", TOKEN + "x", TOKEN[:-1] + "!", f" {TOKEN}"])
def test_malformed_tokens_are_not_hashed(token: str) -> None:
    assert hash_well_formed_token(token) is None


@pytest.mark.parametrize("extra", ["user_id", "email", "role", "document_number"])
def test_verify_only_takes_the_token(extra: str) -> None:
    assert details(PasswordResetTokenRequest, {"token": TOKEN, extra: "x"})[0]["field"] == extra


# --- New password (HU-001 policy) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field", "message"),
    [
        ({"password": "a" * 11, "password_confirmation": "a" * 11}, "password", "La contraseña debe tener entre 12 y 128 caracteres."),
        ({"password": "a" * 129, "password_confirmation": "a" * 129}, "password", "La contraseña debe tener entre 12 y 128 caracteres."),
        ({"password": " " * 12, "password_confirmation": " " * 12}, "password", "La contraseña no puede estar formada solo por espacios."),
        ({"password_confirmation": "otra frase distinta"}, "password_confirmation", "La confirmación no coincide con la contraseña."),
        ({"user_id": 1}, "user_id", "Este campo no está permitido."),
        ({"email": "a@example.com"}, "email", "Este campo no está permitido."),
        ({"role": "ADMINISTRADOR"}, "role", "Este campo no está permitido."),
        ({"resident_unit_id": 1}, "resident_unit_id", "Este campo no está permitido."),
    ],
)
def test_confirm_rules(overrides: dict[str, Any], field: str, message: str) -> None:
    data = {"token": TOKEN, "password": PASSWORD, "password_confirmation": PASSWORD, **overrides}
    assert details(PasswordResetConfirm, data) == [{"field": field, "message": message}]


@pytest.mark.parametrize("length", [12, 128])
def test_password_limits(length: int) -> None:
    assert PasswordResetConfirm.model_validate({"token": TOKEN, "password": "a" * length, "password_confirmation": "a" * length})


def test_secrets_are_not_echoed_in_errors() -> None:
    with pytest.raises(ValidationError) as info:
        PasswordResetConfirm.model_validate({"token": TOKEN, "password": PASSWORD, "password_confirmation": "otra"})
    assert TOKEN not in str(info.value) and PASSWORD not in str(info.value)


# --- Email --------------------------------------------------------------------------------------


def test_reset_email() -> None:
    email = build_password_reset_email(
        to="r@example.com", full_name="Laura Gómez", app_origin="http://localhost:4321/", token=TOKEN,
        expires_at=datetime(2026, 10, 8, 20, 5),
    )
    assert (email.to, email.subject) == ("r@example.com", SUBJECT)
    assert email.body.startswith("Hola, Laura Gómez:")
    assert re.findall(r"https?://\S+", email.body) == [f"http://localhost:4321/reset-password#token={TOKEN}"]
    assert "?token=" not in email.body
    assert "8 de octubre de 2026, 3:05 p. m. (hora de Colombia)" in email.body
    assert "solo puede usarse una vez" in email.body and "puedes ignorar este correo" in email.body
    assert "contraseña:" not in email.body.lower() and hash_token(TOKEN) not in email.body
    assert reset_link("http://x", TOKEN) == f"http://x/reset-password#token={TOKEN}"
