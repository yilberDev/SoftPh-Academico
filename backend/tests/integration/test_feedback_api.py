"""HU-005: /api/v1/admin/feedback on the MySQL _test database."""

import smtplib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.exceptions import FeedbackResidentNotValidError
from app.core.sessions import CSRF_HEADER_NAME, derive_csrf_token, generate_session_token
from app.models import AuditAction, AuditLog, Feedback, User, UserRole, UserStatus
from app.services import feedback as feedback_service
from tests.integration.conftest import FakeMailSender, sign_in
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]
URL = "/api/v1/admin/feedback"


@pytest.fixture
async def admin(make_user: MakeUser) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
async def community(db_session: AsyncSession, admin: User) -> Community:
    return Community(db_session, admin.id)


@pytest.fixture
async def headers(client: AsyncClient, db_session: AsyncSession, admin: User) -> dict[str, str]:
    return await sign_in(client, db_session, admin)


async def new_feedback(db: AsyncSession, resident_id: int, subject: str | None = "Ruido en la torre 2",
                       message: str = "Hay ruido después de las 10 p. m.", created_at: datetime | None = None) -> int:
    feedback_id = await feedback_service.create_feedback(db, resident_id, subject=subject, message=message)
    if created_at is not None:
        await db.execute(update(Feedback).where(Feedback.id == feedback_id).values(created_at=created_at))
    return feedback_id


@pytest.fixture
async def laura(community: Community) -> int:
    return await community.resident("Laura Gómez", "1012345678", (1, 2, 104), email="laura@example.com")


@pytest.fixture
async def item(db_session: AsyncSession, laura: int) -> int:
    return await new_feedback(db_session, laura)


async def respond(client: AsyncClient, headers: dict[str, str], feedback_id: int, response: Any = "Revisaremos el caso.",
                  version: Any = 1, **extra: Any) -> Response:
    return await client.post(f"{URL}/{feedback_id}/response", json={"response": response, "version": version, **extra},
                             headers=headers)


async def resolve(client: AsyncClient, headers: dict[str, str], feedback_id: int, note: Any = "Se habló con el vecino.",
                  version: Any = 1, **extra: Any) -> Response:
    return await client.post(f"{URL}/{feedback_id}/resolve", json={"resolution_note": note, "version": version, **extra},
                             headers=headers)


async def audits(db: AsyncSession, action: AuditAction) -> list[AuditLog]:
    return list((await db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))).all())


# --- 1-3. Access ------------------------------------------------------------------------------------------


async def test_administrator_gets_the_list(client: AsyncClient, headers: dict[str, str], item: int) -> None:
    response = await client.get(URL)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["total"] == 1


async def test_resident_is_403(client: AsyncClient, db_session: AsyncSession, laura: int, item: int,
                               mailer: FakeMailSender) -> None:
    resident = await db_session.get(User, laura)
    assert resident is not None
    resident_headers = await sign_in(client, db_session, resident)

    for response in (await client.get(URL), await client.get(f"{URL}/{item}"),
                     await respond(client, resident_headers, item), await resolve(client, resident_headers, item)):
        assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
    stored = await db_session.get(Feedback, item, populate_existing=True)
    assert stored is not None and (stored.admin_response, stored.status, stored.version) == (None, "PENDING", 1)
    assert mailer.sent == []


async def test_without_session_is_401(client: AsyncClient, origin_headers: dict[str, str], item: int) -> None:
    forged = {**origin_headers, CSRF_HEADER_NAME: derive_csrf_token(generate_session_token())}
    for response in (await client.get(URL), await client.get(f"{URL}/{item}"),
                     await respond(client, forged, item), await resolve(client, forged, item)):
        assert response.status_code == 401


