"""HU-009: POST/GET /api/v1/resident/feedback and GET /{feedback_id}, on the test database.

The world of the service tests (tests/integration/test_resident_feedback_query.py):
Laura has f1, f2 (resolved, with a response and an internal note), f3 (same
instant as f2) and f4 (stored before HU-009: no category, no urgency); Pedro has p1.
"""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sessions import CSRF_HEADER_NAME
from app.models import AuditAction, AuditLog, Feedback, User
from tests.integration import test_resident_feedback_query as service_tests
from tests.integration.conftest import FakeMailSender, sign_in
from tests.integration.test_resident_feedback_query import NOTE, RESPONSE

pytestmark = pytest.mark.anyio

API = "/api/v1/resident/feedback"
FORBIDDEN = {"error": {"code": "FORBIDDEN", "message": "No tienes permiso para realizar esta acción.", "details": []}}
NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "El feedback no existe.", "details": []}}
NOT_ALLOWED = "Este campo no está permitido."
CATEGORY_MESSAGE = "Selecciona una categoría: queja, sugerencia, solicitud u otros."
URGENCY_MESSAGE = "Selecciona la urgencia: alta, media o baja."
ITEM_KEYS = {"id", "subject", "category", "urgency", "status", "created_at"}
# The detail adds what the administration told the resident (HU-005 3.2, HU-017 1); never the internal note.
ADMINISTRATION_KEYS = {"admin_response", "resolved_at"}
DETAIL_KEYS = ITEM_KEYS | {"message"} | ADMINISTRATION_KEYS
SENSITIVE_KEYS = {"resident_id", "resident", "resolution_note", "version", "ip_address", "audit", "action_history",
                  "updated_at"}
BODY = {"subject": "Ruido", "message": "Hay ruido después de las 10 p. m.", "category": "COMPLAINT", "urgency": "HIGH"}

world = service_tests.world  # the same fixture


async def as_user(client: AsyncClient, db: AsyncSession, user_id: int) -> dict[str, str]:
    user = await db.get(User, user_id)
    assert user is not None
    return await sign_in(client, db, user)


@pytest.fixture
async def laura(client: AsyncClient, db_session: AsyncSession, world: dict[str, int]) -> dict[str, str]:
    """Laura signed in: the write headers (Origin + CSRF token)."""
    return await as_user(client, db_session, world["laura"])


async def post(client: AsyncClient, headers: dict[str, str], body: Any = None, **changes: Any) -> Response:
    return await client.post(API, json={**BODY, **changes} if body is None else body, headers=headers)


def details(response: Response) -> list[dict[str, str]]:
    return response.json()["error"]["details"]


