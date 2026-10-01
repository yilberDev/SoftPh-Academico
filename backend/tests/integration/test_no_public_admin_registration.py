import pytest
from httpx import AsyncClient

from app.main import app

pytestmark = pytest.mark.anyio

WRITE_METHODS = {"post", "put", "patch"}


def test_no_route_creates_users_or_administrators() -> None:
    # The OpenAPI schema is the public list of every API route.
    paths = app.openapi()["paths"]
    write_paths = {path for path, operations in paths.items() if WRITE_METHODS & set(operations)}

    assert write_paths, "the schema should list the existing write routes (login/logout)"
    assert not {path for path in write_paths if any(word in path for word in ("admin", "user", "register", "signup"))}


@pytest.mark.parametrize(
    "path",
    ["/api/v1/admins", "/api/v1/administrators", "/api/v1/users", "/api/v1/auth/register", "/api/v1/register"],
)
async def test_registration_paths_do_not_exist(client: AsyncClient, path: str) -> None:
    response = await client.post(path, json={"email": "admin@example.com", "password": "correct horse battery"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
