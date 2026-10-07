"""Obligations extension: /api/v1/admin/charges on the MySQL _test database.

Access (401/403, ADMINISTRADOR only), Origin + CSRF on POST, no-store, the
format validation of the schemas, the domain errors (404/409/422) and the
thin mapping of create_charge, generate_monthly, preview_monthly,
list_charges and get_charge.
"""

import re
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import event, func, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import charges as charges_api
from app.core.sessions import CSRF_HEADER_NAME, derive_csrf_token, generate_session_token
from app.models import AuditAction, AuditLog, Charge, Payment, PaymentAllocation, Unit, User, UserRole
from app.services import charges as charges_service
from tests.integration.conftest import sign_in
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

API = "/api/v1/admin/charges"
TODAY = date(2026, 10, 15)
PREVIEW = {"billing_period": "2026-10", "amount": "180000", "due_date": "2026-10-10"}
FORBIDDEN = {"error": {"code": "FORBIDDEN", "message": "No tienes permiso para realizar esta acción.", "details": []}}
NOT_ALLOWED = "Este campo no está permitido."


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    # "Today" in Bogotá, for the router (list/detail) and the service (origin of a late fee).
    monkeypatch.setattr(charges_api, "bogota_today", lambda: TODAY)
    monkeypatch.setattr(charges_service, "bogota_today", lambda: TODAY)


@pytest.fixture
async def admin(make_user: Any) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
async def headers(client: AsyncClient, db_session: AsyncSession, admin: User) -> dict[str, str]:
    return await sign_in(client, db_session, admin)


@pytest.fixture
async def units(db_session: AsyncSession, admin: User) -> dict[str, int]:
    community = Community(db_session, admin.id)
    return {"a": await community.unit(1, 1, 101), "b": await community.unit(1, 1, 102), "c": await community.unit(2, 1, 101)}


async def get(client: AsyncClient, path: str = "", **params: Any) -> Response:
    return await client.get(f"{API}{path}", params=params)


async def post(client: AsyncClient, headers: dict[str, str], path: str, body: Any) -> Response:
    return await client.post(f"{API}{path}", json=body, headers=headers)


def charge_body(unit_id: int, concept: str = "PENALTY", amount: str = "50000", due: str = "2026-10-20",
                **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"unit_id": unit_id, "concept": concept, "amount": amount, "due_date": due}
    if concept in ("PENALTY", "OTHER"):
        body["description"] = "Uso indebido de zona común"
    return body | extra


async def create(client: AsyncClient, headers: dict[str, str], body: dict[str, Any], expected: int = 201) -> dict[str, Any]:
    response = await post(client, headers, "", body)
    assert response.status_code == expected, response.text
    return dict(response.json())


async def confirm(client: AsyncClient, headers: dict[str, str], params: dict[str, str] = PREVIEW) -> Response:
    preview = (await get(client, "/monthly/preview", **params)).json()
    return await post(client, headers, "/monthly", {**params, "expected_to_create": preview["to_create"],
                                                    "preview_fingerprint": preview["preview_fingerprint"]})


def details(response: Response) -> list[dict[str, str]]:
    return list(response.json()["error"]["details"])


async def count(db: AsyncSession, model: Any) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def credit(db: AsyncSession, unit_id: int, admin: User, amount: str, make_user: Any) -> int:
    resident = await db.scalar(select(User.id).where(User.role == UserRole.RESIDENTE))
    if resident is None:
        resident = (await make_user("pagos@example.com", role=UserRole.RESIDENTE, password_hash="x")).id
    row = Payment(resident_id=resident, unit_id=unit_id, registered_by_user_id=admin.id, amount=Decimal(amount),
                  method="TRANSFER", concept="OTHER", concept_description="Anticipo", payment_date=date(2026, 9, 1),
                  registered_at=datetime(2026, 9, 1, 15), resident_name_snapshot="Laura Gómez", unit_label_snapshot="x")
    db.add(row)
    await db.flush()
    return row.id


ROUTES = [
    ("GET", "/monthly/preview", None), ("POST", "/monthly", {**PREVIEW, "expected_to_create": 0, "preview_fingerprint": "0" * 64}),
    ("POST", "", {"unit_id": 1, "concept": "PENALTY", "amount": "1", "due_date": "2026-10-20", "description": "x"}),
    ("GET", "", None), ("GET", "/1", None),
]


