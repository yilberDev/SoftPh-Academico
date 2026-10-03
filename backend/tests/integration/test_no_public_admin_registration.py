from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.sessions import CSRF_HEADER_NAME, derive_csrf_token, generate_session_token
from app.main import app
from app.models import User, UserRole
from tests.integration.conftest import sign_in

pytestmark = pytest.mark.anyio

WRITE_METHODS = {"post", "put", "patch"}
API = "/api/v1"
# The administrative area (HU-004, HU-006): authenticated ADMINISTRADOR operations, never account creation.
ADMIN_AREA = f"{API}/admin/"
ACCOUNT_WORDS = ("user", "admins", "administrator", "register", "signup")


def write_paths() -> dict[str, set[str]]:
    # The OpenAPI schema is the public list of every API route.
    return {
        path: WRITE_METHODS & set(operations)
        for path, operations in app.openapi()["paths"].items()
        if WRITE_METHODS & set(operations)
    }


def test_no_route_creates_users_or_administrators() -> None:
    paths = write_paths()

    assert paths, "the schema should list the existing write routes (login/logout)"
    assert not {path for path in paths if any(word in path for word in ACCOUNT_WORDS)}
    # "admin" only ever appears as the prefix of the administrative area.
    for path in paths:
        rest = path.removeprefix(ADMIN_AREA) if path.startswith(ADMIN_AREA) else path
        assert "admin" not in rest, path


async def test_administrative_area_writes_need_an_administrator_session(
    client: AsyncClient, db_session: AsyncSession, make_user: Callable[..., Awaitable[User]]
) -> None:
    area = {path: methods for path, methods in write_paths().items() if path.startswith(ADMIN_AREA)}
    assert area  # HU-004: POST /admin/payments
    users_before = await db_session.scalar(select(func.count()).select_from(User))
    anonymous = {"Origin": get_settings().app_origin, CSRF_HEADER_NAME: derive_csrf_token(generate_session_token())}
    for path, methods in area.items():
        for method in methods:
            response = await client.request(method.upper(), path, json={}, headers=anonymous)
            assert response.status_code == 401, (method, path)

    resident = await make_user("residente@example.com", role=UserRole.RESIDENTE, password_hash="not-a-real-hash")
    headers = await sign_in(client, db_session, resident)
    for path, methods in area.items():
        for method in methods:
            response = await client.request(method.upper(), path, json={}, headers=headers)
            assert response.status_code == 403, (method, path)
    assert await db_session.scalar(select(func.count()).select_from(User)) == users_before + 1  # only the test resident


@pytest.mark.parametrize(
    "path",
    ["/api/v1/admins", "/api/v1/administrators", "/api/v1/users", "/api/v1/auth/register", "/api/v1/register"],
)
async def test_registration_paths_do_not_exist(client: AsyncClient, path: str) -> None:
    response = await client.post(path, json={"email": "admin@example.com", "password": "correct horse battery"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
