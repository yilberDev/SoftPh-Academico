"""Deep review, phase 2: the findings of the static review, confirmed or ruled out with a test.

IDs (R-xx) are those of docs/revision/revision-profunda-2026-11.md. A test marked
xfail(strict=True) states the expected behavior and fails today because of the defect; it turns
into XPASS (a failure) once the defect is fixed. The other tests confirm the current behavior.
Only the *_test database is used (integration fixtures), with fictitious data.
"""

import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip
from app.core.clock import bogota_today
from app.core.config import get_settings
from app.main import app
from app.models import Charge, User, UserRole
from app.services import charges as charge_service
from app.services.login_throttle import (
    ThrottlePolicy,
    ThrottleState,
    lock_remaining,
    register_failure,
)
from app.services.monthly_rules import MonthlyParams
from app.services.structure import Actor
from tests.integration.conftest import PASSWORD, sign_in
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

API = "/api/v1"
MakeUser = Callable[..., Awaitable[User]]
APP_DIR = Path(__file__).resolve().parents[2] / "app"


def routes() -> set[tuple[str, str]]:
    """(METHOD, path) of every published route, from the application's OpenAPI."""
    paths = app.openapi()["paths"]
    return {(method.upper(), path.removeprefix(API)) for path, item in paths.items() for method in item}


def has_route(method: str, *fragments: str) -> bool:
    return any(m == method and all(f in p for f in fragments) for m, p in routes())


@pytest.fixture
async def admin(make_user: MakeUser) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
async def community(db_session: AsyncSession, admin: User) -> Community:
    return Community(db_session, admin.id)


@pytest.fixture
async def headers(client: AsyncClient, db_session: AsyncSession, admin: User) -> dict[str, str]:
    return await sign_in(client, db_session, admin)


# --- R-01: payments and obligations cannot be voided or corrected --------------------------------------


@pytest.mark.xfail(strict=True, reason="R-01: no route voids, reverses or corrects a payment or an obligation")
def test_r01_a_payment_or_an_obligation_can_be_voided() -> None:
    writes = {(m, p) for m, p in routes() if m in {"POST", "PATCH", "PUT", "DELETE"}}
    payment_fix = any(p.startswith("/admin/payments/") for _, p in writes)
    charge_fix = any(p.startswith("/admin/charges/") and "monthly" not in p for _, p in writes)
    assert payment_fix and charge_fix


def test_r01_the_only_financial_writes_are_creations() -> None:
    financial = sorted((m, p) for m, p in routes() if p.startswith(("/admin/payments", "/admin/charges"))
                       and m != "GET")
    assert financial == [("POST", "/admin/charges"), ("POST", "/admin/charges/monthly"), ("POST", "/admin/payments")]


# --- R-02: a manual receipt cannot be resent or downloaded ---------------------------------------------


@pytest.mark.xfail(strict=True, reason="R-02: no route resends or downloads the receipt of a MANUAL payment")
def test_r02_the_receipt_of_a_manual_payment_can_be_resent_or_downloaded() -> None:
    admin_side = has_route("POST", "/admin/payments/", "receipt") or has_route("GET", "/admin/payments/", "receipt")
    assert admin_side


def test_r02_the_resident_download_is_only_for_demo_payments() -> None:
    source = (APP_DIR / "api/v1/resident_payments.py").read_text()
    assert "get_demo_receipt" in source and "a demo payment: anything else is \"not found\"" in source


# --- R-03: a pending invitation cannot be cancelled and holds the apartment up to 30 days ---------------


@pytest.mark.xfail(strict=True, reason="R-03: there is no route to cancel a pending invitation")
def test_r03_a_pending_invitation_can_be_cancelled() -> None:
    assert has_route("POST", "/resident-invitations/", "cancel") or has_route("DELETE", "/resident-invitations/")


async def test_r03_a_30_day_invitation_reserves_the_apartment(
    client: AsyncClient, headers: dict[str, str], community: Community
) -> None:
    unit = await community.unit(1, 1, 101)
    created = await client.post(f"{API}/resident-invitations", headers=headers, json={
        "email": "equivocado@example.com", "full_name": "Correo Equivocado", "unit_id": unit, "validity_days": 30})
    assert created.status_code == 201
    available = (await client.get(f"{API}/units/available")).json()["items"]
    assert unit not in [item["unit_id"] for item in available]
    again = await client.post(f"{API}/resident-invitations", headers=headers, json={
        "email": "correcto@example.com", "full_name": "Correo Correcto", "unit_id": unit, "validity_days": 7})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "UNIT_NOT_AVAILABLE")


