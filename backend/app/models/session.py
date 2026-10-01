from datetime import datetime

from sqlalchemy import BigInteger, CHAR, ForeignKey, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserSession(Base):
    """Server-side session (HU-002). Only the SHA-256 of the token is stored."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    last_seen_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
