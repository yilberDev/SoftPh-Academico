import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_health_reports_api_and_database_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_reports_database_unavailable(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def database_down(_session: object) -> bool:
        return False

    monkeypatch.setattr("app.api.v1.health.is_database_available", database_down)

    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "unavailable"}