# --- Access: 401, 403, ADMINISTRADOR ---------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=[f"{m} {p or '/'}" for m, p, _ in ROUTES])
async def test_without_session_is_401(client: AsyncClient, origin_headers: dict[str, str], method: str, path: str,
                                      body: Any) -> None:
    query = PREVIEW if path == "/monthly/preview" else None
    response = await client.request(method, f"{API}{path}", params=query, json=body, headers=origin_headers)
    assert response.status_code == 401 and response.json()["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=[f"{m} {p or '/'}" for m, p, _ in ROUTES])
async def test_resident_is_403(client: AsyncClient, db_session: AsyncSession, make_user: Any, units: dict[str, int],
                               method: str, path: str, body: Any) -> None:
    resident = await make_user("residente@example.com", role=UserRole.RESIDENTE, password_hash="x")
    resident_headers = await sign_in(client, db_session, resident)  # valid Origin + CSRF of its own session
    query = PREVIEW if path == "/monthly/preview" else None
    response = await client.request(method, f"{API}{path}", params=query, json=body, headers=resident_headers)
    assert response.status_code == 403 and response.json() == FORBIDDEN  # no financial data at all
    assert await count(db_session, Charge) == 0
    assert await db_session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action.in_([AuditAction.CHARGE_CREATED, AuditAction.MONTHLY_CHARGES_GENERATED]))) == 0


async def test_administrator_reaches_the_five_routes_without_cache(
    client: AsyncClient, headers: dict[str, str], units: dict[str, int]
) -> None:
    preview = await get(client, "/monthly/preview", **PREVIEW)
    generated = await post(client, headers, "/monthly", {**PREVIEW, "expected_to_create": preview.json()["to_create"],
                                                         "preview_fingerprint": preview.json()["preview_fingerprint"]})
    created = await post(client, headers, "", charge_body(units["a"]))
    listed = await get(client)
    detail = await get(client, f"/{created.json()['id']}")
    for response, status in ((preview, 200), (generated, 200), (created, 201), (listed, 200), (detail, 200)):
        assert response.status_code == status, response.text
        assert response.headers["cache-control"] == "no-store"


# --- CSRF and Origin (POST only) -------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["no-origin", "wrong-origin", "no-csrf", "wrong-csrf", "other-session-csrf", "valid"])
@pytest.mark.parametrize("path", ["", "/monthly"])
async def test_post_requires_origin_and_csrf(
    client: AsyncClient, headers: dict[str, str], units: dict[str, int], db_session: AsyncSession, path: str, case: str
) -> None:
    forged = dict(headers)
    if case == "no-origin":
        forged.pop("Origin")
    elif case == "wrong-origin":
        forged["Origin"] = "https://evil.example"
    elif case == "no-csrf":
        forged.pop(CSRF_HEADER_NAME)
    elif case == "wrong-csrf":
        forged[CSRF_HEADER_NAME] = "wrong-token"
    elif case == "other-session-csrf":
        forged[CSRF_HEADER_NAME] = derive_csrf_token(generate_session_token())
    if path == "":
        body: dict[str, Any] = charge_body(units["a"])
    else:
        preview = (await get(client, "/monthly/preview", **PREVIEW)).json()
        body = {**PREVIEW, "expected_to_create": preview["to_create"], "preview_fingerprint": preview["preview_fingerprint"]}

    response = await post(client, forged, path, body)

    if case == "valid":
        assert response.status_code in (200, 201), response.text
        assert await count(db_session, Charge) > 0
    else:
        assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_FAILED"
        assert await count(db_session, Charge) == 0


async def test_get_never_requires_csrf_or_origin(client: AsyncClient, headers: dict[str, str], units: dict[str, int]) -> None:
    charge_id = (await create(client, headers, charge_body(units["a"])))["id"]
    # Only the session cookie: no Origin, no CSRF token.
    for path, params in (("/monthly/preview", PREVIEW), ("", {}), (f"/{charge_id}", {})):
        assert (await client.get(f"{API}{path}", params=params)).status_code == 200


# --- Preview -------------------------------------------------------------------------------------------


