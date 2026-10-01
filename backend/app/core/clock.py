from datetime import UTC, datetime


def utc_now() -> datetime:
    """Current UTC time as a naive datetime, matching the DATETIME columns."""
    return datetime.now(UTC).replace(tzinfo=None)