# --- R-04 / R-11: no password change for the administrator, no profile for the resident ----------------


@pytest.mark.xfail(strict=True, reason="R-04: an authenticated user cannot change their own password")
def test_r04_a_signed_in_user_can_change_the_password() -> None:
    assert any("password" in p and "reset" not in p and m in {"POST", "PATCH", "PUT"} for m, p in routes())


@pytest.mark.xfail(strict=True, reason="R-11: notifications have no read state and the resident has no profile")
def test_r11_notifications_can_be_marked_read_and_the_resident_edits_the_phone() -> None:
    assert has_route("PATCH", "/resident/notifications") or has_route("POST", "/resident/notifications/", "read")
    assert has_route("PATCH", "/resident/profile") or has_route("PATCH", "/resident/me")


# --- R-05: the e-mail lock keeps the administrator out indefinitely ------------------------------------


@pytest.mark.xfail(strict=True, reason="R-05: 5 wrong attempts by anyone lock the right password out for 15 min")
async def test_r05_the_administrator_signs_in_while_someone_else_guesses(
    client: AsyncClient, make_user: MakeUser, origin_headers: dict[str, str]
) -> None:
    await make_user("admin@example.com", password=PASSWORD)
    for _ in range(get_settings().login_max_failed_attempts):  # an attacker that only knows the e-mail
        await client.post(f"{API}/auth/login", headers=origin_headers,
                          json={"email": "admin@example.com", "password": "clave-incorrecta-123"})
    own = await client.post(f"{API}/auth/login", headers=origin_headers,
                            json={"email": "admin@example.com", "password": PASSWORD})
    assert own.status_code == 200, own.json()


def test_r05_five_attempts_every_lockout_keep_the_account_locked_all_day() -> None:
    policy = ThrottlePolicy.from_settings(get_settings())
    now, state, locked_minutes = datetime(2026, 11, 13, 0, 0), None, 0
    for minute in range(24 * 60):  # one day, an attacker probing once per minute
        moment = now + timedelta(minutes=minute)
        if lock_remaining(state, moment) is not None:
            locked_minutes += 1
            continue  # while locked, the attempt is refused and changes nothing
        state = register_failure(state, moment, policy)
        if state.locked_until is not None:
            locked_minutes += 1
    # One request per minute (whatever the password) keeps the e-mail locked about 79 % of the day;
    # a burst of 5 requests every 15 minutes, right when the lock ends, keeps it locked 100 %.
    assert locked_minutes / (24 * 60) > 0.7
    burst = ThrottleState(0, now, None)
    locked = 0
    for cycle in range(96):
        moment = now + timedelta(minutes=15 * cycle)
        for _ in range(policy.max_failed_attempts):
            burst = register_failure(burst, moment, policy)
        locked += 1 if burst.locked_until == moment + policy.lockout else 0
    assert locked == 96


# --- R-06: no flow sets DISABLED: the "Inactivo" filter of HU-006 is always empty ----------------------


def test_r06_no_code_writes_the_disabled_status() -> None:
    writers = [path for path in APP_DIR.rglob("*.py")
               if "status=UserStatus.DISABLED" in path.read_text() or "status = UserStatus.DISABLED" in path.read_text()]
    assert writers == []


@pytest.mark.xfail(strict=True, reason="R-06: the administrator cannot disable (Inactivo) a resident")
async def test_r06_the_administrator_disables_a_resident(
    client: AsyncClient, headers: dict[str, str], community: Community
) -> None:
    resident = await community.resident("Laura Gómez", "1012345678", (1, 1, 101))
    detail = (await client.get(f"{API}/admin/residents/{resident}")).json()
    response = await client.patch(f"{API}/admin/residents/{resident}", headers=headers, json={
        "full_name": detail["full_name"], "document_number": detail["document_number"], "phone": None,
        "updated_at": detail["updated_at"], "status": "INACTIVE"})
    assert response.status_code == 200
    inactive = (await client.get(f"{API}/admin/residents?status=INACTIVE")).json()
    assert inactive["total"] == 1


# --- R-07 / R-08 / R-09: e-mail reserved after deletion, e-mail not editable, no transfer --------------


