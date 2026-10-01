"""get_current_auth / require_roles: reusable protection for private routes (base for HU-018).

A test-only app is used so that no demo route is added to the real API.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_auth, require_roles
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.sessions import SESSION_COOKIE_NAME
from app.db.session import get_db
from app.models import User, UserRole
from app.services.sessions import AuthContext, create_session

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]

protected_app = FastAPI()
register_exception_handlers(protected_app)


@protected_app.get("/any-user")
async def any_user(auth: Annotated[AuthContext, Depends(get_current_auth)]) -> dict[str, str]:
    return {"role": auth.user.role}


@protected_app.get("/admin-only")
async def admin_only(auth: Annotated[AuthContext, Depends(require_roles(UserRole.ADMINISTRADOR))]) -> dict[str, str]:
    return {"role": auth.user.role}


@protected_app.get("/resident-only")
async def resident_only(auth: Annotated[AuthContext, Depends(require_roles(UserRole.RESIDENTE))]) -> dict[str, str]:
    return {"role": auth.user.role}


@pytest.fixture
async def protected_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    protected_app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=protected_app), base_url="http://testserver") as client:
        yield client
    protected_app.dependency_overrides.clear()


async def sign_in(client: AsyncClient, db: AsyncSession, user: User) -> None:
    # Residents cannot log in yet (HU-007), so the session is created directly.
    token = await create_session(db, user, get_settings(), utc_now())
    client.cookies.set(SESSION_COOKIE_NAME, token)


@pytest.mark.parametrize("path", ["/any-user", "/admin-only", "/resident-only"])
async def test_without_session_returns_401(protected_client: AsyncClient, path: str) -> None:
    response = await protected_client.get(path)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


async def test_administrator_is_allowed_on_admin_routes(protected_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser) -> None:
    await sign_in(protected_client, db_session, await make_user())

    assert (await protected_client.get("/admin-only")).json() == {"role": "ADMINISTRADOR"}
    assert (await protected_client.get("/any-user")).status_code == 200


async def test_administrator_is_forbidden_on_resident_routes(protected_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser) -> None:
    await sign_in(protected_client, db_session, await make_user())

    response = await protected_client.get("/resident-only")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_resident_is_forbidden_on_admin_routes(protected_client: AsyncClient, db_session: AsyncSession, make_user: MakeUser) -> None:
    await sign_in(protected_client, db_session, await make_user(email="resident@example.com", role=UserRole.RESIDENTE))

    assert (await protected_client.get("/admin-only")).status_code == 403
    assert (await protected_client.get("/resident-only")).json() == {"role": "RESIDENTE"}