@pytest.mark.parametrize("csrf", ["missing", "wrong", "other-session"])
async def test_writes_require_the_csrf_token(client: AsyncClient, headers: dict[str, str], item: int, csrf: str,
                                             db_session: AsyncSession) -> None:
    forged = {"Origin": headers["Origin"]}
    if csrf == "wrong":
        forged[CSRF_HEADER_NAME] = "wrong"
    elif csrf == "other-session":
        forged[CSRF_HEADER_NAME] = derive_csrf_token(generate_session_token())
    for response in (await respond(client, forged, item), await resolve(client, forged, item)):
        assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_FAILED"
    assert (await db_session.get(Feedback, item, populate_existing=True)).version == 1  # type: ignore[union-attr]


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
async def test_writes_require_the_public_origin(client: AsyncClient, headers: dict[str, str], item: int,
                                                origin: str | None) -> None:
    forged = {CSRF_HEADER_NAME: headers[CSRF_HEADER_NAME], **({"Origin": origin} if origin else {})}
    assert (await respond(client, forged, item)).json()["error"]["code"] == "CSRF_FAILED"
    assert (await resolve(client, forged, item)).json()["error"]["code"] == "CSRF_FAILED"


# --- 4-12. List ------------------------------------------------------------------------------------------------


@pytest.fixture
async def several(db_session: AsyncSession, community: Community, client: AsyncClient,
                  headers: dict[str, str]) -> dict[str, int]:
    ana = await community.resident("Ana Pérez", "20000001", (1, 1, 101))
    bruno = await community.resident("Bruno Díaz", "20000002", (1, 1, 102))
    carla = await community.resident("Carla Ruiz", "20000003", (1, 1, 103))
    base = datetime(2026, 9, 1, 15, 0)
    ids = {
        "ana_ruido": await new_feedback(db_session, ana, "Ruido nocturno", created_at=base),
        "bruno_piscina": await new_feedback(db_session, bruno, "Horario de la piscina", created_at=base + timedelta(days=1)),
        "carla_sin": await new_feedback(db_session, carla, None, created_at=base + timedelta(days=2)),
        "ana_parqueadero": await new_feedback(db_session, ana, "Parqueadero de visitantes", created_at=base + timedelta(days=3)),
        "bruno_100": await new_feedback(db_session, bruno, "Descuento 100%_especial", created_at=base + timedelta(days=4)),
    }
    assert (await resolve(client, headers, ids["bruno_piscina"])).status_code == 200
    return ids