@pytest.mark.xfail(strict=True, reason="R-07: the e-mail of a deleted resident stays reserved for ever")
async def test_r07_a_deleted_resident_can_be_invited_again(
    client: AsyncClient, headers: dict[str, str], community: Community
) -> None:
    resident = await community.resident("Laura Gómez", "1012345678", (1, 1, 101), email="laura@example.com")
    detail = (await client.get(f"{API}/admin/residents/{resident}")).json()
    deleted = await client.delete(f"{API}/admin/residents/{resident}", headers=headers,
                                  params={"updated_at": detail["updated_at"]})
    assert deleted.status_code == 204
    other = await community.unit(1, 1, 102)  # she moves to another apartment of the same community
    invited = await client.post(f"{API}/resident-invitations", headers=headers, json={
        "email": "laura@example.com", "full_name": "Laura Gómez", "unit_id": other, "validity_days": 7})
    assert invited.status_code == 201, invited.json()


@pytest.mark.xfail(strict=True, reason="R-08: the e-mail of a resident cannot be corrected")
async def test_r08_the_administrator_corrects_the_email(
    client: AsyncClient, headers: dict[str, str], community: Community
) -> None:
    resident = await community.resident("Laura Gómez", "1012345678", (1, 1, 101), email="lauar@example.com")
    detail = (await client.get(f"{API}/admin/residents/{resident}")).json()
    response = await client.patch(f"{API}/admin/residents/{resident}", headers=headers, json={
        "full_name": detail["full_name"], "document_number": detail["document_number"], "phone": None,
        "updated_at": detail["updated_at"], "email": "laura@example.com"})
    assert response.status_code == 200


@pytest.mark.xfail(strict=True, reason="R-09: there is no transfer of a resident to another apartment")
def test_r09_a_resident_can_move_to_another_apartment() -> None:
    assert any("/admin/residents/" in p and any(w in p for w in ("unit", "move", "transfer")) for _, p in routes())


# --- R-10: monthly fees for empty apartments become the next resident's debt ---------------------------


async def test_r10_the_monthly_generation_includes_apartments_without_a_resident(
    db_session: AsyncSession, admin: User, community: Community
) -> None:
    await community.resident("Laura Gómez", "1012345678", (1, 1, 101))
    await community.unit(1, 1, 102)  # empty
    await db_session.commit()
    params = MonthlyParams(date(2026, 10, 1), Decimal(180000), date(2026, 10, 10))
    preview = await charge_service.preview_monthly(db_session, params)
    assert (preview.active_units, preview.to_create) == (2, 2)


@pytest.mark.xfail(strict=True, reason="R-10: the new resident inherits fees generated while the apartment was empty")
async def test_r10_a_new_resident_does_not_inherit_the_fees_of_the_empty_months(
    client: AsyncClient, db_session: AsyncSession, admin: User, community: Community
) -> None:
    empty = await community.unit(1, 1, 102)
    await db_session.commit()
    params = MonthlyParams(date(2026, 8, 1), Decimal(180000), date(2026, 8, 10))
    preview = await charge_service.preview_monthly(db_session, params)
    await charge_service.generate_monthly(db_session, charge_service.MonthlyGenerationInput(
        params, preview.to_create, preview.preview_fingerprint), Actor(admin.id, None))
    # Months later a resident arrives (activation creates the residence today).
    newcomer = await community.resident("Pedro Ruiz", "2012345678", None)
    await community.residence(newcomer, empty)
    await sign_in(client, db_session, admin)
    statement = (await client.get(f"{API}/admin/residents/{newcomer}/account-statement")).json()
    assert statement["summary"]["pending_balance"] in ("0", "0.00"), statement["summary"]


# --- R-12 / R-13 / R-14: deployment and HTTP hardening -------------------------------------------------


@pytest.mark.xfail(strict=True, reason="R-13: the API sends no security headers (nosniff, frame-ancestors, CSP)")
async def test_r13_the_api_sends_security_headers(client: AsyncClient) -> None:
    response = await client.get(f"{API}/health")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert "frame-ancestors" in response.headers.get("content-security-policy", "") \
        or response.headers.get("x-frame-options") in {"DENY", "SAMEORIGIN"}


def test_r14_client_ip_ignores_x_forwarded_for() -> None:
    scope = {"type": "http", "client": ("10.0.0.5", 50000),
             "headers": [(b"x-forwarded-for", b"203.0.113.7")], "method": "GET", "path": "/"}
    assert client_ip(Request(scope)) == "10.0.0.5"  # behind a reverse proxy every entry is the proxy's IP


