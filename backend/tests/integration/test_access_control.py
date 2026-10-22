"""HU-018: access control by role, on the whole API (direct requests, no UI).

ACCESS_POLICY declares the access level of EVERY published route; a new route
that is not classified here makes test_every_route_has_an_access_policy fail.
The role always comes from the server-side session (cookie → session → user →
status → role), never from the request.

    PUBLIC         no session needed (login, logout, activation, password reset, health)
    AUTHENTICATED  any signed-in role; the data is always the caller's own
    ADMINISTRADOR  require_roles(ADMINISTRADOR): 401 without a session, 403 for a resident
    RESIDENTE      require_roles(RESIDENTE): 401 without a session, 403 for the administrator;
                   the data is always the session's resident (HU-008 payments, HU-009 feedback,
                   HU-010 demo payments: published here because the tests enable the demo gateway)
"""

import re
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.sessions import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, derive_csrf_token, generate_session_token, hash_token
from app.main import app
from app.models import AuditLog, User, UserRole, UserSession, UserStatus
from app.schemas.payment import PaymentCreate
from app.services import feedback as feedback_service
from app.services import payments as payment_service
from app.services.sessions import create_session
from app.services.structure import Actor
from tests.integration.conftest import PASSWORD, FakeMailSender
from tests.integration.test_payments_api import charge
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]
API = "/api/v1"


class Access(StrEnum):
    PUBLIC = "PUBLIC"
    AUTHENTICATED = "AUTHENTICATED"
    ADMINISTRADOR = "ADMINISTRADOR"
    RESIDENTE = "RESIDENTE"


ACCESS_POLICY: dict[tuple[str, str], Access] = {
    ("GET", "/health"): Access.PUBLIC,
    # HU-002 / HU-007: single login; logout only changes something with a valid session.
    ("POST", "/auth/login"): Access.PUBLIC,
    ("POST", "/auth/logout"): Access.PUBLIC,
    ("GET", "/auth/session"): Access.AUTHENTICATED,
    # HU-016: password reset (Origin + one-time token).
    ("POST", "/auth/password-reset/request"): Access.PUBLIC,
    ("POST", "/auth/password-reset/verify"): Access.PUBLIC,
    ("POST", "/auth/password-reset/confirm"): Access.PUBLIC,
    # HU-019: activation (Origin + one-time token).
    ("POST", "/resident-invitations/verify"): Access.PUBLIC,
    ("POST", "/resident-invitations/activate"): Access.PUBLIC,
    # HU-003: invitations.
    ("GET", "/units/available"): Access.ADMINISTRADOR,
    ("GET", "/resident-invitations"): Access.ADMINISTRADOR,
    ("POST", "/resident-invitations"): Access.ADMINISTRADOR,
    ("POST", "/resident-invitations/{invitation_id}/resend"): Access.ADMINISTRADOR,
    # HU-006: registered residents (read-only).
    ("GET", "/admin/residents"): Access.ADMINISTRADOR,
    # HU-004: manual payments.
    ("GET", "/admin/payments"): Access.ADMINISTRADOR,
    ("POST", "/admin/payments"): Access.ADMINISTRADOR,
    # HU-005: feedback management.
    ("GET", "/admin/feedback"): Access.ADMINISTRADOR,
    ("GET", "/admin/feedback/{feedback_id}"): Access.ADMINISTRADOR,
    ("POST", "/admin/feedback/{feedback_id}/response"): Access.ADMINISTRADOR,
    ("POST", "/admin/feedback/{feedback_id}/resolve"): Access.ADMINISTRADOR,
    # Obligations extension: charges of the apartments.
    ("GET", "/admin/charges"): Access.ADMINISTRADOR,
    ("POST", "/admin/charges"): Access.ADMINISTRADOR,
    ("GET", "/admin/charges/{charge_id}"): Access.ADMINISTRADOR,
    ("GET", "/admin/charges/monthly/preview"): Access.ADMINISTRADOR,
    ("POST", "/admin/charges/monthly"): Access.ADMINISTRADOR,
    # HU-008: the resident's own payments (read only; the resident is the session's).
    ("GET", "/resident/payments"): Access.RESIDENTE,
    ("GET", "/resident/payments/{payment_id}"): Access.RESIDENTE,
    ("GET", "/resident/payments/{payment_id}/receipt"): Access.RESIDENTE,  # HU-010: receipt of a demo payment
    # HU-009: the resident's own feedback (the resident is the session's).
    ("POST", "/resident/feedback"): Access.RESIDENTE,
    ("GET", "/resident/feedback"): Access.RESIDENTE,
    ("GET", "/resident/feedback/{feedback_id}"): Access.RESIDENTE,
    # HU-010: the resident's demo payment transactions (only with DEMO_PAYMENTS_ENABLED; the resident is the session's).
    ("POST", "/resident/demo-payments"): Access.RESIDENTE,
    ("GET", "/resident/demo-payments/obligations"): Access.RESIDENTE,  # HU-010-D: obligations it can pay
    ("GET", "/resident/demo-payments/instruments"): Access.RESIDENTE,  # the fictitious test instruments
    ("GET", "/resident/demo-payments/{transaction_id}"): Access.RESIDENTE,
    ("POST", "/resident/demo-payments/{transaction_id}/complete"): Access.RESIDENTE,
    ("POST", "/resident/demo-payments/{transaction_id}/cancel"): Access.RESIDENTE,
    # HU-013: physical structure.
    ("GET", "/blocks"): Access.ADMINISTRADOR,
    ("POST", "/blocks"): Access.ADMINISTRADOR,
    ("GET", "/blocks/{block_id}"): Access.ADMINISTRADOR,
    ("PATCH", "/blocks/{block_id}"): Access.ADMINISTRADOR,
    ("DELETE", "/blocks/{block_id}"): Access.ADMINISTRADOR,
    ("GET", "/blocks/{block_id}/towers"): Access.ADMINISTRADOR,
    ("POST", "/blocks/{block_id}/towers"): Access.ADMINISTRADOR,
    ("GET", "/towers/{tower_id}"): Access.ADMINISTRADOR,
    ("PATCH", "/towers/{tower_id}"): Access.ADMINISTRADOR,
    ("DELETE", "/towers/{tower_id}"): Access.ADMINISTRADOR,
    ("GET", "/towers/{tower_id}/units"): Access.ADMINISTRADOR,
    ("POST", "/towers/{tower_id}/units"): Access.ADMINISTRADOR,
    ("GET", "/units/{unit_id}"): Access.ADMINISTRADOR,
    ("PATCH", "/units/{unit_id}"): Access.ADMINISTRADOR,
    ("DELETE", "/units/{unit_id}"): Access.ADMINISTRADOR,
}

