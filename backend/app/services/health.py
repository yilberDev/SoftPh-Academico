import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def is_database_available(session: AsyncSession) -> bool:
    # Any failure (driver, auth, network) means "unavailable", never a 500.
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Database health check failed: %s", type(exc).__name__)
        return False
    return True
