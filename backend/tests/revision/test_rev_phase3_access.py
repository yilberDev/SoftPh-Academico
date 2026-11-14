"""Deep review, phase 3 C/D: route x actor matrix from the OpenAPI, IDOR, sessions and CSRF.

Actors: anonymous, the administrator, resident A (owner of the resources) and resident B.
The routes come from the application's OpenAPI (tests.integration.test_access_control keeps the
policy of each one; a route missing there fails test_every_route_has_an_access_policy). With
REVISION_MATRIX_OUT=<file> the full matrix is written as CSV for the review document.
"""

import csv
import os
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import bogota_today, utc_now
from app.core.config import get_settings
from app.core.sessions import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from app.main import app
from app.models import User, UserSession
from app.schemas.payment import PaymentCreate
from app.services import demo_payments as demo_service
from app.services import feedback as feedback_service
from app.services import payments as payment_service
from app.services.structure import Actor
from tests.integration.conftest import PASSWORD, FakeMailSender
from tests.integration.test_access_control import (
    ACCESS_POLICY,
    Access,
    call,
    open_session,
)
from tests.integration.test_payments_api import charge
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

API = "/api/v1"
UNSAFE = {"POST", "PATCH", "PUT", "DELETE"}


@pytest.fixture
async def world(db_session: AsyncSession, make_user: Any) -> dict[str, Any]:
    admin = await make_user("admin@example.com", password_hash="not-a-real-hash")
    community = Community(db_session, admin.id)
    a = await community.resident("Laura Gómez", "1012345678", (1, 1, 101), email="a@example.com")
    b = await community.resident("Pedro Ruiz", "2012345678", (1, 1, 102), email="b@example.com")
    unit_a = await db_session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": a})
    c1 = await charge(db_session, unit_a, "1000", date(2026, 1, 5))
    c2 = await charge(db_session, unit_a, "1000", date(2026, 2, 5))
    c3 = await charge(db_session, unit_a, "1000", date(2026, 3, 5))
    await db_session.commit()
    manual = await payment_service.register_payment(db_session, PaymentCreate.model_validate({
        "resident_id": a, "amount": "1000", "method": "CASH", "concept": "MONTHLY_FEE",
        "payment_date": bogota_today().isoformat()}), Actor(admin.id, None), mailer=FakeMailSender())
    approved = await demo_service.start_demo_payment(db_session, a, c2, Decimal(1000))
    approved = await demo_service.complete_demo_payment(db_session, a, approved.id, "APPROVED")
    live = await demo_service.start_demo_payment(db_session, a, c3, Decimal(500))
    feedback = await feedback_service.create_feedback(db_session, a, subject="Ruido", message="Hay ruido.",
                                                      category="COMPLAINT", urgency="MEDIUM")
    return {"admin": admin, "a": await db_session.get(User, a), "b": await db_session.get(User, b),
            "ids": {"resident_id": a, "charge_id": c1, "payment_id": approved.payment_id, "manual_payment_id": manual.id,
                    "transaction_id": live.id, "feedback_id": feedback, "unit_id": unit_a}}


def resource_id(route: tuple[str, str], ids: dict[str, int]) -> int:
    path = route[1]
    for name in ("resident_id", "charge_id", "payment_id", "transaction_id", "feedback_id", "unit_id"):
        if "{" + name + "}" in path:
            return ids[name]
    return 1


def expected(level: Access, actor: str, route: tuple[str, str]) -> set[int] | str:
    """The statuses allowed for an actor (a set) or a class ("not-denied")."""
    if level == Access.PUBLIC:
        return "not-denied"
    if actor == "anonymous":
        return {401}
    if level == Access.AUTHENTICATED:
        return {200}
    if level == Access.ADMINISTRADOR:
        return "not-denied" if actor == "admin" else {403}
    # RESIDENTE
    if actor == "admin":
        return {403}
    if actor == "resident_b" and "{" in route[1]:
        return {404}  # IDOR: the resources of resident A look exactly like missing ones
    return "not-denied"


async def test_matrix_route_by_actor(client: AsyncClient, db_session: AsyncSession, world: dict[str, Any]) -> None:
    assert {(m.upper(), p.removeprefix(API)) for p, item in app.openapi()["paths"].items() for m in item} \
        == set(ACCESS_POLICY)
    sessions: dict[str, dict[str, str] | None] = {"anonymous": None}
    tokens: dict[str, str | None] = {"anonymous": None}
    for actor, user in (("admin", world["admin"]), ("resident_a", world["a"]), ("resident_b", world["b"])):
        sessions[actor] = await open_session(client, db_session, user)
        tokens[actor] = client.cookies.get(SESSION_COOKIE_NAME)
    rows, wrong = [], []
    for route, level in sorted(ACCESS_POLICY.items(), key=lambda item: (item[0][1], item[0][0])):
        if route == ("POST", "/auth/logout"):
            continue  # would end the session used by the next routes; covered in the session tests
        for actor in ("anonymous", "admin", "resident_a", "resident_b"):
            client.cookies.clear()
            if tokens[actor]:
                client.cookies.set(SESSION_COOKIE_NAME, tokens[actor])
            headers = sessions[actor] or {"Origin": get_settings().app_origin}
            response = await call(client, route, headers, resource_id(route, world["ids"]))
            allowed = expected(level, actor, route)
            ok = (response.status_code not in (401, 403)) if allowed == "not-denied" \
                else response.status_code in allowed
            rows.append((route[0], route[1], level.value, actor, response.status_code, "ok" if ok else "FAIL"))
            if not ok:
                wrong.append((route, actor, response.status_code, response.text[:200]))
    out = os.environ.get("REVISION_MATRIX_OUT")
    if out:
        with open(out, "w", newline="") as handle:
            csv.writer(handle).writerows([("method", "path", "policy", "actor", "status", "result"), *rows])
    assert wrong == []