async def test_preview(client: AsyncClient, headers: dict[str, str], units: dict[str, int], db_session: AsyncSession) -> None:
    await create(client, headers, {"unit_id": units["a"], "concept": "MONTHLY_FEE", "amount": "150000",
                                   "due_date": "2026-10-05", "billing_period": "2026-10"})
    await db_session.execute(update(Unit).where(Unit.id == units["c"]).values(deleted_at=datetime(2026, 10, 1)))
    before = await count(db_session, Charge)

    body = (await get(client, "/monthly/preview", **PREVIEW)).json()

    assert re.fullmatch(r"[0-9a-f]{64}", body.pop("preview_fingerprint"))
    assert body == {"billing_period": "2026-10", "amount": "180000.00", "due_date": "2026-10-10", "active_units": 2,
                    "existing": 1, "missing": 1, "to_create": 1, "total_amount": "180000.00",
                    "existing_with_other_amount": 1}
    assert await count(db_session, Charge) == before


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({**PREVIEW, "billing_period": "2026-13"}, "query.billing_period"),
        ({**PREVIEW, "billing_period": "10-2026"}, "query.billing_period"),
        ({**PREVIEW, "amount": "0"}, "query.amount"),
        ({**PREVIEW, "amount": "12.345"}, "query.amount"),
        ({**PREVIEW, "due_date": "10/10/2026"}, "query.due_date"),
        ({**PREVIEW, "due_date": "2026-09-30"}, "due_date"),  # service rule: not before the period
        ({**PREVIEW, "extra": "1"}, "query.extra"),
        ({"amount": "1", "due_date": "2026-10-10"}, "query.billing_period"),
    ],
)
async def test_invalid_preview(client: AsyncClient, headers: dict[str, str], params: dict[str, str], field: str) -> None:
    response = await get(client, "/monthly/preview", **params)
    assert response.status_code == 422
    assert [d["field"] for d in details(response)] == [field]


# --- Monthly generation --------------------------------------------------------------------------------


async def test_generation_and_idempotency(
    client: AsyncClient, headers: dict[str, str], units: dict[str, int], db_session: AsyncSession, admin: User, make_user: Any
) -> None:
    await credit(db_session, units["b"], admin, "200000", make_user)

    response = await confirm(client, headers)

    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "billing_period": "2026-10", "amount": "180000.00", "due_date": "2026-10-10", "units_evaluated": 3,
        "created": 3, "existing": 0, "omitted": 0, "failed": 0, "generated_total": "540000.00",
        "credit_applied_total": "180000.00", "credit_applied_to_new_charges": "180000.00",
        "units_with_credit_applied": 1, "outstanding_total": "360000.00", "omitted_units": [], "errors": [],
        "audit_recorded": True,
    }
    again = (await confirm(client, headers)).json()
    assert (again["created"], again["existing"], again["generated_total"]) == (0, 3, "0.00")
    assert await count(db_session, Charge) == 3
    # The audit is the service's: one summary per run, one CHARGE_CREATED per fee (never twice).
    assert await db_session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == AuditAction.MONTHLY_CHARGES_GENERATED)) == 2
    assert await db_session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == AuditAction.CHARGE_CREATED)) == 3


async def test_outdated_preview_is_409_with_the_current_preview(
    client: AsyncClient, headers: dict[str, str], units: dict[str, int], db_session: AsyncSession, admin: User
) -> None:
    preview = (await get(client, "/monthly/preview", **PREVIEW)).json()
    await Community(db_session, admin.id).unit(3, 1, 101)  # an apartment more since the preview

    response = await post(client, headers, "/monthly", {**PREVIEW, "expected_to_create": preview["to_create"],
                                                        "preview_fingerprint": preview["preview_fingerprint"]})

    assert response.status_code == 409 and response.headers["cache-control"] == "no-store"
    error = response.json()["error"]
    assert (error["code"], error["message"], error["details"]) == (
        "MONTHLY_PREVIEW_OUTDATED", "Los datos cambiaron desde la vista previa. Revisa y confirma de nuevo.", [])
    assert (error["preview"]["to_create"], error["preview"]["active_units"]) == (4, 4)
    assert error["preview"]["preview_fingerprint"] != preview["preview_fingerprint"]
    assert await count(db_session, Charge) == 0  # nothing was created
    # Confirming the new preview works.
    current = error["preview"]
    ok = await post(client, headers, "/monthly", {**PREVIEW, "expected_to_create": current["to_create"],
                                                  "preview_fingerprint": current["preview_fingerprint"]})
    assert ok.status_code == 200 and ok.json()["created"] == 4