ROUTES_OF = {level: sorted(r for r, a in ACCESS_POLICY.items() if a == level) for level in Access}
PRIVATE = ROUTES_OF[Access.AUTHENTICATED] + ROUTES_OF[Access.ADMINISTRADOR] + ROUTES_OF[Access.RESIDENTE]

# A complete, valid request for each private route (ids are replaced by real ones where needed).
BODIES: dict[tuple[str, str], Any] = {
    ("POST", "/admin/feedback/{feedback_id}/response"): {"response": "Revisaremos el caso.", "version": 1},
    ("POST", "/admin/feedback/{feedback_id}/resolve"): {"resolution_note": "Caso gestionado.", "version": 1},
    ("POST", "/admin/payments"): {
        "resident_id": 1, "amount": "150000", "method": "CASH", "concept": "MONTHLY_FEE", "payment_date": "2026-01-05",
    },
    ("POST", "/admin/charges"): {"unit_id": 1, "concept": "PENALTY", "amount": "50000", "due_date": "2026-10-20",
                                 "description": "Ruido"},
    ("POST", "/admin/charges/monthly"): {"billing_period": "2026-10", "amount": "180000", "due_date": "2026-10-10",
                                         "expected_to_create": 0, "preview_fingerprint": "0" * 64},
    ("POST", "/resident/feedback"): {"subject": "Ruido", "message": "Hay ruido.", "category": "COMPLAINT", "urgency": "HIGH"},
    ("POST", "/resident/demo-payments"): {"charge_id": 1, "amount": "1000"},
    ("POST", "/resident/demo-payments/{transaction_id}/complete"): {"instrument_id": "TEST_CARD_APPROVE"},
    ("POST", "/blocks"): {"number": 1},
    ("PATCH", "/blocks/{block_id}"): {"number": 2, "version": 1},
    ("POST", "/blocks/{block_id}/towers"): {"number": 1},
    ("PATCH", "/towers/{tower_id}"): {"number": 2, "version": 1},
    ("POST", "/towers/{tower_id}/units"): {"number": 101},
    ("PATCH", "/units/{unit_id}"): {"number": 102, "version": 1},
    ("POST", "/resident-invitations"): {
        "email": "nueva@example.com", "full_name": "Laura Gómez", "unit_id": 1, "validity_days": 7,
    },
}
QUERIES: dict[tuple[str, str], str] = {
    ("GET", "/resident-invitations"): "?status=PENDING",
    ("GET", "/admin/charges/monthly/preview"): "?billing_period=2026-10&amount=180000&due_date=2026-10-10",
    ("DELETE", "/blocks/{block_id}"): "?version=1",
    ("DELETE", "/towers/{tower_id}"): "?version=1",
    ("DELETE", "/units/{unit_id}"): "?version=1",
}


