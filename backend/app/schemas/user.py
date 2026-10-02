import re
from datetime import datetime
from typing import Any

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, EmailStr, SecretStr, ValidationInfo, field_validator

from app.core.emails import normalize_email
from app.core.validation import invalid

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

# Words of letters (any language) separated by a single space, apostrophe or hyphen.
_FULL_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")
_DOCUMENT_RE = re.compile(r"^[A-Z0-9]{5,20}$")
_PHONE_RE = re.compile(r"^\+?\d{7,15}$")


def _optional_text(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


# --- Rules shared by every form that creates an account (HU-001, HU-003) -------


def clean_full_name(value: Any) -> Any:
    """Collapses whitespace and validates a full name (Pydantic "before" validator)."""
    if not isinstance(value, str):
        return value
    name = " ".join(value.split())
    if not name:
        raise invalid("full_name_required", "El nombre completo es obligatorio.")
    if not 3 <= len(name) <= 150:
        raise invalid("full_name_length", "El nombre completo debe tener entre 3 y 150 caracteres.")
    if not _FULL_NAME_RE.match(name):
        raise invalid("full_name_format", "El nombre completo solo puede contener letras, espacios, apóstrofos y guiones.")
    return name


PASSWORD_EQUALS_EMAIL_MESSAGE = "La contraseña no puede ser igual al correo electrónico."


def check_password(value: SecretStr, email: str | None) -> None:
    """Password policy (HU-001): 12-128 characters, not only spaces, different from the email."""
    password = value.get_secret_value()
    if not password:
        raise invalid("password_required", "La contraseña es obligatoria.")
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise invalid(
            "password_length",
            f"La contraseña debe tener entre {PASSWORD_MIN_LENGTH} y {PASSWORD_MAX_LENGTH} caracteres.",
        )
    if not password.strip():
        raise invalid("password_blank", "La contraseña no puede estar formada solo por espacios.")
    if email and password_equals_email(value, email):
        raise invalid("password_equals_email", PASSWORD_EQUALS_EMAIL_MESSAGE)


def password_equals_email(value: SecretStr, email: str) -> bool:
    return value.get_secret_value().strip().lower() == email


def check_password_confirmation(value: SecretStr, password: SecretStr | None) -> None:
    if password is not None and value.get_secret_value() != password.get_secret_value():
        raise invalid("password_mismatch", "La confirmación no coincide con la contraseña.")


def clean_document_number(value: Any) -> Any:
    """Optional document: stripped, uppercase, 5-20 letters or digits (None when empty)."""
    value = _optional_text(value)
    if value is None or not isinstance(value, str):
        return value
    document = value.upper()
    if not _DOCUMENT_RE.match(document):
        raise invalid("document_number_format", "El número de documento debe tener entre 5 y 20 letras o dígitos, sin espacios ni símbolos.")
    return document


def clean_phone(value: Any) -> Any:
    """Optional phone: spaces and hyphens removed, 7-15 digits with an optional leading '+'."""
    value = _optional_text(value)
    if value is None or not isinstance(value, str):
        return value
    phone = re.sub(r"[\s\-]", "", value)
    if not _PHONE_RE.match(phone):
        raise invalid("phone_format", "El teléfono debe tener entre 7 y 15 dígitos, con un «+» inicial opcional.")
    return phone


def clean_new_account_email(value: Any) -> Any:
    """Canonical (normalize_email), ASCII-only and well-formed email of a new account."""
    if not isinstance(value, str):
        return value
    email = normalize_email(value)
    if not email:
        raise invalid("email_required", "El correo electrónico es obligatorio.")
    # New accounts use ASCII emails only (no Unicode look-alikes).
    if not email.isascii():
        raise invalid(
            "email_ascii",
            "El correo electrónico no puede contener tildes, eñes ni otros caracteres especiales.",
        )
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        raise invalid("email_format", "Ingresa un correo electrónico válido.") from None
    return email


class AdminCreate(BaseModel):
    """Input for creating the first administrator (HU-001)."""

    # Never echo raw input (passwords) in validation error messages.
    model_config = ConfigDict(hide_input_in_errors=True)

    full_name: str
    email: EmailStr
    password: SecretStr
    password_confirmation: SecretStr
    document_number: str | None = None
    phone: str | None = None

    @field_validator("full_name", mode="before")
    @classmethod
    def validate_full_name(cls, value: Any) -> Any:
        return clean_full_name(value)

    @field_validator("email", mode="before")
    @classmethod
    def validate_email_address(cls, value: Any) -> Any:
        return clean_new_account_email(value)

    @field_validator("email")
    @classmethod
    def keep_canonical_email(cls, value: str) -> str:
        # EmailStr may rewrite the address; what is stored is always canonical.
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        check_password(value, info.data.get("email"))
        return value

    @field_validator("password_confirmation")
    @classmethod
    def validate_password_confirmation(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        check_password_confirmation(value, info.data.get("password"))
        return value

    @field_validator("document_number", mode="before")
    @classmethod
    def validate_document_number(cls, value: Any) -> Any:
        return clean_document_number(value)

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, value: Any) -> Any:
        return clean_phone(value)


class UserRead(BaseModel):
    """Public view of a user. Never includes the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    document_number: str | None
    phone: str | None
    role: str
    status: str
    created_at: datetime