async def test_per_unit_omission_and_error(
    client: AsyncClient, headers: dict[str, str], units: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_lock, real_settle = charges_service.lock_unit_path, charges_service._settle_credit

    async def deleted_meanwhile(db: AsyncSession, unit_id: int, **kwargs: Any) -> Any:
        if unit_id == units["b"]:
            await db.execute(update(Unit).where(Unit.id == unit_id).values(deleted_at=datetime(2026, 10, 1)))
        return await real_lock(db, unit_id, **kwargs)

    async def broken(db: AsyncSession, unit_id: int) -> Any:
        if unit_id == units["c"]:
            raise RuntimeError("broken")
        return await real_settle(db, unit_id)

    monkeypatch.setattr(charges_service, "lock_unit_path", deleted_meanwhile)
    monkeypatch.setattr(charges_service, "_settle_credit", broken)
    body = (await confirm(client, headers)).json()

    assert (body["units_evaluated"], body["created"], body["omitted"], body["failed"]) == (3, 1, 1, 1)
    assert body["omitted_units"] == [{"unit_id": units["b"], "label": "B1-TO1-APTO-102", "code": "UNIT_DELETED",
                                      "message": "El apartamento fue eliminado."}]
    assert body["errors"] == [{"unit_id": units["c"], "label": "B2-TO1-APTO-101", "code": "INTERNAL_ERROR",
                               "message": "No fue posible generar la mensualidad de este apartamento."}]


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"expected_to_create": -1}, "expected_to_create"), ({"expected_to_create": "3"}, "expected_to_create"),
        ({"preview_fingerprint": "abc"}, "preview_fingerprint"), ({"preview_fingerprint": "Z" * 64}, "preview_fingerprint"),
        ({"billing_period": "2026-1"}, "billing_period"), ({"amount": "-5"}, "amount"),
        ({"created": 10}, "created"), ({"preview_fingerprint": None}, "preview_fingerprint"),
    ],
)
async def test_invalid_generation(client: AsyncClient, headers: dict[str, str], units: dict[str, int],
                                  db_session: AsyncSession, changes: dict[str, Any], field: str) -> None:
    body = {**PREVIEW, "expected_to_create": 3, "preview_fingerprint": "0" * 64, **changes}
    response = await post(client, headers, "/monthly", body)
    assert response.status_code == 422
    assert [d["field"] for d in details(response)] == [field]
    assert await count(db_session, Charge) == 0


# --- Individual obligation -----------------------------------------------------------------------------


async def test_the_four_concepts(client: AsyncClient, headers: dict[str, str], units: dict[str, int], admin: User) -> None:
    monthly = await create(client, headers, {"unit_id": units["a"], "concept": "MONTHLY_FEE", "amount": "180000",
                                             "due_date": "2026-09-10", "billing_period": "2026-09"})
    assert (monthly["concept"], monthly["billing_period"], monthly["description"], monthly["status"]) == (
        "MONTHLY_FEE", "2026-09", None, "OVERDUE")
    historical = await create(client, headers, {"unit_id": units["a"], "concept": "LATE_FEE", "amount": "5000",
                                                "due_date": "2026-10-20"})
    assert historical["source_charge_id"] is None
    linked = await create(client, headers, {"unit_id": units["a"], "concept": "LATE_FEE", "amount": "9000",
                                            "due_date": "2026-10-20", "source_charge_id": monthly["id"],
                                            "description": "Mora de septiembre"})
    assert (linked["source_charge_id"], linked["description"]) == (monthly["id"], "Mora de septiembre")
    penalty = await create(client, headers, charge_body(units["b"], "PENALTY"))
    other = await create(client, headers, charge_body(units["b"], "OTHER", amount="1000.50"))
    assert (penalty["concept"], other["concept"], other["amount"]) == ("PENALTY", "OTHER", "1000.50")
    assert {c["created_by_user_id"] for c in (monthly, historical, linked, penalty, other)} == {admin.id}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|\+00:00)", monthly["created_at"])  # UTC, explicit


async def test_creation_consumes_credit(
    client: AsyncClient, headers: dict[str, str], units: dict[str, int], db_session: AsyncSession, admin: User, make_user: Any
) -> None:
    payment = await credit(db_session, units["a"], admin, "30000", make_user)
    body = await create(client, headers, charge_body(units["a"], amount="50000"))
    assert (body["applied"], body["outstanding"], body["status"]) == ("30000.00", "20000.00", "PARTIAL")
    assert body["credit_applied"] == [{"payment_id": payment, "receipt_number": f"RC-{payment:08d}",
                                       "charge_id": body["id"], "amount": "30000.00"}]
    assert body["account"] == {"pending_balance": "20000.00", "credit_balance": "0.00"}
    assert await db_session.scalar(select(func.sum(PaymentAllocation.amount))) == Decimal("30000.00")