def concrete(route: tuple[str, str], resource_id: int | str = 1) -> str:
    return re.sub(r"\{[^}]+\}", str(resource_id), route[1]) + QUERIES.get(route, "")


def ids(routes: list[tuple[str, str]]) -> list[str]:
    return [f"{method} {path}" for method, path in routes]


async def call(client: AsyncClient, route: tuple[str, str], headers: dict[str, str], resource_id: int | str = 1) -> Response:
    return await client.request(route[0], f"{API}{concrete(route, resource_id)}", json=BODIES.get(route), headers=headers)


async def open_session(client: AsyncClient, db: AsyncSession, user: User) -> dict[str, str]:
    """Session cookie for the user (committed, so a service rollback keeps it) + write headers."""
    token = await create_session(db, user, get_settings(), utc_now())
    await db.commit()
    client.cookies.set(SESSION_COOKIE_NAME, token)
    return {"Origin": get_settings().app_origin, CSRF_HEADER_NAME: derive_csrf_token(token)}


async def count(db: AsyncSession, table: str) -> int:
    return int(await db.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)


@pytest.fixture
async def admin(make_user: MakeUser) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
async def resident(make_user: MakeUser) -> User:
    return await make_user("residente@example.com", role=UserRole.RESIDENTE, password_hash="not-a-real-hash")


# --- The policy covers every route ----------------------------------------------------------------


