"""Reusable request dependencies: session, roles and CSRF (HU-002; role-based access control of HU-018)."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.csrf import origin_allowed
from app.core.exceptions import CsrfFailedError, ForbiddenError, NotAuthenticatedError
from app.core.sessions import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, csrf_token_matches
from app.db.session import get_db
from app.models import UserRole
from app.services.sessions import AuthContext, resolve_session

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[AsyncSession, Depends(get_db)]

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def client_ip(request: Request) -> str | None:
    # X-Forwarded-For is not trusted until the production proxy is defined.
    return request.client.host if request.client else None


def verify_origin(request: Request, settings: SettingsDep) -> None:
    """Origin/Referer check for every state-changing request (with or without session)."""
    if request.method in UNSAFE_METHODS and not origin_allowed(
        request.headers.get("origin"), request.headers.get("referer"), settings.app_origin
    ):
        raise CsrfFailedError()


async def get_optional_auth(request: Request, db: DbDep, settings: SettingsDep) -> AuthContext | None:
    return await resolve_session(db, request.cookies.get(SESSION_COOKIE_NAME), settings)


async def get_current_auth(auth: Annotated[AuthContext | None, Depends(get_optional_auth)]) -> AuthContext:
    if auth is None:
        raise NotAuthenticatedError()
    return auth


def verify_csrf_token(request: Request, auth: AuthContext) -> None:
    if not csrf_token_matches(auth.token, request.headers.get(CSRF_HEADER_NAME)):
        raise CsrfFailedError()


async def require_csrf(
    request: Request,
    _origin: Annotated[None, Depends(verify_origin)],
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> AuthContext:
    """For state-changing requests with a session: Origin + CSRF token."""
    verify_csrf_token(request, auth)
    return auth


def require_roles(*roles: UserRole) -> Callable[..., Awaitable[AuthContext]]:
    """Dependency factory: 401 without a session, 403 when the role is not allowed."""
    allowed = frozenset(roles)

    async def dependency(auth: Annotated[AuthContext, Depends(get_current_auth)]) -> AuthContext:
        if auth.user.role not in allowed:
            raise ForbiddenError()
        return auth

    return dependency
