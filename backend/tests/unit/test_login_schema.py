import pytest
from pydantic import ValidationError

from app.core.validation import error_details
from app.schemas.auth import LoginRequest
from app.schemas.user import AdminCreate


def details(**data: object) -> dict[str, str]:
    with pytest.raises(ValidationError) as exc_info:
        LoginRequest.model_validate(data)
    return {d["field"]: d["message"] for d in error_details(exc_info.value.errors())}


def test_normalizes_email_like_registration() -> None:
    login = LoginRequest.model_validate({"email": "  Admin@Example.COM ", "password": "x"})
    admin = AdminCreate.model_validate(
        {"full_name": "Ana Pérez", "email": "  Admin@Example.COM ", "password": "p" * 12, "password_confirmation": "p" * 12}
    )

    assert login.email == admin.email == "admin@example.com"


def test_registration_rules_are_not_applied() -> None:
    # A malformed email or a short password is just an invalid credential.
    login = LoginRequest.model_validate({"email": "not-an-email", "password": "short"})

    assert login.email == "not-an-email"


@pytest.mark.parametrize("field", ["email", "password"])
def test_required_fields(field: str) -> None:
    data = {"email": "a@example.com", "password": "secret"}
    del data[field]

    assert details(**data) == {field: "Este campo es obligatorio."}


def test_empty_values_are_rejected() -> None:
    assert details(email="  ", password="") == {
        "email": "El correo electrónico es obligatorio.",
        "password": "La contraseña es obligatoria.",
    }


def test_length_limits() -> None:
    assert details(email="a" * 250 + "@x.co", password="p" * 129) == {
        "email": "El correo electrónico no puede superar 254 caracteres.",
        "password": "La contraseña no puede superar 128 caracteres.",
    }


def test_password_never_appears_in_errors_or_repr() -> None:
    secret = "my-secret-password-" + "x" * 120
    with pytest.raises(ValidationError) as exc_info:
        LoginRequest.model_validate({"email": "", "password": secret})

    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value.errors(include_input=False))
    assert "my-secret" not in repr(LoginRequest.model_validate({"email": "a@b.co", "password": "my-secret"}))
