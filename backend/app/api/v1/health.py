from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.health import HealthResponse
from app.services.health import is_database_available

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(session: Annotated[AsyncSession, Depends(get_db)]) -> HealthResponse:
    database_ok = await is_database_available(session)
    return HealthResponse(status="ok", database="ok" if database_ok else "unavailable")