def keys_of(value: Any) -> set[str]:
    """Every key anywhere in a JSON document."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


async def count(db: AsyncSession, table: str) -> int:
    return int(await db.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)


# --- POST ----------------------------------------------------------------------------------------------------


async def test_1_8_the_resident_sends_a_feedback(client: AsyncClient, db_session: AsyncSession, world: dict[str, int],
                                                 laura: dict[str, str], mailer: FakeMailSender) -> None:
    response = await post(client, laura, subject="  Portería  ", message="  Hola.\nNo funciona el citófono.  ",
                          category="REQUEST", urgency="LOW")

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == DETAIL_KEYS
    created_at = body.pop("created_at")
    assert created_at.endswith(("Z", "+00:00"))
    feedback_id = body.pop("id")
    assert body == {"subject": "Portería", "message": "Hola.\nNo funciona el citófono.", "category": "REQUEST",
                    "urgency": "LOW", "status": "PENDING", "admin_response": None, "resolved_at": None}
    row = (await db_session.execute(select(Feedback).where(Feedback.id == feedback_id))).scalar_one()
    assert (row.resident_id, row.status, row.category, row.urgency) == (world["laura"], "PENDING", "REQUEST", "LOW")
    assert (row.admin_response, row.resolution_note, row.resolved_at, row.version) == (None, None, None, 1)
    # The answer is what the detail shows; no email is sent (HU-017 will centralize notifications).
    assert (await client.get(f"{API}/{feedback_id}")).json() == {**body, "id": feedback_id, "created_at": created_at}
    assert mailer.sent == []


async def test_3_the_owner_is_the_sessions_resident(client: AsyncClient, db_session: AsyncSession, world: dict[str, int],
                                                    laura: dict[str, str]) -> None:
    client.cookies.set("resident_id", str(world["pedro"]))
    headers = {**laura, "X-Resident-Id": str(world["pedro"]), "X-User-Id": str(world["pedro"])}
    response = await client.post(f"{API}?resident_id={world['pedro']}", json=BODY, headers=headers)
    assert response.status_code == 201, response.text
    owner = await db_session.scalar(select(Feedback.resident_id).where(Feedback.id == response.json()["id"]))
    assert owner == world["laura"]


@pytest.mark.parametrize("subject", [None, "", "   "])
async def test_6_subject_is_optional(client: AsyncClient, laura: dict[str, str], subject: Any) -> None:
    response = await post(client, laura, subject=subject)
    assert response.status_code == 201 and response.json()["subject"] is None
    without = {k: v for k, v in BODY.items() if k != "subject"}
    assert (await post(client, laura, body=without)).json()["subject"] is None


async def test_9_limits(client: AsyncClient, laura: dict[str, str]) -> None:
    accepted = await post(client, laura, subject="s" * 150, message="m" * 2000)
    assert accepted.status_code == 201
    assert (len(accepted.json()["subject"]), len(accepted.json()["message"])) == (150, 2000)


async def test_10_12_created_once_audited_with_the_ip_never_returned(
    client: AsyncClient, db_session: AsyncSession, world: dict[str, int], laura: dict[str, str]
) -> None:
    audits_before = await count(db_session, "audit_logs")
    response = await post(client, laura)
    feedback_id = response.json()["id"]

    assert await count(db_session, "audit_logs") == audits_before + 1
    [entry] = (await db_session.scalars(select(AuditLog).where(AuditLog.entity_type == "feedback",
                                                               AuditLog.entity_id == feedback_id))).all()
    assert (entry.action, entry.actor_user_id, entry.ip_address) == (AuditAction.FEEDBACK_CREATED, world["laura"],
                                                                     "127.0.0.1")  # client_ip(request), as HU-005
    assert "127.0.0.1" not in response.text and not keys_of(response.json()) & SENSITIVE_KEYS


@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        ({"message": None}, "message", "Escribe el mensaje."),
        ({"message": ""}, "message", "Escribe el mensaje."),
        ({"message": " \n "}, "message", "Escribe el mensaje."),
        ({"message": "m" * 2001}, "message", "El mensaje no puede superar 2000 caracteres."),
        ({"message": 12}, "message", "El mensaje debe ser un texto."),
        ({"subject": "s" * 151}, "subject", "El asunto no puede superar 150 caracteres."),
        ({"subject": 12}, "subject", "El asunto debe ser un texto."),
        ({"category": None}, "category", CATEGORY_MESSAGE),
        ({"category": "Queja"}, "category", CATEGORY_MESSAGE),
        ({"category": "complaint"}, "category", CATEGORY_MESSAGE),
        ({"category": "HIGH"}, "category", CATEGORY_MESSAGE),
        ({"urgency": None}, "urgency", URGENCY_MESSAGE),
        ({"urgency": "Alta"}, "urgency", URGENCY_MESSAGE),
        ({"urgency": "high"}, "urgency", URGENCY_MESSAGE),
        ({"urgency": "URGENT"}, "urgency", URGENCY_MESSAGE),
    ],
)
async def test_7_9_18_21_invalid_fields_are_422(client: AsyncClient, db_session: AsyncSession, laura: dict[str, str],
                                                changes: dict[str, Any], field: str, message: str) -> None:
    before = await count(db_session, "feedback"), await count(db_session, "audit_logs")
    response = await post(client, laura, **changes)
    assert response.status_code == 422
    assert details(response) == [{"field": field, "message": message}]
    assert (await count(db_session, "feedback"), await count(db_session, "audit_logs")) == before


@pytest.mark.parametrize("missing", ["message", "category", "urgency"])
async def test_7_20_21_missing_fields_are_422(client: AsyncClient, laura: dict[str, str], missing: str) -> None:
    response = await post(client, laura, body={k: v for k, v in BODY.items() if k != missing})
    assert response.status_code == 422
    expected = {"message": "Escribe el mensaje.", "category": CATEGORY_MESSAGE, "urgency": URGENCY_MESSAGE}[missing]
    assert details(response) == [{"field": missing, "message": expected}]


async def test_every_invalid_field_is_reported_at_once(client: AsyncClient, laura: dict[str, str]) -> None:
    response = await post(client, laura, body={"subject": "s" * 151})
    assert [d["field"] for d in details(response)] == ["subject", "message", "category", "urgency"]


@pytest.mark.parametrize(
    "field", ["status", "resident_id", "user_id", "unit_id", "resolution_note", "admin_response", "resolved_at",
              "version", "created_at", "id", "extra"],
)
async def test_13_17_fields_the_client_never_sets_are_422(client: AsyncClient, db_session: AsyncSession,
                                                          laura: dict[str, str], field: str) -> None:
    value = {"status": "RESOLVED", "resident_id": 1, "user_id": 1, "unit_id": 1, "version": 9, "id": 1}.get(field, "x")
    response = await post(client, laura, **{field: value})
    assert response.status_code == 422
    assert details(response) == [{"field": field, "message": NOT_ALLOWED}]
    assert await count(db_session, "feedback") == 5  # the world's only


async def test_22_23_csrf_and_origin(client: AsyncClient, db_session: AsyncSession, laura: dict[str, str]) -> None:
    attempts = [
        {k: v for k, v in laura.items() if k != CSRF_HEADER_NAME},  # no CSRF token
        {**laura, CSRF_HEADER_NAME: "x" * 43},  # another token
        {k: v for k, v in laura.items() if k != "Origin"},  # no Origin
        {**laura, "Origin": "https://evil.example.com"},  # another origin
    ]
    for headers in attempts:
        response = await client.post(API, json=BODY, headers=headers)
        assert response.status_code == 403, headers
        assert response.json()["error"]["code"] == "CSRF_FAILED"
        assert response.headers["cache-control"] == "no-store"
    assert await count(db_session, "feedback") == 5


async def test_24_25_administrator_and_anonymous(client: AsyncClient, db_session: AsyncSession, world: dict[str, int],
                                                 origin_headers: dict[str, str]) -> None:
    anonymous = await client.post(API, json=BODY, headers=origin_headers)
    assert anonymous.status_code == 401 and anonymous.json()["error"]["code"] == "NOT_AUTHENTICATED"
    headers = await as_user(client, db_session, world["admin"])
    # The role decides first: even an invalid body gets the 403.
    for body in (BODY, {"status": "RESOLVED"}):
        response = await client.post(API, json=body, headers=headers)
        assert (response.status_code, response.json()) == (403, FORBIDDEN)
    assert await count(db_session, "feedback") == 5


async def test_26_every_post_answer_is_no_store(client: AsyncClient, laura: dict[str, str]) -> None:
    for response in (await post(client, laura), await post(client, laura, category="Queja"),
                     await client.post(API, json=BODY, headers={"Origin": laura["Origin"]})):
        assert response.headers["cache-control"] == "no-store", response.status_code


# --- GET list ------------------------------------------------------------------------------------------------


async def ids(client: AsyncClient, **params: Any) -> list[int]:
    response = await client.get(API, params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


async def test_27_30_only_the_residents_feedback_newest_first(client: AsyncClient, db_session: AsyncSession,
                                                              world: dict[str, int], laura: dict[str, str]) -> None:
    w = world
    assert await ids(client) == [w["f3"], w["f2"], w["f1"], w["f4"]]
    await as_user(client, db_session, w["pedro"])
    assert await ids(client) == [w["p1"]]


async def test_31_33_pagination(client: AsyncClient, world: dict[str, int], laura: dict[str, str]) -> None:
    w = world
    first = (await client.get(API, params={"page_size": 3})).json()
    assert ([i["id"] for i in first["items"]], first["page"], first["page_size"], first["total"], first["total_pages"]) == (
        [w["f3"], w["f2"], w["f1"]], 1, 3, 4, 2)
    second = (await client.get(API, params={"page": 2, "page_size": 3})).json()
    assert ([i["id"] for i in second["items"]], second["page"]) == ([w["f4"]], 2)
    beyond = (await client.get(API, params={"page": 3, "page_size": 3})).json()
    assert (beyond["items"], beyond["total"], beyond["total_pages"]) == ([], 4, 2)
    assert (await client.get(API)).json()["page_size"] == 10


async def test_34_a_resident_without_feedback(client: AsyncClient, db_session: AsyncSession, make_user: Any,
                                              world: dict[str, int]) -> None:
    newcomer = await make_user("nuevo@example.com", role="RESIDENTE", password_hash="not-a-real-hash")
    await sign_in(client, db_session, newcomer)
    assert (await client.get(API)).json() == {"items": [], "page": 1, "page_size": 10, "total": 0, "total_pages": 0}


async def test_35_list_items_and_historical_nulls(client: AsyncClient, world: dict[str, int], laura: dict[str, str]) -> None:
    items = {item["id"]: item for item in (await client.get(API)).json()["items"]}
    assert all(set(item) == ITEM_KEYS for item in items.values())  # no message, nothing administrative
    f1 = items[world["f1"]]
    assert f1["created_at"].startswith("2026-10-03T15:00:00") and f1["created_at"].endswith(("Z", "+00:00"))
    assert {k: f1[k] for k in ("subject", "category", "urgency", "status")} == {
        "subject": "Ruido", "category": "COMPLAINT", "urgency": "HIGH", "status": "PENDING"}
    assert (items[world["f2"]]["status"], items[world["f2"]]["subject"]) == ("RESOLVED", None)
    # Stored before HU-009: null, never an invented category or urgency.
    assert (items[world["f4"]]["category"], items[world["f4"]]["urgency"]) == (None, None)


@pytest.mark.parametrize(
    ("params", "field"),
    [({"page": 0}, "query.page"), ({"page": "abc"}, "query.page"), ({"page": 10_001}, "query.page"),
     ({"page_size": 0}, "query.page_size"), ({"page_size": 101}, "query.page_size"),
     ({"status": "PENDING"}, "query.status"), ({"search": "Ruido"}, "query.search"),
     ({"resident_id": 1}, "query.resident_id"), ({"user_id": 1}, "query.user_id"), ({"unit_id": 1}, "query.unit_id")],
)
async def test_37_invalid_or_unknown_query_is_422(client: AsyncClient, laura: dict[str, str], params: dict[str, Any],
                                                  field: str) -> None:
    response = await client.get(API, params=params)
    assert response.status_code == 422
    assert [d["field"] for d in details(response)] == [field]
    assert response.headers["cache-control"] == "no-store"


# --- GET detail ----------------------------------------------------------------------------------------------


async def test_40_44_own_feedback(client: AsyncClient, world: dict[str, int], laura: dict[str, str]) -> None:
    response = await client.get(f"{API}/{world['f1']}")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == DETAIL_KEYS
    assert body["created_at"].startswith("2026-10-03T15:00:00")
    assert {k: v for k, v in body.items() if k != "created_at"} == {
        "id": world["f1"], "subject": "Ruido", "message": "Hay ruido después de las 10 p. m.", "category": "COMPLAINT",
        "urgency": "HIGH", "status": "PENDING", "admin_response": None, "resolved_at": None}
    historical = (await client.get(f"{API}/{world['f4']}")).json()
    assert (historical["category"], historical["urgency"], historical["message"]) == (None, None, "Mensaje anterior.")


async def test_41_43_foreign_and_missing_look_exactly_the_same(client: AsyncClient, db_session: AsyncSession,
                                                               world: dict[str, int], laura: dict[str, str]) -> None:
    missing = int(await db_session.scalar(select(func.max(Feedback.id))) or 0) + 1000
    foreign, absent = await client.get(f"{API}/{world['p1']}"), await client.get(f"{API}/{missing}")
    assert (foreign.status_code, foreign.json()) == (absent.status_code, absent.json()) == (404, NOT_FOUND)
    assert foreign.content == absent.content
    assert {k: v for k, v in foreign.headers.items() if k != "content-length"} == {
        k: v for k, v in absent.headers.items() if k != "content-length"}
    assert "agua" not in foreign.text.lower()


async def test_45_48_responses_never_carry_the_internal_note_or_sensitive_keys(
    client: AsyncClient, db_session: AsyncSession, world: dict[str, int], laura: dict[str, str]
) -> None:
    resolved = await client.get(f"{API}/{world['f2']}")  # resolved: it HAS a response, a note and a date
    listed = await client.get(API, params={"page_size": 100})
    created = await post(client, laura)
    admin_email = await db_session.scalar(select(User.email).where(User.id == world["admin"]))
    for response in (resolved, listed, created):
        assert not keys_of(response.json()) & SENSITIVE_KEYS, response.request.url
        for value in (NOTE, "Laura", "laura@example.com", "Pedro", admin_email):
            assert value not in response.text, value  # type: ignore[operator]
    # HU-005 3.2 / HU-017 1: the detail shows the response and the resolution date (UTC); the list never does.
    detail = resolved.json()
    assert detail["admin_response"] == RESPONSE
    assert detail["resolved_at"].startswith("2026-10-05T12:00:00") and detail["resolved_at"].endswith(("Z", "+00:00"))
    assert not keys_of(listed.json()) & ADMINISTRATION_KEYS
    for value in (RESPONSE, "2026-10-05T12"):
        assert value not in listed.text, value
    assert (created.json()["admin_response"], created.json()["resolved_at"]) == (None, None)


@pytest.mark.parametrize("feedback_id", ["0", "-1", "abc", str(2**64)])
async def test_52_invalid_ids_are_422(client: AsyncClient, laura: dict[str, str], feedback_id: str) -> None:
    response = await client.get(f"{API}/{feedback_id}")
    assert response.status_code == 422
    assert details(response)[0]["field"] == "path.feedback_id"


# --- Access and cache on every route -------------------------------------------------------------------------


async def test_38_39_50_51_access(client: AsyncClient, db_session: AsyncSession, world: dict[str, int]) -> None:
    for path in ("", f"/{world['f1']}"):
        anonymous = await client.get(f"{API}{path}")
        assert (anonymous.status_code, anonymous.json()["error"]["code"]) == (401, "NOT_AUTHENTICATED")
        assert anonymous.headers["cache-control"] == "no-store"
    await as_user(client, db_session, world["admin"])
    for path in ("", f"/{world['f1']}", "/999999", "?status=PENDING"):
        response = await client.get(f"{API}{path}")
        assert (response.status_code, response.json()) == (403, FORBIDDEN), path
        assert response.headers["cache-control"] == "no-store"


async def test_36_49_reads_are_no_store_and_change_nothing(client: AsyncClient, db_session: AsyncSession,
                                                           world: dict[str, int], laura: dict[str, str]) -> None:
    before = await service_tests.snapshot(db_session)
    for response in (await client.get(API), await client.get(f"{API}/{world['f1']}"), await client.get(f"{API}/{world['p1']}")):
        assert response.headers["cache-control"] == "no-store", response.status_code
    assert await service_tests.snapshot(db_session) == before  # rows, versions and audit entries