async def test_domain_errors(client: AsyncClient, headers: dict[str, str], units: dict[str, int]) -> None:
    origin = await create(client, headers, charge_body(units["b"], due="2026-09-01"))
    response = await post(client, headers, "", {"unit_id": units["a"], "concept": "LATE_FEE", "amount": "1",
                                                 "due_date": "2026-10-20", "source_charge_id": origin["id"]})
    assert response.status_code == 422 and details(response) == [
        {"field": "source_charge_id", "message": "La obligación de origen es de otro apartamento."}]
    historical = await create(client, headers, {"unit_id": units["a"], "concept": "LATE_FEE", "amount": "1",
                                                "due_date": "2026-09-01"})
    response = await post(client, headers, "", {"unit_id": units["a"], "concept": "LATE_FEE", "amount": "1",
                                                 "due_date": "2026-10-20", "source_charge_id": historical["id"]})
    assert response.status_code == 422 and details(response)[0]["field"] == "source_charge_id"
    fee = {"unit_id": units["a"], "concept": "MONTHLY_FEE", "amount": "1", "due_date": "2026-10-10", "billing_period": "2026-10"}
    await create(client, headers, fee)
    duplicate = await post(client, headers, "", fee)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "MONTHLY_FEE_EXISTS"
    late = {"unit_id": units["b"], "concept": "LATE_FEE", "amount": "1", "due_date": "2026-10-20", "source_charge_id": origin["id"]}
    await create(client, headers, late)
    again = await post(client, headers, "", late)
    assert again.status_code == 409 and again.json()["error"]["code"] == "LATE_FEE_EXISTS"
    missing_unit = await post(client, headers, "", charge_body(999999))
    assert missing_unit.status_code == 422 and details(missing_unit)[0]["field"] == "unit_id"


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"amount": "0"}, "amount"), ({"amount": "1e3"}, "amount"), ({"amount": None}, "amount"),
        ({"due_date": "2026/10/20"}, "due_date"), ({"concept": "RENT"}, "concept"), ({"unit_id": "1"}, "unit_id"),
        ({"unit_id": 0}, "unit_id"), ({"billing_period": "2026-10-01"}, "billing_period"),
        ({"source_charge_id": 0}, "source_charge_id"), ({"description": 5}, "description"),
        ({"description": None}, "description"),  # service rule: PENALTY needs a description
        ({"billing_period": "2026-10"}, "billing_period"),  # service rule: only a monthly fee has a period
    ],
)
async def test_invalid_charge(client: AsyncClient, headers: dict[str, str], units: dict[str, int],
                              db_session: AsyncSession, changes: dict[str, Any], field: str) -> None:
    response = await post(client, headers, "", charge_body(units["a"]) | changes)
    assert response.status_code == 422
    assert [d["field"] for d in details(response)] == [field]
    assert await count(db_session, Charge) == 0


@pytest.mark.parametrize("field", ["created_by_user_id", "status", "applied", "outstanding", "credit_applied",
                                   "account", "applied_amount", "id"])
async def test_computed_or_server_values_are_rejected(
    client: AsyncClient, headers: dict[str, str], units: dict[str, int], db_session: AsyncSession, field: str
) -> None:
    response = await post(client, headers, "", charge_body(units["a"]) | {field: 1})
    assert response.status_code == 422 and details(response) == [{"field": field, "message": NOT_ALLOWED}]
    assert await count(db_session, Charge) == 0


# --- List ----------------------------------------------------------------------------------------------


@pytest.fixture
async def listed(client: AsyncClient, headers: dict[str, str], units: dict[str, int], db_session: AsyncSession) -> dict[str, Any]:
    c = {
        "a_sep": await create(client, headers, {"unit_id": units["a"], "concept": "MONTHLY_FEE", "amount": "100000",
                                                "due_date": "2026-09-05", "billing_period": "2026-09"}),
        "a_oct": await create(client, headers, {"unit_id": units["a"], "concept": "MONTHLY_FEE", "amount": "100000",
                                                "due_date": "2026-10-20", "billing_period": "2026-10"}),
        "b_pen": await create(client, headers, charge_body(units["b"], amount="30000", due="2026-11-01")),
        "c_oth": await create(client, headers, charge_body(units["c"], "OTHER", amount="20000", due="2026-10-15")),
    }
    tower = await db_session.scalar(select(Unit.tower_id).where(Unit.id == units["c"]))
    block = await db_session.scalar(text("SELECT block_id FROM towers WHERE id = :t"), {"t": tower})
    return {"ids": {k: v["id"] for k, v in c.items()}, "tower_c": tower, "block_c": block}


async def names(client: AsyncClient, listed: dict[str, Any], **params: Any) -> list[str]:
    response = await get(client, **params)
    assert response.status_code == 200, response.text
    by_id = {v: k for k, v in listed["ids"].items()}
    return [by_id[i["id"]] for i in response.json()["items"]]


async def test_list_rows(client: AsyncClient, listed: dict[str, Any], units: dict[str, int]) -> None:
    body = (await get(client, unit_id=units["a"])).json()
    assert body["total"] == 2 and body["total_pages"] == 1
    first = body["items"][0]
    created_at = first.pop("created_at")
    assert first == {"id": listed["ids"]["a_sep"], "unit_id": units["a"], "unit_label": "B1-TO1-APTO-101",
                     "unit_deleted": False, "concept": "MONTHLY_FEE", "description": None, "billing_period": "2026-09",
                     "due_date": "2026-09-05", "amount": "100000.00", "applied": "0.00", "outstanding": "100000.00",
                     "status": "OVERDUE", "source_charge_id": None}
    assert created_at.endswith(("Z", "+00:00"))


