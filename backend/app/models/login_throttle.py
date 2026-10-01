from datetime import datetime

from sqlalchemy import CHAR, CheckConstraint, Integer, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LoginThrottle(Base):
    """Failed login counter per identifier (HU-002, criterion 3).

    Keyed by the SHA-256 of the normalized email, whether or not an account
    exists, so a lockout never reveals which emails are registered.
    """

    __tablename__ = "login_throttles"
    __table_args__ = (CheckConstraint("failed_attempts >= 0", name="failed_attempts_non_negative"),)

    identifier_hash: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    failed_attempts: Mapped[int] = mapped_column(Integer)
    window_started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    locked_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
    )
