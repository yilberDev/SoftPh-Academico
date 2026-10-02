"""Input and output of the password reset of residents (HU-016).

The identity never comes from the client: the request step only takes an
email, and the next steps only take the one-time token.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationInfo, field_validator

from app.schemas.auth import clean_login_email
from app.schemas.tokens import OneTimeToken
from app.schemas.user import check_password, check_password_confirmation


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: str

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value: Any) -> Any:
        return clean_login_email(value)


class PasswordResetTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    token: OneTimeToken


class PasswordResetConfirm(BaseModel):
    """New password with the HU-001 policy; "different from the email" is checked
    by the service with the email of the token's account."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    token: OneTimeToken
    password: SecretStr
    password_confirmation: SecretStr

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


class PasswordResetAccepted(BaseModel):
    """Always the same answer, whether or not the email belongs to a resident."""

    status: Literal["ACCEPTED"] = "ACCEPTED"


class PasswordResetPreview(BaseModel):
    """A valid link. No user data is needed to show the form."""

    expires_at: datetime  # UTC (timezone-aware)


class PasswordResetCompleted(BaseModel):
    """The password changed and every session was closed. No new session is created."""

    status: Literal["PASSWORD_UPDATED"] = "PASSWORD_UPDATED"