async def test_list_filters(client: AsyncClient, listed: dict[str, Any], units: dict[str, int]) -> None:
    assert await names(client, listed, unit_id=units["a"]) == ["a_sep", "a_oct"]
    assert await names(client, listed, tower_id=listed["tower_c"]) == ["c_oth"]
    assert await names(client, listed, block_id=listed["block_c"]) == ["c_oth"]
    assert await names(client, listed, concept="PENALTY") == ["b_pen"]
    assert await names(client, listed, status="OVERDUE") == ["a_sep"]
    assert await names(client, listed, status="PENDING") == ["c_oth", "a_oct", "b_pen"]  # due today: not overdue
    assert await names(client, listed, billing_period="2026-10") == ["a_oct"]
    assert await names(client, listed, due_from="2026-10-15", due_to="2026-10-20") == ["c_oth", "a_oct"]
    assert await names(client, listed, unit_id=units["a"], status="PENDING", billing_period="2026-10") == ["a_oct"]
    assert await names(client, listed, unit_id=units["b"], concept="MONTHLY_FEE") == []


@pytest.mark.parametrize("sort_by", ["due_date", "unit", "concept", "amount", "outstanding", "status", "created"])
async def test_list_orders(client: AsyncClient, listed: dict[str, Any], sort_by: str) -> None:
    ascending = await names(client, listed, sort_by=sort_by, sort_order="asc")
    descending = await names(client, listed, sort_by=sort_by, sort_order="desc")
    assert sorted(ascending) == sorted(descending) == sorted(listed["ids"]) and len(ascending) == 4
    if sort_by == "amount":
        assert ascending == ["c_oth", "b_pen", "a_sep", "a_oct"] and descending == ["a_oct", "a_sep", "b_pen", "c_oth"]


async def test_list_pagination_and_empty(client: AsyncClient, listed: dict[str, Any]) -> None:
    first = (await get(client, page=1, page_size=3)).json()
    second = (await get(client, page=2, page_size=3)).json()
    beyond = (await get(client, page=3, page_size=3)).json()
    assert (first["total"], first["total_pages"], len(first["items"]), len(second["items"])) == (4, 2, 3, 1)
    assert (beyond["items"], beyond["total"], beyond["page"]) == ([], 4, 3)
    assert {i["id"] for i in first["items"] + second["items"]} == set(listed["ids"].values())
    assert (await get(client, concept="LATE_FEE")).json() == {"items": [], "page": 1, "page_size": 10, "total": 0,
                                                               "total_pages": 0}


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"sort_by": "id; DROP TABLE charges"}, "query.sort_by"), ({"sort_by": "description"}, "query.sort_by"),
        ({"sort_order": "sideways"}, "query.sort_order"), ({"page": 0}, "query.page"), ({"page_size": 101}, "query.page_size"),
        ({"status": "PAGADA"}, "query.status"), ({"concept": "RENT"}, "query.concept"),
        ({"billing_period": "2026-13"}, "query.billing_period"), ({"due_from": "2026-10-02", "due_to": "2026-10-01"}, "query.due_to"),
        ({"search": "Laura"}, "query.search"),  # no free search in this step
        ({"unit_id": "abc"}, "query.unit_id"),
    ],
)
async def test_invalid_list_query(client: AsyncClient, headers: dict[str, str], params: dict[str, Any], field: str) -> None:
    response = await get(client, **params)
    assert response.status_code == 422
    assert [d["field"] for d in details(response)] == [field]


@pytest.fixture
def statements() -> Iterator[list[str]]:
    seen: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


async def test_the_api_adds_no_query_per_row(
    client: AsyncClient, headers: dict[str, str], units: dict[str, int], statements: list[str]
) -> None:
    await create(client, headers, charge_body(units["a"]))
    statements.clear()
    await get(client)
    with_one = len(statements)
    for n in range(9):
        await create(client, headers, charge_body(units["b"], amount=str(1000 + n)))
    statements.clear()
    await get(client)
    assert len(statements) == with_one  # 10 rows, the same statements as 1 row


# --- Detail --------------------------------------------------------------------------------------------


