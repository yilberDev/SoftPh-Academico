"""HU-015: detail, update and logical deletion of a registered resident (administrator only).

GET, PATCH and DELETE /api/v1/admin/residents/{resident_id}. The data is built in the test
transaction with the HU-006 Community helper (block, tower, apartment, account and residence).
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.config import get_settings
from app.models import Charge, DemoPaymentTransaction, Payment, PaymentAllocation, User, UserRole, UserStatus
from app.services.payments import unit_balance
from app.services.sessions import create_session, resolve_session
from tests.integration.conftest import sign_in
from tests.integration.test_residents_api import Community, MakeUser

pytestmark = pytest.mark.anyio

URL = "/api/v1/admin/residents"
DETAIL_FIELDS = {"id", "full_name", "document_number", "phone", "email", "unit", "status", "updated_at"}


@pytest.fixture
async def admin(make_user: MakeUser) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
async def community(db_session: AsyncSession, admin: User) -> Community:
    return Community(db_session, admin.id)


@pytest.fixture
async def headers(client: AsyncClient, db_session: AsyncSession, admin: User) -> dict[str, str]:
    return await sign_in(client, db_session, admin)


@pytest.fixture
async def laura(community: Community) -> int:
    return await community.resident("Laura Gómez", "1012345678", (1, 2, 104), phone="+573001234567",
                                    email="laura@example.com")


async def detail(client: AsyncClient, resident_id: int) -> dict[str, Any]:
    response = await client.get(f"{URL}/{resident_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


def body(current: dict[str, Any], **changes: Any) -> dict[str, Any]:
    data = {key: current[key] for key in ("full_name", "document_number", "phone", "updated_at")}
    return data | changes


async def patch(client: AsyncClient, headers: dict[str, str], resident_id: int, data: Any) -> Response:
    return await client.patch(f"{URL}/{resident_id}", json=data, headers=headers)


async def delete(client: AsyncClient, headers: dict[str, str], resident_id: int, updated_at: str) -> Response:
    return await client.delete(f"{URL}/{resident_id}", params={"updated_at": updated_at}, headers=headers)


async def rows(db: AsyncSession, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in (await db.execute(text(sql), params)).all()]


async def stored(db: AsyncSession, resident_id: int) -> tuple[Any, ...]:
    return (await rows(db, "SELECT full_name, document_number, phone, email, status, deleted_at FROM users "
                           "WHERE id = :id", id=resident_id))[0]


async def audits(db: AsyncSession, action: str) -> list[tuple[Any, ...]]:
    return await rows(db, "SELECT entity_type, entity_id, actor_user_id, context FROM audit_logs WHERE action = :a "
                          "ORDER BY id", a=action)


def error(response: Response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


# --- Access (HU-018) ---------------------------------------------------------------------------------------


async def test_without_session_every_operation_is_401(client: AsyncClient, origin_headers: dict[str, str],
                                                      laura: int) -> None:
    for response in (
        await client.get(f"{URL}/{laura}"),
        await client.patch(f"{URL}/{laura}", json={}, headers=origin_headers),
        await client.delete(f"{URL}/{laura}", params={"updated_at": "2026-10-22T00:00:00Z"}, headers=origin_headers),
    ):
        assert error(response) == (401, "NOT_AUTHENTICATED")


async def test_a_resident_gets_403_before_anything_is_read(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, laura: int
) -> None:
    resident = await make_user("residente@example.com", role=UserRole.RESIDENTE, password_hash="not-a-real-hash")
    resident_headers = await sign_in(client, db_session, resident)
    before = await stored(db_session, laura)

    for resident_id in (laura, 999_999):
        for response in (
            await client.get(f"{URL}/{resident_id}"),
            await patch(client, resident_headers, resident_id, {"full_name": "Otro Nombre"}),
            await delete(client, resident_headers, resident_id, "2026-10-22T00:00:00Z"),
        ):
            assert error(response) == (403, "FORBIDDEN")
    assert await stored(db_session, laura) == before
    assert await audits(db_session, "RESIDENT_UPDATED") == await audits(db_session, "RESIDENT_DELETED") == []


async def test_writes_need_origin_and_the_csrf_token(client: AsyncClient, headers: dict[str, str], laura: int) -> None:
    current = await detail(client, laura)
    without_csrf = {"Origin": headers["Origin"]}
    without_origin = {key: value for key, value in headers.items() if key != "Origin"}

    for request_headers in (without_csrf, without_origin):
        assert error(await patch(client, request_headers, laura, body(current, full_name="Otro Nombre"))) == (
            403, "CSRF_FAILED")
        assert error(await delete(client, request_headers, laura, current["updated_at"])) == (403, "CSRF_FAILED")
    assert (await detail(client, laura)) == current


# --- Detail (criteria 1.1 and 4.1) ---------------------------------------------------------------------------


async def test_the_detail_of_a_registered_resident(client: AsyncClient, headers: dict[str, str], laura: int) -> None:
    response = await client.get(f"{URL}/{laura}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert set(data) == DETAIL_FIELDS
    assert {key: data[key] for key in DETAIL_FIELDS - {"unit", "updated_at"}} == {
        "id": laura, "full_name": "Laura Gómez", "document_number": "1012345678", "phone": "+573001234567",
        "email": "laura@example.com", "status": "ACTIVE",
    }
    assert data["unit"]["label"] == "B1-TO2-APTO-104"
    assert datetime.fromisoformat(data["updated_at"]).tzinfo is not None  # UTC, the optimistic-locking token
    for forbidden in ("password_hash", "not-a-real-hash", "deleted_at", "role", "token"):
        assert forbidden not in response.text


async def test_an_inactive_resident_is_registered_too(client: AsyncClient, headers: dict[str, str],
                                                      community: Community) -> None:
    pedro = await community.resident("Pedro Ruiz", "10000002", (1, 1, 102), status=UserStatus.DISABLED)

    assert (await detail(client, pedro))["status"] == "INACTIVE"


async def test_who_is_not_a_registered_resident_looks_like_a_missing_one(
    client: AsyncClient, headers: dict[str, str], community: Community, admin: User
) -> None:
    others = [
        await community.resident("Pendiente Invitada", None, None, status=UserStatus.PENDING_ACTIVATION),
        await community.resident("Residencia Terminada", "10000004", (1, 1, 104), ended=True),
        await community.resident("Sin Residencia", "10000005", None),
        await community.resident("Cuenta Eliminada", "10000006", (1, 1, 105), deleted=True),
        admin.id,
        999_999,
    ]
    bodies = set()
    for resident_id in others:
        for response in (
            await client.get(f"{URL}/{resident_id}"),
            await patch(client, headers, resident_id, {"full_name": "Otro Nombre", "document_number": "10000099",
                                                       "phone": None, "updated_at": "2026-10-22T00:00:00Z"}),
            await delete(client, headers, resident_id, "2026-10-22T00:00:00Z"),
        ):
            assert error(response) == (404, "RESIDENT_NOT_FOUND"), (resident_id, response.text)
            bodies.add(response.text)
    assert bodies == {('{"error":{"code":"RESIDENT_NOT_FOUND","message":"El residente no existe o ya no está '
                       'registrado.","details":[]}}')}


@pytest.mark.parametrize("resident_id", ["0", "-1", "abc", str(2**63)])
async def test_invalid_ids_are_422(client: AsyncClient, headers: dict[str, str], resident_id: str) -> None:
    assert error(await client.get(f"{URL}/{resident_id}")) == (422, "VALIDATION_ERROR")


# --- Update (criteria 1.2 and 3) ------------------------------------------------------------------------------


async def test_the_administrator_updates_the_registration_data(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], admin: User, laura: int
) -> None:
    current = await detail(client, laura)

    response = await patch(client, headers, laura, body(
        current, full_name="  Laura   Gómez Ruiz ", document_number=" ab123456 ", phone="300 123-4567"))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    updated = response.json()
    # The same normalization as HU-003 and HU-019; email, status and apartment untouched.
    assert {key: updated[key] for key in ("full_name", "document_number", "phone", "email", "status")} == {
        "full_name": "Laura Gómez Ruiz", "document_number": "AB123456", "phone": "3001234567",
        "email": "laura@example.com", "status": "ACTIVE"}
    assert updated["unit"] == current["unit"]
    assert updated["updated_at"] != current["updated_at"]  # a new token for the next change
    assert await detail(client, laura) == updated
    # Criterion 3.2: the list of residents (HU-006) reflects it on the next query.
    listed = (await client.get(URL)).json()["items"]
    assert [(item["id"], item["full_name"], item["document_number"]) for item in listed] == [
        (laura, "Laura Gómez Ruiz", "AB123456")]
    # Audited with the names of the changed fields only: never the values.
    assert await audits(db_session, "RESIDENT_UPDATED") == [
        ("user", laura, admin.id, '{"changed_fields": ["full_name", "document_number", "phone"]}')]


async def test_the_phone_can_be_removed(client: AsyncClient, headers: dict[str, str], laura: int) -> None:
    current = await detail(client, laura)

    for phone in ("", None):
        response = await patch(client, headers, laura, body(current, phone=phone))
        assert response.status_code == 200, response.text
        current = response.json()
        assert current["phone"] is None


async def test_saving_without_changes_changes_nothing(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], laura: int
) -> None:
    current = await detail(client, laura)

    response = await patch(client, headers, laura, body(current))

    assert response.status_code == 200
    assert response.json() == current  # same token: nothing was written
    assert await audits(db_session, "RESIDENT_UPDATED") == []


async def test_the_token_may_come_in_another_time_zone(client: AsyncClient, headers: dict[str, str],
                                                       laura: int) -> None:
    current = await detail(client, laura)
    bogota = datetime.fromisoformat(current["updated_at"]).astimezone(UTC).astimezone(
        datetime.now().astimezone().tzinfo).isoformat()

    response = await patch(client, headers, laura, body(current, updated_at=bogota, full_name="Laura Gómez Ruiz"))

    assert response.status_code == 200, response.text


# --- Validation (criterion 2, CU-015 7a) ------------------------------------------------------------------------


@pytest.mark.parametrize(("changes", "field", "message"), [
    ({"full_name": ""}, "full_name", "El nombre completo es obligatorio."),
    ({"full_name": "Lu"}, "full_name", "El nombre completo debe tener entre 3 y 150 caracteres."),
    ({"full_name": "Laura 2"}, "full_name",
     "El nombre completo solo puede contener letras, espacios, apóstrofos y guiones."),
    ({"document_number": ""}, "document_number", "El número de documento es obligatorio."),
    ({"document_number": "12-34"}, "document_number",
     "El número de documento debe tener entre 5 y 20 letras o dígitos, sin espacios ni símbolos."),
    ({"phone": "12"}, "phone", "El teléfono debe tener entre 7 y 15 dígitos, con un «+» inicial opcional."),
    ({"full_name": 123}, "full_name", "Debe ser un texto."),
    ({"updated_at": "ayer"}, "updated_at", "El valor no es válido."),
])
async def test_invalid_data_is_not_saved(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], laura: int,
    changes: dict[str, Any], field: str, message: str,
) -> None:
    current = await detail(client, laura)
    before = await stored(db_session, laura)

    response = await patch(client, headers, laura, body(current, **changes))

    assert error(response) == (422, "VALIDATION_ERROR")
    assert response.json()["error"]["details"] == [{"field": field, "message": message}]
    assert await stored(db_session, laura) == before


async def test_incomplete_data_lists_every_missing_field(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], laura: int
) -> None:
    before = await stored(db_session, laura)

    response = await patch(client, headers, laura, {})

    assert error(response) == (422, "VALIDATION_ERROR")
    assert response.json()["error"]["details"] == [
        {"field": field, "message": "Este campo es obligatorio."}
        for field in ("full_name", "document_number", "phone", "updated_at")]
    assert await stored(db_session, laura) == before


@pytest.mark.parametrize("field", ["email", "role", "status", "unit_id", "password", "deleted_at", "id"])
async def test_fields_outside_the_registration_data_are_refused(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], laura: int, field: str
) -> None:
    current = await detail(client, laura)
    before = await stored(db_session, laura)

    response = await patch(client, headers, laura, body(current, full_name="Otro Nombre", **{field: "x"}))

    assert error(response) == (422, "VALIDATION_ERROR")
    assert response.json()["error"]["details"] == [{"field": field, "message": "Este campo no está permitido."}]
    assert await stored(db_session, laura) == before


@pytest.mark.parametrize("owner", ["registered", "deleted"])
async def test_a_document_of_another_account_is_a_conflict(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], community: Community, laura: int,
    owner: str,
) -> None:
    # The UNIQUE index covers every account, also a deleted one (its row is kept).
    await community.resident("Otra Persona", "AB123456", (1, 1, 101), deleted=owner == "deleted")
    current = await detail(client, laura)
    before = await stored(db_session, laura)

    response = await patch(client, headers, laura, body(current, document_number="ab123456"))

    assert error(response) == (409, "DOCUMENT_ALREADY_REGISTERED")
    assert response.json()["error"]["details"] == [
        {"field": "document_number", "message": "El número de documento ya está registrado."}]
    assert await stored(db_session, laura) == before
    assert await audits(db_session, "RESIDENT_UPDATED") == []


async def test_keeping_the_own_document_is_not_a_conflict(client: AsyncClient, headers: dict[str, str],
                                                          laura: int) -> None:
    current = await detail(client, laura)

    response = await patch(client, headers, laura, body(current, full_name="Laura Gómez Ruiz"))

    assert response.status_code == 200, response.text


# --- Concurrency and errors (CU-015 9a) ---------------------------------------------------------------------------


async def test_a_stale_token_is_a_conflict_and_nothing_changes(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], laura: int
) -> None:
    first = await detail(client, laura)
    assert (await patch(client, headers, laura, body(first, full_name="Laura Gómez Ruiz"))).status_code == 200
    before = await stored(db_session, laura)

    stale_update = await patch(client, headers, laura, body(first, phone="3009998877"))
    stale_delete = await delete(client, headers, laura, first["updated_at"])

    for response in (stale_update, stale_delete):
        assert error(response) == (409, "VERSION_CONFLICT")
        assert response.json()["error"]["message"] == (
            "El registro fue modificado por otra persona. Recarga la página e inténtalo de nuevo.")
    assert await stored(db_session, laura) == before


# --- Deletion (criteria 4 and 5, CU-015 7c) -------------------------------------------------------------------------


async def test_the_deletion_is_logical_and_consistent(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], admin: User, laura: int
) -> None:
    user = await db_session.get(User, laura)
    assert user is not None
    resident_token = await create_session(db_session, user, get_settings(), utc_now())
    await db_session.commit()
    current = await detail(client, laura)
    unit_id = current["unit"]["id"]

    response = await delete(client, headers, laura, current["updated_at"])

    assert response.status_code == 204
    assert response.content == b""
    # The account row stays (its email, payments and history are kept), marked deleted.
    full_name, document, phone, email, status, deleted_at = await stored(db_session, laura)
    assert (full_name, document, phone, email, status) == (
        "Laura Gómez", "1012345678", "+573001234567", "laura@example.com", "ACTIVE")
    assert deleted_at is not None
    # The residence ends: the apartment can be invited again (HU-003).
    assert await rows(db_session, "SELECT ended_at IS NOT NULL FROM resident_units WHERE user_id = :u", u=laura) == [
        (1,)]
    available = (await client.get("/api/v1/units/available")).json()
    assert unit_id in {unit["unit_id"] for unit in available["items"]}
    # Every session of the resident is revoked: it can no longer act.
    assert await rows(db_session, "SELECT revoked_at IS NOT NULL FROM sessions WHERE user_id = :u", u=laura) == [(1,)]
    assert await resolve_session(db_session, resident_token, get_settings()) is None
    # Criterion 5.2: the list no longer shows the resident; the detail is "not found".
    assert (await client.get(URL)).json()["items"] == []
    assert error(await client.get(f"{URL}/{laura}")) == (404, "RESIDENT_NOT_FOUND")
    # Audited without personal data.
    [(entity, entity_id, actor, context)] = await audits(db_session, "RESIDENT_DELETED")
    assert (entity, entity_id, actor) == ("user", laura, admin.id)
    assert '"sessions_revoked": 1' in context and '"unit_label": "B1-TO2-APTO-104"' in context
    for personal in ("Laura", "1012345678", "laura@example.com", "3001234567"):
        assert personal not in context


async def test_deleting_twice_is_not_found_the_second_time(client: AsyncClient, headers: dict[str, str],
                                                           laura: int) -> None:
    current = await detail(client, laura)

    assert (await delete(client, headers, laura, current["updated_at"])).status_code == 204
    assert error(await delete(client, headers, laura, current["updated_at"])) == (404, "RESIDENT_NOT_FOUND")


async def test_the_deletion_needs_the_token(client: AsyncClient, db_session: AsyncSession, headers: dict[str, str],
                                            laura: int) -> None:
    for params in ({}, {"updated_at": "ayer"}):
        response = await client.delete(f"{URL}/{laura}", params=params, headers=headers)
        assert error(response) == (422, "VALIDATION_ERROR")
    assert (await stored(db_session, laura))[-1] is None


async def money(db: AsyncSession, admin: User, resident_id: int, unit_id: int, *, charged: str | None = None,
                paid: str | None = None, applied: str | None = None) -> tuple[int | None, int | None]:
    """A charge of the apartment and/or a manual payment of the resident, with one allocation (test data)."""
    charge = payment = None
    if charged is not None:
        charge = Charge(unit_id=unit_id, concept="MONTHLY_FEE", amount=Decimal(charged), due_date=date(2026, 10, 10),
                        billing_period=date(2026, 10, 1), created_by_user_id=admin.id)
        db.add(charge)
        await db.flush()
    if paid is not None:
        payment = Payment(resident_id=resident_id, unit_id=unit_id, registered_by_user_id=admin.id, amount=Decimal(paid),
                          method="CASH", concept="MONTHLY_FEE", source="MANUAL", payment_date=date(2026, 10, 5),
                          registered_at=utc_now() - timedelta(days=1), resident_name_snapshot="Laura Gómez",
                          unit_label_snapshot="B1-TO2-APTO-104", receipt_email_status="SENT", receipt_sent_at=utc_now())
        db.add(payment)
        await db.flush()
    if charge is not None and payment is not None and applied is not None:
        db.add(PaymentAllocation(payment_id=payment.id, charge_id=charge.id, amount=Decimal(applied)))
    await db.commit()
    return (charge.id if charge else None, payment.id if payment else None)


async def demo_transaction(db: AsyncSession, resident_id: int, charge_id: int, *, expired: bool = False) -> None:
    """An INITIATED demo transaction (HU-010) on the charge: live, or already past its expiry."""
    created = utc_now() - (timedelta(minutes=20) if expired else timedelta(minutes=1))
    db.add(DemoPaymentTransaction(resident_id=resident_id, charge_id=charge_id, amount=Decimal("1000.00"),
                                  reference=f"DEMO-{resident_id:016X}", status="INITIATED", created_at=created,
                                  expires_at=created + timedelta(minutes=15)))
    await db.commit()


async def snapshot(db: AsyncSession, resident_id: int) -> list[Any]:
    """Everything a blocked deletion must leave as it was."""
    return [
        await stored(db, resident_id),
        await rows(db, "SELECT unit_id, ended_at FROM resident_units WHERE user_id = :u", u=resident_id),
        await rows(db, "SELECT id, revoked_at FROM sessions WHERE user_id = :u ORDER BY id", u=resident_id),
        await rows(db, "SELECT id, resident_id, amount FROM payments ORDER BY id"),
        await rows(db, "SELECT id, amount FROM charges ORDER BY id"),
        await rows(db, "SELECT payment_id, charge_id, amount FROM payment_allocations ORDER BY id"),
        await rows(db, "SELECT id, status FROM demo_payment_transactions ORDER BY id"),
        await audits(db, "RESIDENT_DELETED"),
    ]


async def test_with_every_balance_at_zero_the_history_is_kept(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], admin: User, laura: int
) -> None:
    # A charge fully covered by a payment: no pending balance, no credit. The deletion moves no money.
    current = await detail(client, laura)
    unit_id = current["unit"]["id"]
    charge_id, _ = await money(db_session, admin, laura, unit_id, charged="180000.00", paid="180000.00",
                               applied="180000.00")
    assert charge_id is not None
    await demo_transaction(db_session, laura, charge_id, expired=True)  # an old one, past its expiry: not live
    history = await rows(db_session, "SELECT id, resident_id, amount FROM payments")

    assert (await delete(client, headers, laura, current["updated_at"])).status_code == 204

    balance = await unit_balance(db_session, unit_id)
    assert (balance.pending_balance, balance.credit_balance) == (Decimal("0.00"), Decimal("0.00"))
    assert await rows(db_session, "SELECT id, resident_id, amount FROM payments") == history
    assert await rows(db_session, "SELECT COUNT(*) FROM charges") == [(1,)]
    assert await rows(db_session, "SELECT COUNT(*) FROM payment_allocations") == [(1,)]
    assert await rows(db_session, "SELECT status FROM demo_payment_transactions") == [("INITIATED",)]


BLOCKED_PENDING = "su unidad residencial B1-TO2-APTO-104 tiene saldo pendiente por pagar"
BLOCKED_CREDIT = "su unidad residencial B1-TO2-APTO-104 tiene saldo a favor sin aplicar"
BLOCKED_DEMO = "hay un pago de demostración en curso para una obligación de su unidad residencial B1-TO2-APTO-104"


@pytest.mark.parametrize(("case", "reasons"), [
    ("pending", [("pending_balance", BLOCKED_PENDING)]),
    ("credit", [("credit_balance", BLOCKED_CREDIT)]),
    ("demo", [("demo_payment", BLOCKED_DEMO)]),
    ("pending_and_credit", [("pending_balance", BLOCKED_PENDING), ("credit_balance", BLOCKED_CREDIT)]),
])
async def test_the_apartment_must_be_settled_first(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], admin: User, laura: int, case: str,
    reasons: list[tuple[str, str]],
) -> None:
    current = await detail(client, laura)
    unit_id = current["unit"]["id"]
    user = await db_session.get(User, laura)
    assert user is not None
    await create_session(db_session, user, get_settings(), utc_now())
    if case == "pending":  # 180 000 charged, 100 000 applied: 80 000 pending
        await money(db_session, admin, laura, unit_id, charged="180000.00", paid="100000.00", applied="100000.00")
    elif case == "credit":  # a payment with nothing to cover: all of it is credit
        await money(db_session, admin, laura, unit_id, paid="50000.00")
    elif case == "demo":  # a covered charge with a demo transaction still in progress on it
        charge_id, _ = await money(db_session, admin, laura, unit_id, charged="180000.00", paid="180000.00",
                                   applied="180000.00")
        assert charge_id is not None
        await demo_transaction(db_session, laura, charge_id)
    else:  # a partly covered charge and another payment not applied
        await money(db_session, admin, laura, unit_id, charged="180000.00", paid="100000.00", applied="100000.00")
        await money(db_session, admin, laura, unit_id, paid="5000.00")
    before = await snapshot(db_session, laura)

    response = await delete(client, headers, laura, current["updated_at"])

    assert error(response) == (409, "RESIDENT_DELETION_BLOCKED")
    what = "esa condición" if len(reasons) == 1 else "esas condiciones"
    assert response.json()["error"] == {
        "code": "RESIDENT_DELETION_BLOCKED",
        "message": f"No se puede eliminar al residente porque {'; '.join(text for _, text in reasons)}. "
                   f"Resuelve {what} e inténtalo de nuevo.",
        "details": [{"field": condition, "message": text} for condition, text in reasons],
    }
    for undisclosed in ("80000", "50000", "5000", "180000", "DEMO-", "Laura"):
        assert undisclosed not in response.text  # no amounts, references or personal data
    # Nothing changed: the account, its residence, its sessions, the money and the audit.
    assert await snapshot(db_session, laura) == before
    assert (await detail(client, laura))["updated_at"] == current["updated_at"]


async def test_the_email_of_a_deleted_account_stays_reserved(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], community: Community, laura: int
) -> None:
    current = await detail(client, laura)
    assert (await delete(client, headers, laura, current["updated_at"])).status_code == 204
    free_unit = current["unit"]["id"]  # its residence ended: the apartment can be offered again
    users_before = await rows(db_session, "SELECT COUNT(*) FROM users")

    response = await client.post("/api/v1/resident-invitations", headers=headers, json={
        "email": "LAURA@example.com", "full_name": "Laura Gómez", "unit_id": free_unit, "validity_days": 7})

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "EMAIL_ALREADY_REGISTERED",
                                         "message": "El correo electrónico ya está registrado.", "details": []}}
    assert await rows(db_session, "SELECT COUNT(*) FROM users") == users_before
    assert await rows(db_session, "SELECT COUNT(*) FROM resident_invitations WHERE status = 'PENDING'") == [(0,)]
    assert await rows(db_session, "SELECT email, deleted_at IS NOT NULL FROM users WHERE id = :id", id=laura) == [
        ("laura@example.com", 1)]
    # Another email can still be invited to that apartment.
    other = await client.post("/api/v1/resident-invitations", headers=headers, json={
        "email": "nueva@example.com", "full_name": "Nueva Persona", "unit_id": free_unit, "validity_days": 7})
    assert other.status_code == 201, other.text


async def test_another_resident_is_untouched(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str], community: Community, laura: int
) -> None:
    pedro = await community.resident("Pedro Ruiz", "10000002", (1, 1, 102))
    pedro_before = await stored(db_session, pedro)
    current = await detail(client, laura)

    assert (await delete(client, headers, laura, current["updated_at"])).status_code == 204

    assert await stored(db_session, pedro) == pedro_before
    assert [item["id"] for item in (await client.get(URL)).json()["items"]] == [pedro]