# --- R-15 (and R-16): resident with credit cannot be deleted, and there is no refund --------------------


@pytest.mark.xfail(strict=True, reason="R-16: a resident with saldo a favor can never leave (no refund, no void)")
async def test_r16_a_resident_with_only_credit_can_be_removed(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], community: Community
) -> None:
    resident = await community.resident("Laura Gómez", "1012345678", (1, 1, 101))
    await db_session.commit()
    paid = await client.post(f"{API}/admin/payments", headers=headers, json={
        "resident_id": resident, "amount": "50000", "method": "CASH", "concept": "MONTHLY_FEE",
        "payment_date": bogota_today().isoformat()})
    assert paid.status_code == 201 and paid.json()["status"] == "CREDIT"
    detail = (await client.get(f"{API}/admin/residents/{resident}")).json()
    deleted = await client.delete(f"{API}/admin/residents/{resident}", headers=headers,
                                  params={"updated_at": detail["updated_at"]})
    assert deleted.status_code == 204, deleted.json()


async def test_r16_the_only_way_out_is_a_fictitious_charge(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], community: Community
) -> None:
    resident = await community.resident("Laura Gómez", "1012345678", (1, 1, 101))
    unit = await db_session.scalar(text("SELECT unit_id FROM resident_units WHERE user_id = :u"), {"u": resident})
    await db_session.commit()
    await client.post(f"{API}/admin/payments", headers=headers, json={
        "resident_id": resident, "amount": "50000", "method": "CASH", "concept": "MONTHLY_FEE",
        "payment_date": bogota_today().isoformat()})
    detail = (await client.get(f"{API}/admin/residents/{resident}")).json()
    blocked = await client.delete(f"{API}/admin/residents/{resident}", headers=headers,
                                  params={"updated_at": detail["updated_at"]})
    assert (blocked.status_code, blocked.json()["error"]["code"]) == (409, "RESIDENT_DELETION_BLOCKED")
    # The administrator has to invent an obligation to consume the credit.
    created = await client.post(f"{API}/admin/charges", headers=headers, json={
        "unit_id": unit, "concept": "OTHER", "amount": "50000", "due_date": bogota_today().isoformat(),
        "description": "Ajuste para devolver saldo"})
    assert created.status_code == 201
    detail = (await client.get(f"{API}/admin/residents/{resident}")).json()
    deleted = await client.delete(f"{API}/admin/residents/{resident}", headers=headers,
                                  params={"updated_at": detail["updated_at"]})
    assert deleted.status_code == 204
    kept = await db_session.scalar(select(Charge.description).where(Charge.unit_id == unit))
    assert kept == "Ajuste para devolver saldo"  # an obligation that never existed stays in the history


def test_resident_role_is_still_the_only_payable_role() -> None:
    assert {UserRole.ADMINISTRADOR, UserRole.RESIDENTE} == set(UserRole)


@pytest.mark.xfail(strict=True, reason="R-07: the document of a deleted resident stays reserved too: no way back")
async def test_r07_a_deleted_resident_comes_back_with_another_email(
    client: AsyncClient, headers: dict[str, str], community: Community, mailer: Any
) -> None:
    resident = await community.resident("Laura Gómez", "1012345678", (1, 1, 101), email="laura@example.com")
    detail = (await client.get(f"{API}/admin/residents/{resident}")).json()
    await client.delete(f"{API}/admin/residents/{resident}", headers=headers, params={"updated_at": detail["updated_at"]})
    unit = await community.unit(1, 1, 103)
    invited = await client.post(f"{API}/resident-invitations", headers=headers, json={
        "email": "laura.nueva@example.com", "full_name": "Laura Gómez", "unit_id": unit, "validity_days": 7})
    assert invited.status_code == 201
    token = re.findall(r"[A-Za-z0-9_-]{43}", mailer.sent[-1].body)[-1]
    activated = await client.post(f"{API}/resident-invitations/activate", headers={"Origin": headers["Origin"]}, json={
        "token": token, "document_number": "1012345678", "phone": None,
        "password": "una clave larga 123", "password_confirmation": "una clave larga 123"})
    assert activated.status_code == 200, activated.json()  # today: 409 DOCUMENT_ALREADY_REGISTERED