async def page(client: AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def subjects(result: dict[str, Any]) -> list[str | None]:
    return [item["subject"] for item in result["items"]]


async def test_list_columns_and_default_order(client: AsyncClient, several: dict[str, int]) -> None:
    result = await page(client)
    assert (result["total"], result["total_pages"], result["page"], result["page_size"]) == (5, 1, 1, 10)
    assert subjects(result) == ["Descuento 100%_especial", "Parqueadero de visitantes", None, "Horario de la piscina", "Ruido nocturno"]
    first = result["items"][-1]
    assert set(first) == {"id", "resident", "subject", "status", "created_at", "resolved_at"}
    assert (first["resident"]["full_name"], first["status"], first["created_at"]) == ("Ana Pérez", "PENDING", "2026-09-01T15:00:00Z")


async def test_pagination(client: AsyncClient, several: dict[str, int]) -> None:
    pages = [await page(client, page=n, page_size=2, sort_order="asc") for n in (1, 2, 3, 4)]
    assert [subjects(p) for p in pages] == [
        ["Ruido nocturno", "Horario de la piscina"], [None, "Parqueadero de visitantes"], ["Descuento 100%_especial"], [],
    ]
    assert all((p["total"], p["total_pages"]) == (5, 3) for p in pages)


async def test_filter_by_status(client: AsyncClient, several: dict[str, int]) -> None:
    assert len((await page(client, status="PENDING"))["items"]) == 4
    resolved = await page(client, status="RESOLVED")
    assert subjects(resolved) == ["Horario de la piscina"] and resolved["items"][0]["resolved_at"] is not None


async def test_search_by_resident_and_subject(client: AsyncClient, several: dict[str, int]) -> None:
    assert subjects(await page(client, search="ana pérez", sort_order="asc")) == ["Ruido nocturno", "Parqueadero de visitantes"]
    assert subjects(await page(client, search="PEREZ")) == ["Parqueadero de visitantes", "Ruido nocturno"]  # accents/case
    assert subjects(await page(client, search="piscina")) == ["Horario de la piscina"]
    assert subjects(await page(client, search="carla")) == [None]  # without subject, found by resident
    assert subjects(await page(client, search="100%_")) == ["Descuento 100%_especial"]
    assert (await page(client, search="%"))["total"] == 1 and (await page(client, search="_"))["total"] == 1
    assert subjects(await page(client, search="ruido", status="PENDING")) == ["Ruido nocturno"]
    assert (await page(client, search="piscina", status="PENDING"))["total"] == 0


@pytest.mark.parametrize(
    ("sort_by", "ascending"),
    [
        ("created_at", ["Ruido nocturno", "Horario de la piscina", None, "Parqueadero de visitantes", "Descuento 100%_especial"]),
        ("resident", ["Ruido nocturno", "Parqueadero de visitantes", "Horario de la piscina", "Descuento 100%_especial", None]),
        ("subject", ["Descuento 100%_especial", "Horario de la piscina", "Parqueadero de visitantes", "Ruido nocturno", None]),
        ("status", ["Ruido nocturno", None, "Parqueadero de visitantes", "Descuento 100%_especial", "Horario de la piscina"]),
    ],
)
async def test_sort_asc_and_desc(client: AsyncClient, several: dict[str, int], sort_by: str, ascending: list[str | None]) -> None:
    assert subjects(await page(client, sort_by=sort_by, sort_order="asc")) == ascending
    descending = subjects(await page(client, sort_by=sort_by, sort_order="desc"))
    if sort_by == "subject":
        assert descending == ascending[:-1][::-1] + [None]  # without subject: always last
    elif sort_by == "created_at":
        assert descending == ascending[::-1]
    else:
        assert sorted(descending, key=str) == sorted(ascending, key=str) and descending != ascending


async def test_no_feedback(client: AsyncClient, headers: dict[str, str]) -> None:
    assert await page(client) == {"items": [], "page": 1, "page_size": 10, "total": 0, "total_pages": 0}


@pytest.mark.parametrize(
    "params",
    [{"sort_by": "message"}, {"sort_by": "resident_id"}, {"sort_by": "created_at; DROP TABLE feedback"},
     {"sort_order": "ascending"}, {"sort_order": "DESC"}, {"status": "OPEN"}, {"status": "pending"},
     {"page": 0}, {"page_size": 0}, {"page_size": 101}, {"search": "x" * 101},
     {"resident_id": 1}, {"role": "ADMINISTRADOR"}, {"unknown": "1"}],
    ids=lambda params: "&".join(f"{k}={str(v)[:16]}" for k, v in params.items()),
)
async def test_invalid_list_query_is_422(client: AsyncClient, headers: dict[str, str], params: dict[str, Any]) -> None:
    response = await client.get(URL, params=params)
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- 13-14, 32. Detail ---------------------------------------------------------------------------------------


async def test_detail(client: AsyncClient, headers: dict[str, str], item: int, laura: int) -> None:
    response = await client.get(f"{URL}/{item}")

    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"id", "resident", "subject", "message", "status", "admin_response", "resolution_note",
                         "created_at", "updated_at", "resolved_at", "version", "action_history"}
    assert body["resident"] == {"id": laura, "full_name": "Laura Gómez", "email": "laura@example.com",
                                "unit_label": "B1-TO2-APTO-104"}
    assert (body["subject"], body["message"], body["status"], body["version"]) == (
        "Ruido en la torre 2", "Hay ruido después de las 10 p. m.", "PENDING", 1)
    assert (body["admin_response"], body["resolution_note"], body["resolved_at"], body["action_history"]) == (None, None, None, [])
    for leak in ("password", "document", "token", "phone"):
        assert leak not in response.text


