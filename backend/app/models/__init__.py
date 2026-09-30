"""SQLAlchemy models.

Import every model module here so Alembic autogenerate sees it through
Base.metadata. No business models exist yet: each one is added with its HU.
"""

from app.db.base import Base

__all__ = ["Base"]