async def test_detail(
    client: AsyncClient, headers: dict[str, str], units: dict[str, int], db_session: AsyncSession, admin: User, make_user: Any
) -> None:
    payment = await credit(db_session, units["a"], admin, "40000", make_user)
    origin = await create(client, headers, {"unit_id": units["a"], "concept": "MONTHLY_FEE", "amount": "100000",
                                            "due_date": "2026-09-05", "billing_period": "2026-09"})
    late = await create(client, headers, {"unit_id": units["a"], "concept": "LATE_FEE", "amount": "5000",
                                          "due_date": "2026-10-20", "source_charge_id": origin["id"]})

    body = (await get(client, f"/{origin['id']}")).json()
    allocation = body["allocations"][0]
    assert allocation.pop("allocation_date").endswith(("Z", "+00:00"))
    assert allocation == {"payment_id": payment, "receipt_number": f"RC-{payment:08d}", "payment_date": "2026-09-01",
                          "payment_method": "TRANSFER", "applied_amount": "40000.00"}
    assert (body["applied"], body["outstanding"], body["status"], body["created_by_user_id"]) == (
        "40000.00", "60000.00", "OVERDUE", admin.id)
    assert (body["source"], [f["id"] for f in body["late_fees"]]) == (None, [late["id"]])

    child = (await get(client, f"/{late['id']}")).json()
    assert child["source"]["id"] == origin["id"] and child["source"]["unit_label"] == "B1-TO1-APTO-101"
    assert child["late_fees"] == [] and child["allocations"] == []


async def test_detail_of_a_deleted_apartment(
    client: AsyncClient, headers: dict[str, str], units: dict[str, int], db_session: AsyncSession
) -> None:
    created = await create(client, headers, charge_body(units["c"]))
    await db_session.execute(update(Unit).where(Unit.id == units["c"]).values(deleted_at=datetime(2026, 10, 14)))
    body = (await get(client, f"/{created['id']}")).json()
    assert (body["unit_label"], body["unit_deleted"]) == ("B2-TO1-APTO-101", True)


async def test_missing_and_invalid_ids(client: AsyncClient, headers: dict[str, str]) -> None:
    missing = await get(client, "/999999")
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "CHARGE_NOT_FOUND", "message": "La obligación no existe.", "details": []}}
    for invalid in ("0", "-1", "abc", str(2**64)):
        assert (await get(client, f"/{invalid}")).status_code == 422


