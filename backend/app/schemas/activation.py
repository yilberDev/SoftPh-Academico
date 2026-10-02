"""Input and output of the account activation by the resident (HU-019).

The invitation token is the only proof of identity: the user, the apartment
and the invitation always come from it, never from the request. Raw input is
never echoed in validation errors (token, password).
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, SecretStr, StringConstraints, ValidationInfo, field_validator

from app.core.validation import invalid
from app.schemas.user import check_password, check_password_confirmation, clean_document_number, clean_phone

# A real token has 43 characters; the cap only stops absurd payloads (422).
# The format itself is checked by the service, which answers a malformed token
# exactly like an unknown one (404 INVITATION_INVALID).
InvitationToken = Annotated[str, StringConstraints(strict=True, max_length=512)]


class InvitationTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    token: InvitationToken


class InvitationPreview(BaseModel):
    """What the activation form shows. No ids, token or hashes."""

    email: str
    full_name: str
    unit_code: str
    expires_at: datetime  # UTC (timezone-aware)


class ResidentActivation(BaseModel):
    """What the resident completes. Same rules as HU-001 for document, phone and password.

    "The password differs from the email" needs the invitation email, so the
    service checks it.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    token: InvitationToken
    document_number: str
    phone: str | None = None
    password: SecretStr
    password_confirmation: SecretStr

    @field_validator("document_number", mode="before")
    @classmethod
    def validate_document_number(cls, value: Any) -> Any:
        document = clean_document_number(value)
        if document is None:
            raise invalid("document_number_required", "El número de documento es obligatorio.")
        return document

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, value: Any) -> Any:
        return clean_phone(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        check_password(value, None)
        return value

    @field_validator("password_confirmation")
    @classmethod
    def validate_password_confirmation(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        check_password_confirmation(value, info.data.get("password"))
        return value


class ActivationResult(BaseModel):
    """The account is ACTIVE. No session is created (login is HU-007)."""

    status: Literal["ACTIVATED"] = "ACTIVATED"