async def test_every_write_needs_the_csrf_token_and_the_origin(
    client: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
) -> None:
    admin = await open_session(client, db_session, world["admin"])
    admin_token = client.cookies.get(SESSION_COOKIE_NAME)
    resident = await open_session(client, db_session, world["a"])
    resident_token = client.cookies.get(SESSION_COOKIE_NAME)
    checked = 0
    for route, level in ACCESS_POLICY.items():
        if route[0] not in UNSAFE or level == Access.PUBLIC:
            continue
        token, headers = (resident_token, resident) if level == Access.RESIDENTE else (admin_token, admin)
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE_NAME, token)
        rid = resource_id(route, world["ids"])
        without_token = await call(client, route, {"Origin": headers["Origin"]}, rid)
        foreign = await call(client, route, {**headers, "Origin": "https://evil.example"}, rid)
        no_origin = await call(client, route, {CSRF_HEADER_NAME: headers[CSRF_HEADER_NAME]}, rid)
        for response in (without_token, foreign, no_origin):
            assert (response.status_code, response.json()["error"]["code"]) == (403, "CSRF_FAILED"), route
        checked += 1
    assert checked == sum(1 for (m, _), level in ACCESS_POLICY.items() if m in UNSAFE and level != Access.PUBLIC) == 22


async def test_public_writes_need_the_origin(client: AsyncClient) -> None:
    for route, level in ACCESS_POLICY.items():
        if route[0] in UNSAFE and level == Access.PUBLIC:
            response = await call(client, route, {"Origin": "https://evil.example"})
            assert response.status_code == 403, route


# --- D. Sessions -------------------------------------------------------------------------------------------


async def _set_session_times(db: AsyncSession, user: User, **values: Any) -> None:
    await db.execute(update(UserSession).where(UserSession.user_id == user.id).values(**values))
    await db.commit()


async def test_idle_expiry_at_30_minutes(client: AsyncClient, db_session: AsyncSession, world: dict[str, Any]) -> None:
    await open_session(client, db_session, world["admin"])
    await _set_session_times(db_session, world["admin"], last_seen_at=utc_now() - timedelta(minutes=29))
    assert (await client.get(f"{API}/auth/session")).status_code == 200
    await _set_session_times(db_session, world["admin"], last_seen_at=utc_now() - timedelta(minutes=30, seconds=1))
    assert (await client.get(f"{API}/auth/session")).status_code == 401


async def test_absolute_expiry_at_8_hours(client: AsyncClient, db_session: AsyncSession, world: dict[str, Any]) -> None:
    await open_session(client, db_session, world["admin"])
    await _set_session_times(db_session, world["admin"], expires_at=utc_now() - timedelta(seconds=1),
                             last_seen_at=utc_now())
    assert (await client.get(f"{API}/auth/session")).status_code == 401


async def test_logout_revokes_on_the_server(client: AsyncClient, db_session: AsyncSession, world: dict[str, Any]) -> None:
    headers = await open_session(client, db_session, world["a"])
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert (await client.post(f"{API}/auth/logout", headers=headers)).status_code == 204
    client.cookies.set(SESSION_COOKIE_NAME, token)  # a stolen copy of the cookie
    assert (await client.get(f"{API}/auth/session")).status_code == 401


async def test_deleting_the_account_ends_its_sessions(
    client: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
) -> None:
    await open_session(client, db_session, world["b"])
    token_b = client.cookies.get(SESSION_COOKIE_NAME)
    admin = await open_session(client, db_session, world["admin"])
    detail = (await client.get(f"{API}/admin/residents/{world['b'].id}")).json()
    deleted = await client.delete(f"{API}/admin/residents/{world['b'].id}", headers=admin,
                                  params={"updated_at": detail["updated_at"]})
    assert deleted.status_code == 204
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, token_b)
    assert (await client.get(f"{API}/auth/session")).status_code == 401


async def test_cookie_attributes(client: AsyncClient, make_user: Any, origin_headers: dict[str, str]) -> None:
    await make_user("admin@example.com", password=PASSWORD)
    response = await client.post(f"{API}/auth/login", headers=origin_headers,
                                 json={"email": "admin@example.com", "password": PASSWORD})
    cookie = response.headers["set-cookie"].lower()
    assert response.status_code == 200
    for attribute in ("httponly", "samesite=lax", "path=/", "max-age=28800"):
        assert attribute in cookie
    assert ("secure" in cookie) == get_settings().session_cookie_secure  # false only for local HTTP (M-2)
    assert response.headers["cache-control"] == "no-store"