async def test_unit_has_balance_still_409_on_the_structure_endpoint(
    client: AsyncClient, headers: dict[str, str], units: dict[str, int]
) -> None:
    await create(client, headers, charge_body(units["a"]))
    response = await client.delete(f"/api/v1/units/{units['a']}", params={"version": 1}, headers=headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "UNIT_HAS_BALANCE"


# --- Cache-Control: no-store on every answer, success or error --------------------------------------------


async def statuses(client: AsyncClient, calls: list[tuple[str, int, Any]]) -> None:
    """Each call: (case, expected status, coroutine) → expected status and no-store."""
    for case, expected, request in calls:
        response = await request
        assert response.status_code == expected, (case, response.text)
        assert response.headers.get("cache-control") == "no-store", (case, response.status_code)


async def as_resident(client: AsyncClient, db_session: AsyncSession, make_user: Any) -> dict[str, str]:
    resident = await db_session.scalar(select(User).where(User.role == UserRole.RESIDENTE))
    if resident is None:
        resident = await make_user("residente@example.com", role=UserRole.RESIDENTE, password_hash="x")
    return await sign_in(client, db_session, resident)


async def test_no_store_on_preview(client: AsyncClient, db_session: AsyncSession, admin: User, make_user: Any,
                                   units: dict[str, int]) -> None:
    await statuses(client, [("401", 401, client.get(f"{API}/monthly/preview", params=PREVIEW))])
    await as_resident(client, db_session, make_user)
    await statuses(client, [("403", 403, client.get(f"{API}/monthly/preview", params=PREVIEW))])
    await sign_in(client, db_session, admin)
    await statuses(client, [
        ("200", 200, client.get(f"{API}/monthly/preview", params=PREVIEW)),
        ("422 format", 422, client.get(f"{API}/monthly/preview", params={**PREVIEW, "amount": "0"})),
        ("422 service rule", 422, client.get(f"{API}/monthly/preview", params={**PREVIEW, "due_date": "2026-09-30"})),
        ("422 unknown parameter", 422, client.get(f"{API}/monthly/preview", params={**PREVIEW, "x": "1"})),
    ])


async def test_no_store_on_monthly_generation(client: AsyncClient, db_session: AsyncSession, admin: User, make_user: Any,
                                              origin_headers: dict[str, str], units: dict[str, int]) -> None:
    stale = {**PREVIEW, "expected_to_create": 3, "preview_fingerprint": "0" * 64}
    await statuses(client, [("401", 401, client.post(f"{API}/monthly", json=stale, headers=origin_headers))])
    resident = await as_resident(client, db_session, make_user)
    await statuses(client, [("403", 403, client.post(f"{API}/monthly", json=stale, headers=resident))])
    headers = await sign_in(client, db_session, admin)
    no_origin = {k: v for k, v in headers.items() if k != "Origin"}
    await statuses(client, [
        ("403 without Origin", 403, client.post(f"{API}/monthly", json=stale, headers=no_origin)),
        ("403 invalid Origin", 403, client.post(f"{API}/monthly", json=stale, headers={**headers, "Origin": "https://evil.example"})),
        ("403 invalid CSRF", 403, client.post(f"{API}/monthly", json=stale, headers={**headers, CSRF_HEADER_NAME: "x"})),
        ("409 outdated", 409, client.post(f"{API}/monthly", json=stale, headers=headers)),
        ("422", 422, client.post(f"{API}/monthly", json={**stale, "expected_to_create": -1}, headers=headers)),
        ("422 malformed JSON", 422, client.post(f"{API}/monthly", content=b"{", headers={**headers, "Content-Type": "application/json"})),
    ])
    await statuses(client, [("200", 200, confirm(client, headers))])


async def test_no_store_on_charge_creation(client: AsyncClient, db_session: AsyncSession, admin: User, make_user: Any,
                                           origin_headers: dict[str, str], units: dict[str, int]) -> None:
    body = charge_body(units["a"])
    await statuses(client, [("401", 401, client.post(API, json=body, headers=origin_headers))])
    resident = await as_resident(client, db_session, make_user)
    await statuses(client, [("403", 403, client.post(API, json=body, headers=resident))])
    headers = await sign_in(client, db_session, admin)
    fee = {"unit_id": units["b"], "concept": "MONTHLY_FEE", "amount": "1", "due_date": "2026-10-10", "billing_period": "2026-10"}
    await statuses(client, [
        ("403 CSRF", 403, client.post(API, json=body, headers={**headers, CSRF_HEADER_NAME: "x"})),
        ("201", 201, client.post(API, json=body, headers=headers)),
        ("201 monthly", 201, client.post(API, json=fee, headers=headers)),
        ("409 duplicate", 409, client.post(API, json=fee, headers=headers)),
        ("422 format", 422, client.post(API, json={**body, "amount": "x"}, headers=headers)),
        ("422 forbidden field", 422, client.post(API, json={**body, "status": "PAID"}, headers=headers)),
        ("422 service rule", 422, client.post(API, json=charge_body(999999), headers=headers)),
    ])


async def test_no_store_on_list(client: AsyncClient, db_session: AsyncSession, admin: User, make_user: Any) -> None:
    await statuses(client, [("401", 401, client.get(API))])
    await as_resident(client, db_session, make_user)
    await statuses(client, [("403", 403, client.get(API))])
    await sign_in(client, db_session, admin)
    await statuses(client, [
        ("200", 200, client.get(API)),
        ("422", 422, client.get(API, params={"sort_by": "id; DROP TABLE charges"})),
    ])


async def test_no_store_on_detail(client: AsyncClient, db_session: AsyncSession, admin: User, make_user: Any,
                                  units: dict[str, int]) -> None:
    await statuses(client, [("401", 401, client.get(f"{API}/1"))])
    await as_resident(client, db_session, make_user)
    await statuses(client, [("403", 403, client.get(f"{API}/1"))])
    headers = await sign_in(client, db_session, admin)
    charge_id = (await create(client, headers, charge_body(units["a"])))["id"]
    await statuses(client, [
        ("200", 200, client.get(f"{API}/{charge_id}")),
        ("404 CHARGE_NOT_FOUND", 404, client.get(f"{API}/999999")),
        ("422 invalid id", 422, client.get(f"{API}/abc")),
    ])


async def test_error_bodies_are_unchanged(client: AsyncClient, headers: dict[str, str]) -> None:
    # The same JSON as the global handlers: only the header is added.
    client.cookies.clear()
    assert (await client.get(API)).json() == {
        "error": {"code": "NOT_AUTHENTICATED", "message": "Debes iniciar sesión para continuar.", "details": []}}


async def test_other_apis_are_unchanged(client: AsyncClient, db_session: AsyncSession, admin: User) -> None:
    # Only /admin/charges uses the no-store route class: an error of another API keeps its headers.
    client.cookies.clear()
    assert "cache-control" not in (await client.get("/api/v1/admin/payments")).headers
    await sign_in(client, db_session, admin)
    assert "cache-control" not in (await client.get("/api/v1/admin/payments", params={"x": "1"})).headers
    assert (await client.get("/api/v1/admin/payments")).headers["cache-control"] == "no-store"  # its own, as before
