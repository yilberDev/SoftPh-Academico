import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient

from app.main import app

pytestmark = pytest.mark.anyio


def test_no_route_creates_users_or_administrators() -> None:
    write_routes = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and route.methods & {"POST", "PUT", "PATCH"}
    ]

    assert write_routes == []


@pytest.mark.parametrize(
    "path",
    ["/api/v1/admins", "/api/v1/administrators", "/api/v1/users", "/api/v1/auth/register", "/api/v1/register"],
)
async def test_registration_paths_do_not_exist(client: AsyncClient, path: str) -> None:
    response = await client.post(path, json={"email": "admin@example.com", "password": "correct horse battery"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
