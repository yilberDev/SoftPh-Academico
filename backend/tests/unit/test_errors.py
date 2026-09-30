import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_unknown_route_uses_standard_error_format(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "NOT_FOUND", "message": "El recurso solicitado no existe.", "details": []}
    }


async def test_wrong_method_uses_standard_error_format(client: AsyncClient) -> None:
    response = await client.post("/api/v1/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


async def test_api_is_only_served_under_versioned_prefix(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 404
