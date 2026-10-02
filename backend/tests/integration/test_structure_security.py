"""HU-013: access control and CSRF on every structure endpoint.

Every route requires an ADMINISTRADOR session. State-changing routes also
require Origin (checked for the whole router) and the CSRF token.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.sessions import CSRF_HEADER_NAME, derive_csrf_token, generate_session_token
from app.main import app
from app.models import User, UserRole
from tests.integration.conftest import sign_in

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]

READS = ["/blocks", "/blocks/1", "/blocks/1/towers", "/towers/1", "/towers/1/units", "/units/1"]
WRITES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/blocks", {"number": 1}),
    ("PATCH", "/blocks/1", {"number": 2, "version": 1}),
    ("DELETE", "/blocks/1?version=1", None),
    ("POST", "/blocks/1/towers", {"number": 1}),
    ("PATCH", "/towers/1", {"number": 2, "version": 1}),
    ("DELETE", "/towers/1?version=1", None),
    ("POST", "/towers/1/units", {"number": 1}),
    ("PATCH", "/units/1", {"number": 2, "version": 1}),
    ("DELETE", "/units/1?version=1", None),
]
WRITE_IDS = [f"{method} {path}" for method, path, _ in WRITES]


async def send(client: AsyncClient, method: str, path: str, body: Any, headers: dict[str, str]) -> Any:
    return await client.request(method, f"/api/v1{path}", json=body, headers=headers)


def _template(path: str) -> str:
    path = path.split("?")[0]
    for level, name in (("blocks", "block_id"), ("towers", "tower_id"), ("units", "unit_id")):
        path = path.replace(f"/{level}/1", f"/{level}/{{{name}}}")
    return path


def test_every_structure_route_is_covered() -> None:
    # If a route is added, it must be added to READS/WRITES (and so tested here).
    published = {
        (method.upper(), path.removeprefix("/api/v1"))
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(("/api/v1/blocks", "/api/v1/towers", "/api/v1/units"))
        and path != "/api/v1/units/available"  # HU-003 (tests in test_invitation_security.py)
        for method in operations
    }
    covered = {("GET", _template(path)) for path in READS} | {(method, _template(path)) for method, path, _ in WRITES}
    assert published == covered
    assert len(published) == 15


@pytest.mark.parametrize("path", READS)
async def test_reads_require_a_session(client: AsyncClient, path: str) -> None:
    response = await client.get(f"/api/v1{path}")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=WRITE_IDS)
async def test_writes_require_a_session(
    client: AsyncClient, origin_headers: dict[str, str], method: str, path: str, body: Any
) -> None:
    headers = {**origin_headers, CSRF_HEADER_NAME: derive_csrf_token(generate_session_token())}
    response = await send(client, method, path, body, headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.fixture
async def resident_headers(client: AsyncClient, db_session: AsyncSession, make_user: MakeUser) -> dict[str, str]:
    resident = await make_user("resident@example.com", role=UserRole.RESIDENTE, password_hash="not-a-real-hash")
    return await sign_in(client, db_session, resident)


@pytest.mark.parametrize("path", READS)
async def test_reads_are_forbidden_for_residents(client: AsyncClient, resident_headers: dict[str, str], path: str) -> None:
    response = await client.get(f"/api/v1{path}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=WRITE_IDS)
async def test_writes_are_forbidden_for_residents(
    client: AsyncClient, resident_headers: dict[str, str], method: str, path: str, body: Any
) -> None:
    response = await send(client, method, path, body, resident_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=WRITE_IDS)
@pytest.mark.parametrize("csrf", ["missing", "wrong", "other-session"])
async def test_writes_require_the_csrf_token(
    client: AsyncClient, admin_headers: dict[str, str], method: str, path: str, body: Any, csrf: str
) -> None:
    headers = {"Origin": admin_headers["Origin"]}
    if csrf == "wrong":
        headers[CSRF_HEADER_NAME] = "wrong-token"
    elif csrf == "other-session":
        headers[CSRF_HEADER_NAME] = derive_csrf_token(generate_session_token())

    response = await send(client, method, path, body, headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=WRITE_IDS)
@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://127.0.0.1:9999"])
async def test_writes_require_the_public_origin(
    client: AsyncClient, admin_headers: dict[str, str], method: str, path: str, body: Any, origin: str | None
) -> None:
    headers = {CSRF_HEADER_NAME: admin_headers[CSRF_HEADER_NAME]}
    if origin is not None:
        headers["Origin"] = origin

    response = await send(client, method, path, body, headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


async def test_rejected_write_changes_nothing(
    client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    await send(client, "POST", "/blocks", {"number": 1}, {"Origin": admin_headers["Origin"]})
    await send(client, "POST", "/blocks", {"number": 1}, {CSRF_HEADER_NAME: admin_headers[CSRF_HEADER_NAME]})

    assert await db_session.scalar(text("SELECT COUNT(*) FROM blocks")) == 0
    assert await db_session.scalar(text("SELECT COUNT(*) FROM audit_logs")) == 0


async def test_reads_do_not_need_origin_or_csrf(client: AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await client.get("/api/v1/blocks")
    assert response.status_code == 200
    assert response.json() == {"items": [], "child_label_prefix": "B"}


async def test_valid_admin_write_succeeds(client: AsyncClient, admin_headers: dict[str, str]) -> None:
    # Control for the rejections above: same request with Origin + token works.
    response = await send(client, "POST", "/blocks", {"number": 1}, admin_headers)
    assert response.status_code == 201
    assert admin_headers["Origin"] == get_settings().app_origin


async def test_disabled_admin_session_is_rejected(
    client: AsyncClient, db_session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    await db_session.execute(text("UPDATE users SET status = 'DISABLED' WHERE email = 'admin@example.com'"))
    response = await send(client, "POST", "/blocks", {"number": 1}, admin_headers)
    assert response.status_code == 401
