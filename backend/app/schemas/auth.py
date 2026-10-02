from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from app.core.emails import normalize_email
from app.core.validation import invalid
from app.schemas.user import PASSWORD_MAX_LENGTH

EMAIL_MAX_LENGTH = 254


def clean_login_email(value: Any) -> Any:
    """Canonical email of a login-like form: required and within the length limit.

    No registration rules (format, ASCII): an address that matches no account
    simply matches nothing.
    """
    if not isinstance(value, str):
        return value
    email = normalize_email(value)
    if not email:
        raise invalid("email_required", "El correo electrónico es obligatorio.")
    if len(email) > EMAIL_MAX_LENGTH:
        raise invalid("email_too_long", f"El correo electrónico no puede superar {EMAIL_MAX_LENGTH} caracteres.")
    return email


class LoginRequest(BaseModel):
    """Single login form for every role (HU-002).

    Registration rules (email format, minimum length) are not applied here:
    any credential that does not match is simply "invalid credentials".
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    email: str
    password: SecretStr

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value: Any) -> Any:
        return clean_login_email(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()
        if not password:
            raise invalid("password_required", "La contraseña es obligatoria.")
        if len(password) > PASSWORD_MAX_LENGTH:
            raise invalid("password_too_long", f"La contraseña no puede superar {PASSWORD_MAX_LENGTH} caracteres.")
        return value


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str
    role: str


class SessionResponse(BaseModel):
    """Current user plus the CSRF token for state-changing requests."""

    user: AuthenticatedUser
    csrf_token: str