def test_every_route_has_an_access_policy() -> None:
    published = {
        (method.upper(), path.removeprefix(API))
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    assert published == set(ACCESS_POLICY), "classify every new route in ACCESS_POLICY (HU-018)"
    assert set(BODIES) | set(QUERIES) <= set(ACCESS_POLICY)


def test_only_the_two_academic_roles_exist() -> None:
    assert {role.value for role in UserRole} == {"ADMINISTRADOR", "RESIDENTE"}


# --- Criterion 3: no session → 401 on every private route; public routes stay public ---------------


@pytest.mark.parametrize("route", PRIVATE, ids=ids(PRIVATE))
async def test_private_routes_require_a_session(client: AsyncClient, route: tuple[str, str]) -> None:
    # Even with a valid Origin and a well-formed (but foreign) CSRF token.
    headers = {"Origin": get_settings().app_origin, CSRF_HEADER_NAME: derive_csrf_token(generate_session_token())}

    response = await call(client, route, headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


PUBLIC_REQUESTS: list[tuple[tuple[str, str], Any, int]] = [
    (("GET", "/health"), None, 200),
    (("POST", "/auth/login"), {"email": "nadie@example.com", "password": "una clave cualquiera"}, 401),
    (("POST", "/auth/logout"), None, 204),
    (("POST", "/auth/password-reset/request"), {"email": "nadie@example.com"}, 202),
    (("POST", "/auth/password-reset/verify"), {"token": "x" * 43}, 404),
    (("POST", "/auth/password-reset/confirm"), {"token": "x" * 43, "password": "una clave segura 2026", "password_confirmation": "una clave segura 2026"}, 404),
    (("POST", "/resident-invitations/verify"), {"token": "x" * 43}, 404),
    (("POST", "/resident-invitations/activate"), {"token": "x" * 43, "password": "una clave segura 2026", "password_confirmation": "una clave segura 2026", "document_number": "1234567890"}, 404),
]


def test_public_requests_cover_every_public_route() -> None:
    assert sorted(route for route, _, _ in PUBLIC_REQUESTS) == ROUTES_OF[Access.PUBLIC]


@pytest.mark.parametrize(("route", "body", "expected"), PUBLIC_REQUESTS, ids=ids([r for r, _, _ in PUBLIC_REQUESTS]))
async def test_public_routes_never_ask_for_a_session(
    client: AsyncClient, origin_headers: dict[str, str], route: tuple[str, str], body: Any, expected: int
) -> None:
    response = await client.request(route[0], f"{API}{route[1]}", json=body, headers=origin_headers)

    assert response.status_code == expected
    if response.status_code >= 400:
        assert response.json()["error"]["code"] != "NOT_AUTHENTICATED"


# --- Criterion 1: the administrator reaches every administrative function --------------------------


async def test_administrator_reaches_every_administrator_route(
    client: AsyncClient, db_session: AsyncSession, admin: User, mailer: FakeMailSender
) -> None:
    headers = await open_session(client, db_session, admin)
    visited: set[tuple[str, str]] = set()

    async def ok(method: str, template: str, path: str, expected: int, body: Any = None) -> Any:
        response = await client.request(method, f"{API}{path}", json=body, headers=headers)
        assert response.status_code == expected, (method, path, response.text)
        visited.add((method, template))
        return response.json() if response.content else None

    block = await ok("POST", "/blocks", "/blocks", 201, {"number": 1})
    await ok("GET", "/blocks", "/blocks", 200)
    await ok("GET", "/blocks/{block_id}", f"/blocks/{block['id']}", 200)
    block = await ok("PATCH", "/blocks/{block_id}", f"/blocks/{block['id']}", 200, {"number": 2, "version": block["version"]})
    tower = await ok("POST", "/blocks/{block_id}/towers", f"/blocks/{block['id']}/towers", 201, {"number": 1})
    await ok("GET", "/blocks/{block_id}/towers", f"/blocks/{block['id']}/towers", 200)
    await ok("GET", "/towers/{tower_id}", f"/towers/{tower['id']}", 200)
    tower = await ok("PATCH", "/towers/{tower_id}", f"/towers/{tower['id']}", 200, {"number": 3, "version": tower["version"]})
    unit = await ok("POST", "/towers/{tower_id}/units", f"/towers/{tower['id']}/units", 201, {"number": 101})
    await ok("GET", "/towers/{tower_id}/units", f"/towers/{tower['id']}/units", 200)
    await ok("GET", "/units/{unit_id}", f"/units/{unit['id']}", 200)
    unit = await ok("PATCH", "/units/{unit_id}", f"/units/{unit['id']}", 200, {"number": 102, "version": unit["version"]})
    await ok("GET", "/units/available", "/units/available", 200)
    invitation = await ok("POST", "/resident-invitations", "/resident-invitations", 201, {
        "email": "laura@example.com", "full_name": "Laura Gómez", "unit_id": unit["id"], "validity_days": 7,
    })
    await ok("GET", "/resident-invitations", "/resident-invitations?status=PENDING", 200)
    await ok("POST", "/resident-invitations/{invitation_id}/resend", f"/resident-invitations/{invitation['id']}/resend", 200)
    await ok("GET", "/admin/residents", "/admin/residents?search=Laura&status=ACTIVE&sort_by=unit&page=1&page_size=10", 200)
    # The invited resident activates the account (HU-019) with the link of the email, then receives a payment.
    token = re.search(r"#token=([A-Za-z0-9_-]+)", mailer.sent[-1].body).group(1)  # type: ignore[union-attr]
    activated = await client.post(f"{API}/resident-invitations/activate", headers={"Origin": headers["Origin"]}, json={
        "token": token, "document_number": "1012345678", "password": "una clave segura 2026",
        "password_confirmation": "una clave segura 2026"})
    assert activated.status_code == 200, activated.text
    resident_id = (await client.get(f"{API}/admin/residents")).json()["items"][0]["id"]
    await ok("POST", "/admin/payments", "/admin/payments", 201, {
        "resident_id": resident_id, "amount": "150000", "method": "CASH", "concept": "MONTHLY_FEE",
        "payment_date": "2026-01-05"})
    await ok("GET", "/admin/payments", "/admin/payments?date_from=2026-01-01&sort_by=amount&sort_order=asc", 200)
    # HU-005: a feedback of that resident (HU-009 will create them; here, the domain function).
    feedback_id = await feedback_service.create_feedback(db_session, resident_id, subject="Ruido", message="Hay ruido.",
                                                         category="COMPLAINT", urgency="MEDIUM")
    await ok("GET", "/admin/feedback", "/admin/feedback?status=PENDING&search=Ruido&sort_by=resident&sort_order=asc", 200)
    await ok("GET", "/admin/feedback/{feedback_id}", f"/admin/feedback/{feedback_id}", 200)
    await ok("POST", "/admin/feedback/{feedback_id}/response", f"/admin/feedback/{feedback_id}/response", 200,
             {"response": "Revisaremos el caso.", "version": 1})
    await ok("POST", "/admin/feedback/{feedback_id}/resolve", f"/admin/feedback/{feedback_id}/resolve", 200,
             {"resolution_note": "Caso gestionado.", "version": 2})
    # Obligations: preview and generation of a monthly fee, one obligation, list and detail.
    preview = await ok("GET", "/admin/charges/monthly/preview", "/admin/charges/monthly/preview?billing_period=2026-10"
                       "&amount=180000&due_date=2026-10-10", 200)
    await ok("POST", "/admin/charges/monthly", "/admin/charges/monthly", 200, {
        "billing_period": "2026-10", "amount": "180000", "due_date": "2026-10-10",
        "expected_to_create": preview["to_create"], "preview_fingerprint": preview["preview_fingerprint"]})
    charge = await ok("POST", "/admin/charges", "/admin/charges", 201, {
        "unit_id": unit["id"], "concept": "PENALTY", "amount": "50000", "due_date": "2026-10-20", "description": "Ruido"})
    await ok("GET", "/admin/charges", "/admin/charges?status=PENDING&sort_by=unit&sort_order=desc&page=1&page_size=10", 200)
    await ok("GET", "/admin/charges/{charge_id}", f"/admin/charges/{charge['id']}", 200)
    spare = await ok("POST", "/towers/{tower_id}/units", f"/towers/{tower['id']}/units", 201, {"number": 103})
    await ok("DELETE", "/units/{unit_id}", f"/units/{spare['id']}?version={spare['version']}", 204)
    other_block = await ok("POST", "/blocks", "/blocks", 201, {"number": 9})
    other_tower = await ok("POST", "/blocks/{block_id}/towers", f"/blocks/{other_block['id']}/towers", 201, {"number": 1})
    await ok("DELETE", "/towers/{tower_id}", f"/towers/{other_tower['id']}?version={other_tower['version']}", 204)
    await ok("DELETE", "/blocks/{block_id}", f"/blocks/{other_block['id']}?version={other_block['version']}", 204)

    assert sorted(visited) == ROUTES_OF[Access.ADMINISTRADOR]
    assert len(mailer.sent) == 5  # invitation + resend + payment receipt + 2 feedback notifications


# --- Criteria 2 and 3: the resident only reaches what belongs to the resident ----------------------


@pytest.mark.parametrize("route", ROUTES_OF[Access.ADMINISTRADOR], ids=ids(ROUTES_OF[Access.ADMINISTRADOR]))
async def test_resident_is_forbidden_on_every_administrator_route(
    client: AsyncClient, db_session: AsyncSession, resident: User, mailer: FakeMailSender, route: tuple[str, str]
) -> None:
    headers = await open_session(client, db_session, resident)  # valid Origin + CSRF token
    audit_before = await count(db_session, "audit_logs")

    response = await call(client, route, headers)

    assert response.status_code == 403
    assert response.json() == {
        "error": {"code": "FORBIDDEN", "message": "No tienes permiso para realizar esta acción.", "details": []}
    }
    # Nothing was read or changed: no rows, no email, no audit entry.
    for table in ("blocks", "towers", "units", "resident_invitations", "payments", "payment_allocations", "feedback",
                  "charges"):
        assert await count(db_session, table) == 0
    assert mailer.sent == []
    assert await count(db_session, "audit_logs") == audit_before


async def test_resident_reaches_every_resident_route(
    client: AsyncClient, db_session: AsyncSession, admin: User, mailer: FakeMailSender
) -> None:
    # HU-008: a resident with a residence and a payment registered by the administrator (HU-004).
    resident_id = await Community(db_session, admin.id).resident("Laura Gómez", "1012345678", (1, 1, 101))
    payment = await payment_service.register_payment(db_session, PaymentCreate(
        resident_id=resident_id, amount="150000", method="CASH", concept="MONTHLY_FEE", payment_date="2026-01-05",
    ), Actor(user_id=admin.id, ip_address=None), mailer=mailer)
    resident = await db_session.get(User, resident_id)
    assert resident is not None
    headers = await open_session(client, db_session, resident)
    visited: set[tuple[str, str]] = set()

    for route, path in (
        (("GET", "/resident/payments"), "/resident/payments?status=CREDIT&date_from=2026-01-01&search=2026-01-05"
                                        "&page=1&page_size=10"),
        (("GET", "/resident/payments/{payment_id}"), f"/resident/payments/{payment.id}"),
    ):
        response = await client.request(route[0], f"{API}{path}", headers=headers)
        assert response.status_code == 200, (path, response.text)
        visited.add(route)
    # HU-009: the resident sends a feedback, then lists and reads it.
    sent = await client.post(f"{API}/resident/feedback", headers=headers, json=BODIES[("POST", "/resident/feedback")])
    assert sent.status_code == 201, sent.text
    visited.add(("POST", "/resident/feedback"))
    for route, path in ((("GET", "/resident/feedback"), "/resident/feedback?page=1&page_size=10"),
                        (("GET", "/resident/feedback/{feedback_id}"), f"/resident/feedback/{sent.json()['id']}")):
        response = await client.request(route[0], f"{API}{path}", headers=headers)
        assert response.status_code == 200, (path, response.text)
        visited.add(route)
    # HU-010: an obligation of the resident's apartment, paid in part with the demo gateway, then a cancelled start.
    unit_id = await db_session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": resident_id})
    await charge(db_session, int(unit_id), "100000", date(2026, 10, 5))
    fee = await db_session.scalar(text("SELECT MAX(id) FROM charges"))
    payable = await client.get(f"{API}/resident/demo-payments/obligations", headers=headers)
    assert payable.status_code == 200 and [i["charge_id"] for i in payable.json()["items"]] == [fee], payable.text
    visited.add(("GET", "/resident/demo-payments/obligations"))
    started = await client.post(f"{API}/resident/demo-payments", headers=headers, json={"charge_id": fee, "amount": "40000"})
    assert started.status_code == 201, started.text
    visited.add(("POST", "/resident/demo-payments"))
    transaction = started.json()["id"]
    for route, path, body in (
        (("GET", "/resident/demo-payments/instruments"), "/resident/demo-payments/instruments", None),
        (("GET", "/resident/demo-payments/{transaction_id}"), f"/resident/demo-payments/{transaction}", None),
        (("POST", "/resident/demo-payments/{transaction_id}/complete"), f"/resident/demo-payments/{transaction}/complete",
         {"instrument_id": "TEST_CARD_APPROVE"}),
    ):
        response = await client.request(route[0], f"{API}{path}", headers=headers, json=body)
        assert response.status_code == 200, (path, response.text)
        visited.add(route)
    # HU-010: the receipt of the payment just approved (the manual payment above has none).
    approved = response.json()["payment_id"]
    receipt = await client.get(f"{API}/resident/payments/{approved}/receipt", headers=headers)
    assert receipt.status_code == 200 and receipt.content.startswith(b"%PDF-"), receipt.text
    visited.add(("GET", "/resident/payments/{payment_id}/receipt"))
    again = await client.post(f"{API}/resident/demo-payments", headers=headers, json={"charge_id": fee, "amount": "1000"})
    cancelled = await client.post(f"{API}/resident/demo-payments/{again.json()['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED", cancelled.text
    visited.add(("POST", "/resident/demo-payments/{transaction_id}/cancel"))

    assert sorted(visited) == ROUTES_OF[Access.RESIDENTE]


@pytest.mark.parametrize("route", ROUTES_OF[Access.RESIDENTE], ids=ids(ROUTES_OF[Access.RESIDENTE]))
async def test_administrator_is_forbidden_on_every_resident_route(
    client: AsyncClient, db_session: AsyncSession, admin: User, route: tuple[str, str]
) -> None:
    headers = await open_session(client, db_session, admin)

    response = await call(client, route, headers)

    assert response.status_code == 403
    assert response.json() == {
        "error": {"code": "FORBIDDEN", "message": "No tienes permiso para realizar esta acción.", "details": []}
    }


@pytest.mark.parametrize("role", [UserRole.ADMINISTRADOR, UserRole.RESIDENTE])
async def test_both_roles_read_only_their_own_session(
    client: AsyncClient, db_session: AsyncSession, admin: User, resident: User, role: UserRole
) -> None:
    me, other = (admin, resident) if role == UserRole.ADMINISTRADOR else (resident, admin)
    expected = {"id": me.id, "full_name": me.full_name, "email": me.email, "role": role.value}
    other_id = other.id
    await open_session(client, db_session, me)

    # Identifiers or roles sent by the client select nothing.
    for query in ("", f"?user_id={other_id}", f"?id={other_id}&role=ADMINISTRADOR", f"?email={other.email}"):
        response = await client.get(f"{API}/auth/session{query}", headers={"X-User-Id": str(other_id), "X-Role": "ADMINISTRADOR"})
        assert response.status_code == 200
        assert response.json()["user"] == expected
        assert set(response.json()) == {"user", "csrf_token"}


# --- Role tampering: nothing from the client replaces the session ----------------------------------


async def test_role_sent_by_the_client_is_ignored(client: AsyncClient, db_session: AsyncSession, resident: User) -> None:
    headers = await open_session(client, db_session, resident)
    forged = {**headers, "X-Role": "ADMINISTRADOR", "X-User-Role": "ADMINISTRADOR", "X-User-Id": "1"}
    client.cookies.set("role", "ADMINISTRADOR")
    client.cookies.set("user_role", "ADMINISTRADOR")

    attempts = [
        await client.get(f"{API}/blocks?role=ADMINISTRADOR&user_role=ADMINISTRADOR", headers=forged),
        await client.post(f"{API}/blocks", json={"number": 1, "role": "ADMINISTRADOR", "user_role": "ADMINISTRADOR"}, headers=forged),
        await client.get(f"{API}/resident-invitations?status=PENDING&role=ADMINISTRADOR", headers=forged),
    ]

    assert [r.status_code for r in attempts] == [403, 403, 403]
    assert (await client.get(f"{API}/auth/session?role=ADMINISTRADOR")).json()["user"]["role"] == "RESIDENTE"
    assert await count(db_session, "blocks") == 0


async def test_login_ignores_a_role_sent_by_the_client(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser
) -> None:
    await make_user("residente@example.com", role=UserRole.RESIDENTE)
    forged = {"email": "residente@example.com", "password": PASSWORD,
              "role": "ADMINISTRADOR", "user_role": "ADMINISTRADOR", "is_admin": True}

    response = await client.post(f"{API}/auth/login", json=forged, headers=origin_headers)

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "RESIDENTE"
    assert (await client.get(f"{API}/blocks")).status_code == 403


async def test_role_is_read_from_the_database_on_every_request(
    client: AsyncClient, db_session: AsyncSession, admin: User
) -> None:
    await open_session(client, db_session, admin)
    assert (await client.get(f"{API}/blocks")).status_code == 200

    # The role of the account changes: the same session obeys the new role immediately.
    await db_session.execute(update(User).where(User.id == admin.id).values(role=UserRole.RESIDENTE))

    assert (await client.get(f"{API}/blocks")).status_code == 403
    assert (await client.get(f"{API}/auth/session")).json()["user"]["role"] == "RESIDENTE"


# --- Criterion 4 / IDOR: changed identifiers reveal nothing ----------------------------------------


async def test_resident_cannot_reach_resources_by_changing_identifiers(
    client: AsyncClient, db_session: AsyncSession, admin: User, resident: User, mailer: FakeMailSender
) -> None:
    admin_headers = await open_session(client, db_session, admin)
    block = (await client.post(f"{API}/blocks", json={"number": 1}, headers=admin_headers)).json()
    tower = (await client.post(f"{API}/blocks/{block['id']}/towers", json={"number": 1}, headers=admin_headers)).json()
    unit = (await client.post(f"{API}/towers/{tower['id']}/units", json={"number": 101}, headers=admin_headers)).json()
    invitation = (await client.post(f"{API}/resident-invitations", headers=admin_headers, json={
        "email": "otra@example.com", "full_name": "Otra Persona", "unit_id": unit["id"], "validity_days": 7,
    })).json()
    feedback_id = await feedback_service.create_feedback(db_session, resident.id, subject="Mío", message="Hola",
                                                         category="COMPLAINT", urgency="MEDIUM")
    charge = (await client.post(f"{API}/admin/charges", headers=admin_headers, json={
        "unit_id": unit["id"], "concept": "OTHER", "amount": "1000", "due_date": "2026-10-20", "description": "Llave",
    })).json()
    sent, audit_before = len(mailer.sent), await count(db_session, "audit_logs")
    headers = await open_session(client, db_session, resident)

    # Real ids, missing ids and invalid ids get the same answer: no existence leak.
    real = {"/blocks/{block_id}": block["id"], "/towers/{tower_id}": tower["id"], "/units/{unit_id}": unit["id"],
            "/resident-invitations/{invitation_id}/resend": invitation["id"], "/admin/feedback/{feedback_id}": feedback_id,
            "/admin/charges/{charge_id}": charge["id"]}
    bodies = set()
    for route in ROUTES_OF[Access.ADMINISTRADOR]:
        if "{" not in route[1]:
            continue
        own_id = next(v for k, v in real.items() if route[1].startswith(k.split("/{")[0] + "/{"))
        for resource_id in (own_id, 999_999, 0, -1, "abc", 2**64):
            response = await call(client, route, headers, resource_id)
            assert response.status_code == 403, (route, resource_id, response.text)
            bodies.add(response.text)

    assert len(bodies) == 1
    assert len(mailer.sent) == sent  # the invitation was not resent
    assert await count(db_session, "audit_logs") == audit_before
    stored = (await db_session.execute(text("SELECT number FROM units WHERE id = :id"), {"id": unit["id"]})).scalar_one()
    assert stored == 101


async def test_no_resident_route_selects_another_person() -> None:
    # Nothing published takes a user/resident identifier: the identity is always the session.
    for path in app.openapi()["paths"]:
        assert not re.search(r"\{(user|resident|owner|account)_?id\}", path), path


async def test_unpublished_resident_routes_do_not_exist(
    client: AsyncClient, db_session: AsyncSession, resident: User, admin: User
) -> None:
    await open_session(client, db_session, resident)
    for path in (f"/residents/{admin.id}", f"/users/{admin.id}", f"/residents/{admin.id}/payments", "/resident/units"):
        response = await client.get(f"{API}{path}")
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_resident_never_receives_secrets_or_other_people_data(
    client: AsyncClient, db_session: AsyncSession, admin: User, resident: User
) -> None:
    admin_headers = await open_session(client, db_session, admin)
    await client.post(f"{API}/blocks", json={"number": 1}, headers=admin_headers)
    admin_email = admin.email
    headers = await open_session(client, db_session, resident)
    token = client.cookies[SESSION_COOKIE_NAME]
    resident_hash = resident.password_hash or ""

    responses = [await client.get(f"{API}/auth/session")]
    for route in ROUTES_OF[Access.ADMINISTRADOR]:
        responses.append(await call(client, route, headers))
    dump = "".join(r.text + str(dict(r.headers)) for r in responses)

    for secret in (token, hash_token(token), resident_hash, admin_email, "password_hash", "token_hash", "document_number"):
        assert secret not in dump
    assert all("set-cookie" not in r.headers for r in responses)


# --- Invalid, revoked or expired sessions → 401 (never 403, never data) ----------------------------


SESSION_CASES = ["missing", "random", "malformed", "stored-hash", "revoked", "absolute-expired", "idle-expired",
                 "disabled-user", "deleted-user"]


@pytest.mark.parametrize("case", SESSION_CASES)
async def test_invalid_sessions_are_rejected(
    client: AsyncClient, db_session: AsyncSession, admin: User, case: str
) -> None:
    await open_session(client, db_session, admin)
    token = client.cookies[SESSION_COOKIE_NAME]
    now = utc_now()
    session_row = update(UserSession).where(UserSession.token_hash == hash_token(token))
    match case:
        case "missing":
            client.cookies.clear()
        case "random":
            client.cookies.set(SESSION_COOKIE_NAME, generate_session_token())
        case "malformed":
            client.cookies.set(SESSION_COOKIE_NAME, "' OR 1=1 --" + "x" * 600)
        case "stored-hash":  # what the database stores is not a valid cookie
            client.cookies.set(SESSION_COOKIE_NAME, hash_token(token))
        case "revoked":
            await db_session.execute(session_row.values(revoked_at=now))
        case "absolute-expired":
            await db_session.execute(session_row.values(expires_at=now - timedelta(seconds=1)))
        case "idle-expired":
            await db_session.execute(session_row.values(last_seen_at=now - timedelta(minutes=31)))
        case "disabled-user":
            await db_session.execute(update(User).where(User.id == admin.id).values(status=UserStatus.DISABLED))
        case "deleted-user":
            await db_session.execute(update(User).where(User.id == admin.id).values(deleted_at=now))

    for path in ("/auth/session", "/blocks", "/units/available"):
        response = await client.get(f"{API}{path}")
        assert response.status_code == 401, (case, path)
        assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.parametrize("signed_in", [False, True], ids=["anonymous", "resident"])
async def test_malformed_json_is_rejected_before_anything_runs(
    client: AsyncClient, db_session: AsyncSession, resident: User, origin_headers: dict[str, str], signed_in: bool
) -> None:
    # FastAPI parses the JSON syntax before the dependencies: a broken body gets
    # 422 instead of 401/403. Nothing runs and nothing about the data is revealed.
    headers = await open_session(client, db_session, resident) if signed_in else origin_headers
    response = await client.post(f"{API}/blocks", content=b"{bad json", headers={**headers, "Content-Type": "application/json"})

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": "1", "message": "El cuerpo de la solicitud no es un JSON válido."}]
    assert await count(db_session, "blocks") == 0
    assert await count(db_session, "audit_logs") == 0


async def test_denied_requests_leave_no_session_behind(
    client: AsyncClient, db_session: AsyncSession, resident: User
) -> None:
    headers = await open_session(client, db_session, resident)
    sessions = await db_session.scalar(select(func.count()).select_from(UserSession))
    for route in ROUTES_OF[Access.ADMINISTRADOR]:
        await call(client, route, headers)
    # A 403 neither ends nor creates sessions; the resident keeps working.
    assert await db_session.scalar(select(func.count()).select_from(UserSession)) == sessions
    assert (await client.get(f"{API}/auth/session")).status_code == 200
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == 0