async def test_detail_without_current_residence(client: AsyncClient, headers: dict[str, str], community: Community,
                                                db_session: AsyncSession) -> None:
    moved = await community.resident("Ya Salió", "30000001", (1, 1, 109), ended=True)
    feedback_id = await new_feedback(db_session, moved)
    assert (await client.get(f"{URL}/{feedback_id}")).json()["resident"]["unit_label"] is None


@pytest.mark.parametrize("feedback_id", ["999999", "0", "-1", "abc", str(2**64)])
async def test_missing_or_invalid_detail(client: AsyncClient, headers: dict[str, str], feedback_id: str) -> None:
    response = await client.get(f"{URL}/{feedback_id}")
    assert response.status_code == (404 if feedback_id == "999999" else 422)
    if feedback_id == "999999":
        assert response.json()["error"] == {"code": "NOT_FOUND", "message": "El feedback no existe.", "details": []}


async def test_actions_on_missing_feedback_are_404(client: AsyncClient, headers: dict[str, str]) -> None:
    assert (await respond(client, headers, 999_999)).status_code == 404
    assert (await resolve(client, headers, 999_999)).status_code == 404


# --- 15-19. Response -----------------------------------------------------------------------------------------


async def test_response(client: AsyncClient, headers: dict[str, str], item: int, admin: User, db_session: AsyncSession,
                        mailer: FakeMailSender) -> None:
    admin_id = admin.id
    response = await respond(client, headers, item, "  Hablaremos con el\nadministrador de la torre.  ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["notification_status"] == "SENT"
    feedback = body["feedback"]
    assert (feedback["admin_response"], feedback["status"], feedback["version"], feedback["resolution_note"]) == (
        "Hablaremos con el\nadministrador de la torre.", "PENDING", 2, None)
    assert [a["action"] for a in feedback["action_history"]] == ["FEEDBACK_RESPONDED", "FEEDBACK_NOTIFICATION_SENT"]
    assert feedback["action_history"][0]["actor"] == {"id": admin_id, "full_name": "Ana María Pérez"}
    # Audited with its context; the text itself is not copied to the audit.
    [entry] = await audits(db_session, AuditAction.FEEDBACK_RESPONDED)
    assert (entry.actor_user_id, entry.entity_type, entry.entity_id) == (admin_id, "feedback", item)
    assert entry.context == {"status": "PENDING", "previous_version": 1, "version": 2, "replaced_response": False}
    assert entry.ip_address is not None and entry.created_at is not None
    # The notification: to the resident, with the response.
    [email] = mailer.sent
    assert email.to == "laura@example.com" and email.subject == "Respuesta a tu feedback — SoftPh-Académico"
    assert "«Ruido en la torre 2»" in email.body and "Hablaremos con el\nadministrador de la torre." in email.body
    [sent] = await audits(db_session, AuditAction.FEEDBACK_NOTIFICATION_SENT)
    assert sent.context == {"notification": "response"}


async def test_response_can_be_replaced_while_pending(client: AsyncClient, headers: dict[str, str], item: int,
                                                      db_session: AsyncSession, mailer: FakeMailSender) -> None:
    await respond(client, headers, item, "Primera respuesta", version=1)
    second = await respond(client, headers, item, "Respuesta corregida", version=2)

    assert second.json()["feedback"]["admin_response"] == "Respuesta corregida" and second.json()["feedback"]["version"] == 3
    assert [e.context["replaced_response"] for e in await audits(db_session, AuditAction.FEEDBACK_RESPONDED)] == [False, True]
    assert len(mailer.sent) == 2


async def test_failed_notification_keeps_the_response(client: AsyncClient, headers: dict[str, str], item: int,
                                                      db_session: AsyncSession, mailer: FakeMailSender,
                                                      caplog: pytest.LogCaptureFixture) -> None:
    mailer.fail_with = smtplib.SMTPServerDisconnected("down")

    response = await respond(client, headers, item, "Respuesta guardada")

    assert response.status_code == 200
    assert response.json()["notification_status"] == "FAILED"
    stored = await db_session.get(Feedback, item, populate_existing=True)
    assert stored is not None and (stored.admin_response, stored.version) == ("Respuesta guardada", 2)  # no rollback
    [failed] = await audits(db_session, AuditAction.FEEDBACK_NOTIFICATION_FAILED)
    assert failed.context == {"notification": "response", "error": "SMTPServerDisconnected"}
    assert [a["action"] for a in response.json()["feedback"]["action_history"]] == [
        "FEEDBACK_RESPONDED", "FEEDBACK_NOTIFICATION_FAILED"]
    assert "Feedback notification not sent" in caplog.text and "Respuesta guardada" not in caplog.text


@pytest.mark.parametrize(
    ("body", "field"),
    [({"response": ""}, "response"), ({"response": "   \n "}, "response"), ({"response": None}, "response"),
     ({"response": "x" * 2001}, "response"), ({"response": 5}, "response"), ({"version": 0}, "version"),
     ({"version": "2"}, "version"), ({"version": True}, "version"), ({"version": None}, "version"),
     ({"status": "RESOLVED"}, "status"), ({"message": "otro"}, "message"), ({"resident_id": 2}, "resident_id")],
)
async def test_invalid_response(client: AsyncClient, headers: dict[str, str], item: int, body: dict[str, Any], field: str,
                                db_session: AsyncSession) -> None:
    payload = {"response": "Una respuesta", "version": 1, **body}
    response = await client.post(f"{URL}/{item}/response", json=payload, headers=headers)
    assert response.status_code == 422
    assert [d["field"] for d in response.json()["error"]["details"]] == [field]
    assert (await db_session.get(Feedback, item, populate_existing=True)).version == 1  # type: ignore[union-attr]


# --- 20-26. Resolution -----------------------------------------------------------------------------------------


async def test_resolution(client: AsyncClient, headers: dict[str, str], item: int, admin: User, db_session: AsyncSession,
                          mailer: FakeMailSender) -> None:
    admin_id = admin.id
    await respond(client, headers, item, "Revisaremos el ruido.")
    before = utc_now()
    response = await resolve(client, headers, item, "Se citó al vecino del 204 y se acordó horario.", version=2)
    after = utc_now()

    assert response.status_code == 200, response.text
    feedback = response.json()["feedback"]
    assert (feedback["status"], feedback["version"], feedback["resolution_note"], feedback["admin_response"]) == (
        "RESOLVED", 3, "Se citó al vecino del 204 y se acordó horario.", "Revisaremos el ruido.")
    stored = await db_session.get(Feedback, item, populate_existing=True)
    assert stored is not None and stored.resolved_at is not None and before <= stored.resolved_at <= after
    [entry] = await audits(db_session, AuditAction.FEEDBACK_RESOLVED)
    assert (entry.actor_user_id, entry.entity_id) == (admin_id, item)
    assert entry.context == {"previous_status": "PENDING", "status": "RESOLVED", "previous_version": 2, "version": 3,
                             "with_response": True}
    # The resolution email includes the response and never the internal note.
    resolution_email = mailer.sent[-1]
    assert resolution_email.subject == "Tu feedback fue resuelto — SoftPh-Académico"
    assert "Respuesta de la administración:\nRevisaremos el ruido." in resolution_email.body
    assert "vecino del 204" not in resolution_email.body
    assert [a["action"] for a in feedback["action_history"]] == [
        "FEEDBACK_RESPONDED", "FEEDBACK_NOTIFICATION_SENT", "FEEDBACK_RESOLVED", "FEEDBACK_NOTIFICATION_SENT"]


async def test_resolution_without_response(client: AsyncClient, headers: dict[str, str], item: int,
                                           mailer: FakeMailSender) -> None:
    feedback = (await resolve(client, headers, item, "Nota interna: caso cerrado por teléfono.")).json()["feedback"]

    assert (feedback["admin_response"], feedback["resolution_note"]) == (None, "Nota interna: caso cerrado por teléfono.")
    [email] = mailer.sent
    assert "realizó la gestión correspondiente y lo marcó como resuelto" in email.body
    assert "Respuesta" not in email.body and "caso cerrado por teléfono" not in email.body


@pytest.mark.parametrize("note", ["", "   ", None, "x" * 2001])
async def test_resolution_note_is_required(client: AsyncClient, headers: dict[str, str], item: int, note: Any,
                                           db_session: AsyncSession, mailer: FakeMailSender) -> None:
    response = await resolve(client, headers, item, note)
    assert response.status_code == 422
    assert [d["field"] for d in response.json()["error"]["details"]] == ["resolution_note"]
    stored = await db_session.get(Feedback, item, populate_existing=True)
    assert stored is not None and stored.status == "PENDING" and mailer.sent == []


async def test_resolving_twice_is_409(client: AsyncClient, headers: dict[str, str], item: int, db_session: AsyncSession,
                                      mailer: FakeMailSender) -> None:
    assert (await resolve(client, headers, item, version=1)).status_code == 200
    resolved_at = (await db_session.get(Feedback, item, populate_existing=True)).resolved_at  # type: ignore[union-attr]

    again = await resolve(client, headers, item, "Otra nota", version=2)

    assert again.status_code == 409
    assert again.json()["error"] == {"code": "FEEDBACK_ALREADY_RESOLVED",
                                     "message": "Este feedback ya fue marcado como resuelto.", "details": []}
    stored = await db_session.get(Feedback, item, populate_existing=True)
    assert stored is not None and (stored.resolution_note, stored.version, stored.resolved_at) == (
        "Se habló con el vecino.", 2, resolved_at)
    assert len(await audits(db_session, AuditAction.FEEDBACK_RESOLVED)) == 1 and len(mailer.sent) == 1


async def test_no_response_after_resolution_and_no_reopening(client: AsyncClient, headers: dict[str, str], item: int,
                                                             db_session: AsyncSession) -> None:
    await resolve(client, headers, item, version=1)

    late = await respond(client, headers, item, "Tarde", version=2)

    assert late.status_code == 409 and late.json()["error"]["code"] == "FEEDBACK_ALREADY_RESOLVED"
    stored = await db_session.get(Feedback, item, populate_existing=True)
    assert stored is not None and (stored.status, stored.admin_response) == ("RESOLVED", None)
    # No route exists to reopen or edit freely.
    for method, path in (("POST", f"{URL}/{item}/reopen"), ("PUT", f"{URL}/{item}"), ("PATCH", f"{URL}/{item}"),
                         ("DELETE", f"{URL}/{item}")):
        assert (await client.request(method, path, json={}, headers=headers)).status_code in (404, 405)


@pytest.mark.parametrize("action", ["response", "resolve"])
async def test_stale_version_is_409(client: AsyncClient, headers: dict[str, str], item: int, action: str,
                                    db_session: AsyncSession, mailer: FakeMailSender) -> None:
    await respond(client, headers, item, "Primera", version=1)  # now version 2

    stale = await (respond(client, headers, item, "Otra", version=1) if action == "response"
                   else resolve(client, headers, item, "Nota", version=1))

    assert stale.status_code == 409
    assert stale.json()["error"] == {
        "code": "VERSION_CONFLICT",
        "message": "El feedback fue actualizado en otra sesión. Vuelve a cargar la información antes de realizar cambios.",
        "details": []}
    stored = await db_session.get(Feedback, item, populate_existing=True)
    assert stored is not None and (stored.admin_response, stored.status, stored.version) == ("Primera", "PENDING", 2)
    assert len(mailer.sent) == 1


# --- 29-31. Integrity --------------------------------------------------------------------------------------------


async def test_original_message_is_immutable(client: AsyncClient, headers: dict[str, str], item: int,
                                             db_session: AsyncSession) -> None:
    await respond(client, headers, item, "Respuesta", version=1, message="mensaje cambiado")  # rejected (extra field)
    await respond(client, headers, item, "Respuesta", version=1)
    await resolve(client, headers, item, "Nota", version=2, subject="otro asunto")  # rejected
    await resolve(client, headers, item, "Nota", version=2)

    stored = await db_session.get(Feedback, item, populate_existing=True)
    assert stored is not None
    assert (stored.subject, stored.message, stored.status) == ("Ruido en la torre 2", "Hay ruido después de las 10 p. m.", "RESOLVED")


@pytest.mark.parametrize("case", ["administrator", "deleted", "pending-activation", "disabled", "missing"])
async def test_only_an_activated_resident_can_own_feedback(db_session: AsyncSession, community: Community, admin: User,
                                                           case: str) -> None:
    owner = {
        "administrator": lambda: admin.id,
        "missing": lambda: 999_999,
    }
    if case in owner:
        user_id = owner[case]()
    else:
        status = {"deleted": UserStatus.ACTIVE, "pending-activation": UserStatus.PENDING_ACTIVATION,
                  "disabled": UserStatus.DISABLED}[case]
        user_id = await community.resident("Otra Persona", None, None, status=status, deleted=case == "deleted")

    with pytest.raises(FeedbackResidentNotValidError):
        await feedback_service.create_feedback(db_session, user_id, subject=None, message="Hola")
    assert await db_session.scalar(select(func.count()).select_from(Feedback)) == 0


async def test_feedback_belongs_to_its_resident(client: AsyncClient, headers: dict[str, str], db_session: AsyncSession,
                                                community: Community) -> None:
    ana = await community.resident("Ana Pérez", "40000001", (1, 1, 101))
    bruno = await community.resident("Bruno Díaz", "40000002", (1, 1, 102))
    mine = await new_feedback(db_session, ana, "De Ana")

    detail = (await client.get(f"{URL}/{mine}")).json()
    await respond(client, headers, mine, "R", version=1, resident_id=bruno)  # cannot be moved: 422

    assert detail["resident"]["id"] == ana and detail["resident"]["unit_label"] == "B1-TO1-APTO-101"
    assert await db_session.scalar(select(Feedback.resident_id).where(Feedback.id == mine)) == ana


async def test_history_comes_from_audit_logs(client: AsyncClient, headers: dict[str, str], item: int,
                                             db_session: AsyncSession, make_user: MakeUser) -> None:
    await respond(client, headers, item, "Respuesta", version=1)
    # Unrelated audit rows (another entity, the same id) never leak into the history.
    await db_session.execute(text(
        "INSERT INTO audit_logs (action, entity_type, entity_id, context) VALUES ('UNIT_UPDATED', 'unit', :id, NULL)"),
        {"id": item})

    history = (await client.get(f"{URL}/{item}")).json()["action_history"]

    rows = (await db_session.execute(text(
        "SELECT action FROM audit_logs WHERE entity_type = 'feedback' AND entity_id = :id ORDER BY id"), {"id": item})).all()
    assert [h["action"] for h in history] == [row[0] for row in rows] == ["FEEDBACK_RESPONDED", "FEEDBACK_NOTIFICATION_SENT"]
    assert all(set(h) == {"action", "actor", "at"} and h["at"].endswith("Z") for h in history)
    assert "context" not in str(history) and "ip_address" not in str(history)


async def test_reading_is_not_audited(client: AsyncClient, headers: dict[str, str], item: int, db_session: AsyncSession) -> None:
    await client.get(URL)
    await client.get(f"{URL}/{item}")
    assert await db_session.scalar(text("SELECT COUNT(*) FROM audit_logs WHERE entity_type = 'feedback'")) == 0
