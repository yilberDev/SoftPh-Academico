from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo("America/Bogota")


def utc_now() -> datetime:
    """Current UTC time as a naive datetime, matching the DATETIME columns."""
    return datetime.now(UTC).replace(tzinfo=None)


def bogota_today(now_utc: datetime | None = None) -> date:
    """Calendar date in Bogotá (the date the administrator sees) for a naive UTC instant."""
    moment = now_utc if now_utc is not None else utc_now()
    return moment.replace(tzinfo=UTC).astimezone(BOGOTA).date()
