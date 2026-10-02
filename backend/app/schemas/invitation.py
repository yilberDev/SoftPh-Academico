"""Input and output of resident invitations (HU-003)."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, StrictInt, field_validator

from app.core.emails import normalize_email
from app.core.validation import invalid
from app.models.invitation import VALIDITY_DAYS_MAX, VALIDITY_DAYS_MIN
from app.schemas.user import clean_full_name, clean_new_account_email

# BIGINT ids: anything outside the range cannot be a real unit.
ID_MAX = 2**63 - 1


def _valid_unit_id(value: int) -> int:
    if not 1 <= value <= ID_MAX:
        raise invalid("unit_id_invalid", "Selecciona un apartamento válido.")
    return value


def _valid_validity_days(value: int) -> int:
    if not VALIDITY_DAYS_MIN <= value <= VALIDITY_DAYS_MAX:
        raise invalid(
            "validity_days_range",
            f"La vigencia debe estar entre {VALIDITY_DAYS_MIN} y {VALIDITY_DAYS_MAX} días.",
        )
    return value


# Strict: "15", 15.0 and true are rejected instead of being coerced.
UnitId = Annotated[StrictInt, AfterValidator(_valid_unit_id)]
ValidityDays = Annotated[StrictInt, AfterValidator(_valid_validity_days)]


class InvitationCreate(BaseModel):
    """What the administrator enters to invite a resident.

    The email and the name follow exactly the rules of HU-001 (shared
    validators); availability and duplicates are business rules of the service.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: str
    unit_id: UnitId
    validity_days: ValidityDays

    @field_validator("email", mode="before")
    @classmethod
    def validate_email_address(cls, value: Any) -> Any:
        return clean_new_account_email(value)

    @field_validator("email")
    @classmethod
    def keep_canonical_email(cls, value: str) -> str:
        # EmailStr may rewrite the address; what is stored is always canonical.
        return normalize_email(value)

    @field_validator("full_name", mode="before")
    @classmethod
    def validate_full_name(cls, value: Any) -> Any:
        return clean_full_name(value)


class AvailableUnit(BaseModel):
    """An apartment that can be offered in an invitation. Clients send unit_id; label is only shown."""

    unit_id: int
    label: str
    block_number: int
    tower_number: int
    number: int


class AvailableUnitList(BaseModel):
    items: list[AvailableUnit]


class InvitationUnit(BaseModel):
    id: int
    label: str


class InvitationRead(BaseModel):
    """An invitation as the administrator sees it. Never includes the token or its hash."""

    id: int
    email: str
    full_name: str
    unit: InvitationUnit
    status: str
    delivery_status: str
    send_attempts: int
    validity_days: int
    created_at: datetime  # UTC (timezone-aware)
    expires_at: datetime  # UTC (timezone-aware)


class InvitationList(BaseModel):
    items: list[InvitationRead]
