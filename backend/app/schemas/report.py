"""HU-012: queries and answers of the financial reports (administrator only, read only)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.core.validation import invalid
from app.models import PaymentMethod
from app.schemas.payment import PaymentRead
from app.services.account_statement import AccountStatus
from app.services.financial_reports import PaymentsReport, PendingBalancesReport

DATE_FROM_REQUIRED = "Indica la fecha inicial del reporte."
DATE_TO_REQUIRED = "Indica la fecha final del reporte."
DATE_RANGE_MESSAGE = "La fecha inicial no puede ser posterior a la fecha final."


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


class PaymentsReportQuery(BaseModel):
    """CU-012 steps 4-5: the criteria of the payments report. Both dates are required (5a); unknown: 422.

    A missing or empty date gets its own message next to its field (CU-012 6a: "solicita completar").
    """

    model_config = ConfigDict(extra="forbid")

    date_from: date | None = Field(default=None, validate_default=True)
    date_to: date | None = Field(default=None, validate_default=True)

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def empty_is_missing(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("date_from")
    @classmethod
    def date_from_required(cls, value: date | None) -> date:
        if value is None:
            raise invalid("date_from_required", DATE_FROM_REQUIRED)
        return value

    @field_validator("date_to")
    @classmethod
    def date_to_required(cls, value: date | None, info: ValidationInfo) -> date:
        if value is None:
            raise invalid("date_to_required", DATE_TO_REQUIRED)
        date_from = info.data.get("date_from")
        if date_from is not None and date_from > value:
            raise invalid("date_range", DATE_RANGE_MESSAGE)
        return value

    def dates(self) -> tuple[date, date]:
        """The validated range (both dates are always present once the query is valid)."""
        if self.date_from is None or self.date_to is None:
            raise ValueError("The query was not validated.")
        return self.date_from, self.date_to


class PendingScope(StrEnum):
    ALL = "ALL"  # every apartment with a pending balance
    OVERDUE = "OVERDUE"  # only those en mora


class PendingBalancesQuery(BaseModel):
    """The criteria of the pending balances report: which apartments (all that owe, by default)."""

    model_config = ConfigDict(extra="forbid")

    scope: PendingScope = PendingScope.ALL


class MethodTotalRead(BaseModel):
    method: PaymentMethod
    payments: int
    amount: Decimal


class PaymentsReportRead(BaseModel):
    date_from: date
    date_to: date
    generated_at: datetime  # UTC
    has_data: bool  # False: "no existen datos para los criterios seleccionados" (CU-012 5b)
    items: list[PaymentRead]
    payments: int
    total_amount: Decimal
    total_applied: Decimal
    total_credit: Decimal
    by_method: list[MethodTotalRead]

    @classmethod
    def from_report(cls, report: PaymentsReport) -> Self:
        return cls(
            date_from=report.date_from, date_to=report.date_to, generated_at=_utc(report.generated_at),
            has_data=bool(report.items), items=list(report.items), payments=len(report.items),
            total_amount=report.total_amount, total_applied=report.total_applied, total_credit=report.total_credit,
            by_method=[MethodTotalRead(method=m.method, payments=m.payments, amount=m.amount) for m in report.by_method],
        )


class ReportUnitRead(BaseModel):
    id: int
    label: str
    deleted: bool


class ReportResidentRead(BaseModel):
    id: int  # opens the HU-011 statement: /admin/residents/{id}/account-statement
    full_name: str


class PendingBalanceRead(BaseModel):
    unit: ReportUnitRead
    resident: ReportResidentRead | None  # None: the apartment has no current registered resident
    status: AccountStatus
    pending_balance: Decimal
    overdue_balance: Decimal
    credit_balance: Decimal
    pending_obligations: int
    overdue_obligations: int
    oldest_due_date: date


class PendingBalancesReportRead(BaseModel):
    as_of: date
    generated_at: datetime  # UTC
    scope: PendingScope
    has_data: bool
    items: list[PendingBalanceRead]
    units: int
    overdue_units: int
    total_pending: Decimal
    total_overdue: Decimal

    @classmethod
    def from_report(cls, report: PendingBalancesReport) -> Self:
        return cls(
            as_of=report.as_of, generated_at=_utc(report.generated_at),
            scope=PendingScope.OVERDUE if report.only_overdue else PendingScope.ALL,
            has_data=bool(report.items),
            items=[
                PendingBalanceRead(
                    unit=ReportUnitRead(id=item.unit.id, label=item.unit.label, deleted=item.unit.deleted),
                    resident=(ReportResidentRead(id=item.resident.id, full_name=item.resident.full_name)
                              if item.resident else None),
                    status=item.status, pending_balance=item.pending_balance, overdue_balance=item.overdue_balance,
                    credit_balance=item.credit_balance, pending_obligations=item.pending_obligations,
                    overdue_obligations=item.overdue_obligations, oldest_due_date=item.oldest_due_date,
                )
                for item in report.items
            ],
            units=len(report.items), overdue_units=report.overdue_units,
            total_pending=report.total_pending, total_overdue=report.total_overdue,
        )
