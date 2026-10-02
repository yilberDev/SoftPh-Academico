"""HU-003: registro de un residente mediante invitación (flujo del administrador).

Academic criteria: CA-1 form (frontend), CA-2 available unit, CA-3 data,
CA-4 duplicates, CA-5 invitation, CA-6 email, CA-7 confirmation, CA-8 errors.
HU-019 (activation) is not implemented: where a test needs an activated
resident, the database state is simulated with SQL.
"""

import hashlib
import re
import smtplib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import AuditAction, AuditLog
from app.services import invitations as invitation_service
from tests.integration.conftest import FakeMailSender

pytestmark = pytest.mark.anyio

API = "/api/v1"
TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]+)")


class Residents:
    """API helper with the administrator's headers."""

    def __init__(self, client: AsyncClient, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = headers
        self._blocks: dict[int, int] = {}
        self._towers: dict[tuple[int, int], int] = {}

    async def unit(self, block: int = 1, tower: int = 2, number: int = 104) -> int:
        if block not in self._blocks:
            response = await self.client.post(f"{API}/blocks", json={"number": block}, headers=self.headers)
            self._blocks[block] = response.json()["id"]
        if (block, tower) not in self._towers:
            response = await self.client.post(
                f"{API}/blocks/{self._blocks[block]}/towers", json={"number": tower}, headers=self.headers
            )
            self._towers[(block, tower)] = response.json()["id"]
        response = await self.client.post(
            f"{API}/towers/{self._towers[(block, tower)]}/units", json={"number": number}, headers=self.headers
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    async def invite(
        self,
        unit_id: Any,
        email: Any = "residente@example.com",
        full_name: Any = "Laura Gómez",
        validity_days: Any = 7,
        **extra: Any,
    ) -> Response:
        body = {"email": email, "full_name": full_name, "unit_id": unit_id, "validity_days": validity_days, **extra}
        return await self.client.post(f"{API}/resident-invitations", json=body, headers=self.headers)

    async def invited(self, unit_id: int, email: str = "residente@example.com", **kwargs: Any) -> dict[str, Any]:
        response = await self.invite(unit_id, email, **kwargs)
        assert response.status_code == 201, response.text
        return response.json()

    async def resend(self, invitation_id: int) -> Response:
        return await self.client.post(f"{API}/resident-invitations/{invitation_id}/resend", headers=self.headers)

    async def available(self) -> list[str]:
        response = await self.client.get(f"{API}/units/available")
        assert response.status_code == 200
        return [item["label"] for item in response.json()["items"]]

    async def pending(self) -> list[dict[str, Any]]:
        response = await self.client.get(f"{API}/resident-invitations", params={"status": "PENDING"})
        assert response.status_code == 200
        return response.json()["items"]


@pytest.fixture
def api(client: AsyncClient, admin_headers: dict[str, str]) -> Residents:
    return Residents(client, admin_headers)


def token_of(mailer: FakeMailSender, index: int = -1) -> str:
    match = TOKEN_RE.search(mailer.sent[index].body)
    assert match is not None
    return match.group(1)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def row(db: AsyncSession, sql: str, **params: Any) -> Any:
    return (await db.execute(text(sql), params)).mappings().one()


async def rows(db: AsyncSession, sql: str, **params: Any) -> list[Any]:
    return list((await db.execute(text(sql), params)).mappings().all())


async def expire(db: AsyncSession, invitation_id: int) -> None:
    await db.execute(
        text(
            "UPDATE resident_invitations SET created_at = UTC_TIMESTAMP(6) - INTERVAL 8 DAY, "
            "expires_at = UTC_TIMESTAMP(6) - INTERVAL 1 DAY WHERE id = :id"
        ),
        {"id": invitation_id},
    )


async def audit(db: AsyncSession, *actions: AuditAction) -> list[AuditLog]:
    query = select(AuditLog).order_by(AuditLog.id)
    if actions:
        query = query.where(AuditLog.action.in_(actions))
    return list((await db.scalars(query)).all())


def error(response: Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code
    return body


# --- CA-5 / CA-6 / CA-7: successful invitation ---------------------------------------------


async def test_invitation_is_created_and_sent(api: Residents, mailer: FakeMailSender) -> None:
    unit_id = await api.unit(1, 2, 104)

    response = await api.invite(unit_id, "residente@example.com", "Laura Gómez", 7)

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body | {"id": None, "created_at": None, "expires_at": None} == {
        "id": None,
        "email": "residente@example.com",
        "full_name": "Laura Gómez",
        "unit": {"id": unit_id, "label": "B1-TO2-APTO-104"},
        "status": "PENDING",
        "delivery_status": "SENT",
        "send_attempts": 1,
        "validity_days": 7,
        "created_at": None,
        "expires_at": None,
    }
    created = datetime.fromisoformat(body["created_at"])
    expires = datetime.fromisoformat(body["expires_at"])
    assert created.tzinfo is not None and body["expires_at"].endswith("Z")
    assert expires - created == timedelta(days=7)
    assert len(mailer.sent) == 1


async def test_resident_user_is_pending_without_password(api: Residents, db_session: AsyncSession) -> None:
    await api.invited(await api.unit())

    user = await row(db_session, "SELECT * FROM users WHERE email = 'residente@example.com'")
    assert (user["role"], user["status"], user["password_hash"]) == ("RESIDENTE", "PENDING_ACTIVATION", None)
    assert user["full_name"] == "Laura Gómez"


async def test_invitation_row_stores_only_the_token_hash(
    api: Residents, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    unit_id = await api.unit()
    body = await api.invited(unit_id)
    token = token_of(mailer)

    invitation = await row(db_session, "SELECT * FROM resident_invitations WHERE id = :id", id=body["id"])
    admin_id = (await row(db_session, "SELECT id FROM users WHERE email = 'admin@example.com'"))["id"]
    user_id = (await row(db_session, "SELECT id FROM users WHERE email = 'residente@example.com'"))["id"]
    assert invitation["token_hash"] == sha256(token)
    assert token not in str(dict(invitation))
    assert (invitation["user_id"], invitation["unit_id"], invitation["invited_by_user_id"]) == (user_id, unit_id, admin_id)
    assert (invitation["status"], invitation["delivery_status"], invitation["send_attempts"]) == ("PENDING", "SENT", 1)
    assert invitation["last_sent_at"] is not None and invitation["used_at"] is None
    assert len(token) == 43  # 256 random bits, URL-safe base64


async def test_email_content(api: Residents, mailer: FakeMailSender) -> None:
    body = await api.invited(await api.unit(1, 2, 104), "residente@example.com", full_name="Laura Gómez")

    [email] = mailer.sent
    assert email.to == "residente@example.com"
    assert email.subject == "Invitación para activar tu cuenta en SoftPh-Académico"
    assert "Hola, Laura Gómez:" in email.body
    assert "B1-TO2-APTO-104" in email.body
    assert f"{get_settings().app_origin}/activate-account#token={token_of(mailer)}" in email.body
    assert "?token=" not in email.body
    assert "(hora de Colombia)" in email.body
    expires_bogota = datetime.fromisoformat(body["expires_at"]).astimezone(UTC) - timedelta(hours=5)
    assert f"{expires_bogota.day} de " in email.body and str(expires_bogota.year) in email.body
    assert "contraseña:" not in email.body.lower()
    assert "Si no esperabas esta invitación, puedes ignorar este correo." in email.body


async def test_invitation_is_audited(api: Residents, mailer: FakeMailSender, db_session: AsyncSession) -> None:
    unit_id = await api.unit()
    body = await api.invited(unit_id)

    invited, sent = await audit(db_session, AuditAction.RESIDENT_INVITED, AuditAction.INVITATION_EMAIL_SENT)
    user_id = (await row(db_session, "SELECT id FROM users WHERE email = 'residente@example.com'"))["id"]
    assert (invited.entity_type, invited.entity_id) == ("user", user_id)
    assert invited.context == {"invitation_id": body["id"], "unit_id": unit_id, "validity_days": 7, "reinvitation": False}
    assert (sent.entity_type, sent.entity_id, sent.context) == (
        "resident_invitation", body["id"], {"invitation_id": body["id"], "attempt": 1}
    )
    assert invited.ip_address == "127.0.0.1" and invited.actor_user_id is not None
    assert token_of(mailer) not in str([e.context for e in await audit(db_session)])


async def test_email_is_normalized(api: Residents, db_session: AsyncSession) -> None:
    body = await api.invited(await api.unit(), "  Residente@Example.COM ")
    assert body["email"] == "residente@example.com"


# --- CA-2: available apartments -----------------------------------------------------------


async def test_available_units_exclude_reserved_and_deleted(api: Residents) -> None:
    reserved = await api.unit(1, 2, 104)
    await api.unit(1, 2, 105)
    deleted = await api.unit(1, 2, 106)
    await api.unit(2, 1, 1)
    await api.client.delete(f"{API}/units/{deleted}", params={"version": 1}, headers=api.headers)

    assert await api.available() == ["B1-TO2-APTO-104", "B1-TO2-APTO-105", "B2-TO1-APTO-1"]
    await api.invited(reserved)
    assert await api.available() == ["B1-TO2-APTO-105", "B2-TO1-APTO-1"]


async def test_available_units_shape(api: Residents) -> None:
    unit_id = await api.unit(1, 2, 104)
    response = await api.client.get(f"{API}/units/available")
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "items": [{"unit_id": unit_id, "label": "B1-TO2-APTO-104", "block_number": 1, "tower_number": 2, "number": 104}]
    }


async def test_reserved_unit_cannot_be_invited_again(api: Residents, db_session: AsyncSession) -> None:
    unit_id = await api.unit()
    await api.invited(unit_id, "uno@example.com")

    body = error(await api.invite(unit_id, "dos@example.com"), 409, "UNIT_NOT_AVAILABLE")
    assert "no está disponible" in body["message"]
    assert (await row(db_session, "SELECT COUNT(*) AS n FROM users WHERE email = 'dos@example.com'"))["n"] == 0


async def test_deleted_or_unknown_unit_is_rejected(api: Residents) -> None:
    unit_id = await api.unit()
    await api.client.delete(f"{API}/units/{unit_id}", params={"version": 1}, headers=api.headers)

    error(await api.invite(unit_id), 409, "UNIT_NOT_AVAILABLE")
    error(await api.invite(999_999_999), 409, "UNIT_NOT_AVAILABLE")


async def test_unit_with_active_residence_is_not_available(api: Residents, db_session: AsyncSession) -> None:
    # Simulates the state HU-019 will produce (activation is not implemented).
    unit_id = await api.unit()
    body = await api.invited(unit_id, "activo@example.com")
    await db_session.execute(
        text("UPDATE resident_invitations SET status = 'USED', used_at = UTC_TIMESTAMP(6) WHERE id = :id"),
        {"id": body["id"]},
    )
    await db_session.execute(
        text("UPDATE users SET status = 'ACTIVE', password_hash = 'not-a-real-hash' WHERE email = 'activo@example.com'")
    )
    await db_session.execute(
        text(
            "INSERT INTO resident_units (user_id, unit_id, invitation_id, started_at) "
            "SELECT user_id, unit_id, id, UTC_TIMESTAMP(6) FROM resident_invitations WHERE id = :id"
        ),
        {"id": body["id"]},
    )

    assert "B1-TO2-APTO-104" not in await api.available()
    error(await api.invite(unit_id, "otro@example.com"), 409, "UNIT_NOT_AVAILABLE")


async def test_expired_invitation_releases_the_unit(api: Residents, db_session: AsyncSession) -> None:
    unit_id = await api.unit()
    first = await api.invited(unit_id, "uno@example.com")
    await expire(db_session, first["id"])

    assert "B1-TO2-APTO-104" in await api.available()
    # Reading the list does not write: the old invitation is still PENDING.
    assert (await row(db_session, "SELECT status FROM resident_invitations WHERE id = :id", id=first["id"]))["status"] == "PENDING"

    second = await api.invited(unit_id, "dos@example.com")

    statuses = await rows(db_session, "SELECT id, status FROM resident_invitations ORDER BY id")
    assert [(r["id"], r["status"]) for r in statuses] == [(first["id"], "EXPIRED"), (second["id"], "PENDING")]
    [invited] = (await audit(db_session, AuditAction.RESIDENT_INVITED))[1:]
    assert invited.context["expired_invitation_ids"] == [first["id"]]


# --- CA-4: duplicates -------------------------------------------------------------------------


@pytest.mark.parametrize("email", ["admin@example.com", "  ADMIN@Example.com "])
async def test_administrator_email_is_rejected(api: Residents, db_session: AsyncSession, email: str) -> None:
    body = error(await api.invite(await api.unit(), email), 409, "EMAIL_ALREADY_REGISTERED")
    assert body["message"] == "El correo electrónico ya está registrado."
    assert (await row(db_session, "SELECT COUNT(*) AS n FROM resident_invitations"))["n"] == 0


@pytest.mark.parametrize(
    ("status", "deleted"),
    [("ACTIVE", False), ("DISABLED", False), ("PENDING_ACTIVATION", True)],
    ids=["active-resident", "disabled-resident", "deleted-resident"],
)
async def test_existing_account_is_rejected(
    api: Residents, db_session: AsyncSession, status: str, deleted: bool
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO users (email, full_name, role, status, password_hash, deleted_at) "
            "VALUES ('residente@example.com', 'Laura Gómez', 'RESIDENTE', :status, 'not-a-real-hash', :deleted)"
        ),
        {"status": status, "deleted": "2026-01-01 00:00:00" if deleted else None},
    )
    error(await api.invite(await api.unit()), 409, "EMAIL_ALREADY_REGISTERED")


async def test_email_with_valid_pending_invitation_is_rejected(api: Residents, db_session: AsyncSession) -> None:
    await api.invited(await api.unit(1, 2, 104))
    other = await api.unit(1, 2, 105)

    body = error(await api.invite(other), 409, "INVITATION_ALREADY_PENDING")
    assert "invitación vigente" in body["message"]
    assert "B1-TO2-APTO-105" in await api.available()
    assert (await row(db_session, "SELECT COUNT(*) AS n FROM users WHERE role = 'RESIDENTE'"))["n"] == 1


async def test_reinvitation_after_expiry_reuses_the_user(
    api: Residents, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    first_unit = await api.unit(1, 2, 104)
    first = await api.invited(first_unit, full_name="Laura Gomez")
    await expire(db_session, first["id"])
    other_unit = await api.unit(1, 2, 105)

    second = await api.invited(other_unit, full_name="Laura Gómez Ruiz", validity_days=3)

    users = await rows(db_session, "SELECT id, full_name, status FROM users WHERE email = 'residente@example.com'")
    assert len(users) == 1 and users[0]["full_name"] == "Laura Gómez Ruiz"
    assert users[0]["status"] == "PENDING_ACTIVATION"
    invitations = await rows(db_session, "SELECT id, user_id, unit_id, status, validity_days FROM resident_invitations ORDER BY id")
    assert [(i["user_id"], i["unit_id"], i["status"]) for i in invitations] == [
        (users[0]["id"], first_unit, "EXPIRED"),
        (users[0]["id"], other_unit, "PENDING"),
    ]
    assert second["id"] != first["id"] and second["validity_days"] == 3
    assert sha256(token_of(mailer, 0)) != sha256(token_of(mailer, 1))
    reinvited = (await audit(db_session, AuditAction.RESIDENT_INVITED))[-1]
    assert reinvited.context == {
        "invitation_id": second["id"], "unit_id": other_unit, "validity_days": 3, "reinvitation": True,
        "expired_invitation_ids": [first["id"]], "full_name_updated": True,
    }


async def test_reinvitation_to_the_same_unit(api: Residents, db_session: AsyncSession) -> None:
    unit_id = await api.unit()
    first = await api.invited(unit_id)
    await expire(db_session, first["id"])

    second = await api.invited(unit_id)

    assert second["unit"]["id"] == unit_id and second["id"] != first["id"]


# --- CA-3 / CA-8: validation -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "field", "message"),
    [
        ({"email": ""}, "email", "El correo electrónico es obligatorio."),
        ({"email": "no-es-correo"}, "email", "Ingresa un correo electrónico válido."),
        ({"email": "josé@example.com"}, "email", "El correo electrónico no puede contener tildes, eñes ni otros caracteres especiales."),
        ({"full_name": " "}, "full_name", "El nombre completo es obligatorio."),
        ({"full_name": "Ana 2"}, "full_name", "El nombre completo solo puede contener letras, espacios, apóstrofos y guiones."),
        ({"validity_days": 0}, "validity_days", "La vigencia debe estar entre 1 y 30 días."),
        ({"validity_days": 31}, "validity_days", "La vigencia debe estar entre 1 y 30 días."),
        ({"validity_days": "7"}, "validity_days", "Debe ser un número entero."),
        ({"validity_days": 7.5}, "validity_days", "Debe ser un número entero."),
        ({"unit_id": "15"}, "unit_id", "Debe ser un número entero."),
        ({"unit_id": 0}, "unit_id", "Selecciona un apartamento válido."),
        ({"unit_id": True}, "unit_id", "Debe ser un número entero."),
        ({"token": "x"}, "token", "Este campo no está permitido."),
    ],
)
async def test_invalid_payload(
    api: Residents, db_session: AsyncSession, mailer: FakeMailSender, payload: dict[str, Any], field: str, message: str
) -> None:
    unit_id = await api.unit()
    body = {"email": "residente@example.com", "full_name": "Laura Gómez", "unit_id": unit_id, "validity_days": 7, **payload}

    response = await api.client.post(f"{API}/resident-invitations", json=body, headers=api.headers)

    assert error(response, 422, "VALIDATION_ERROR")["details"] == [{"field": field, "message": message}]
    assert (await row(db_session, "SELECT COUNT(*) AS n FROM resident_invitations"))["n"] == 0
    assert mailer.sent == []


async def test_missing_fields(api: Residents) -> None:
    response = await api.client.post(f"{API}/resident-invitations", json={}, headers=api.headers)
    details = error(response, 422, "VALIDATION_ERROR")["details"]
    assert {d["field"] for d in details} == {"email", "full_name", "unit_id", "validity_days"}
    assert {d["message"] for d in details} == {"Este campo es obligatorio."}


@pytest.mark.parametrize("days", [1, 30])
async def test_validity_limits(api: Residents, days: int) -> None:
    body = await api.invited(await api.unit(), validity_days=days)
    delta = datetime.fromisoformat(body["expires_at"]) - datetime.fromisoformat(body["created_at"])
    assert delta == timedelta(days=days)


# --- E-1: email failure, resend and recovery -----------------------------------------------------


async def test_smtp_failure_keeps_a_valid_invitation(
    api: Residents, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    mailer.fail_with = smtplib.SMTPServerDisconnected("connection lost")
    unit_id = await api.unit()

    response = await api.invite(unit_id)

    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["delivery_status"], body["send_attempts"]) == ("PENDING", "FAILED", 1)
    assert "B1-TO2-APTO-104" not in await api.available()  # still reserved
    [failed] = await audit(db_session, AuditAction.INVITATION_EMAIL_FAILED)
    assert failed.context == {"invitation_id": body["id"], "attempt": 1, "error": "SMTPServerDisconnected"}
    assert [i["id"] for i in await api.pending()] == [body["id"]]

    mailer.fail_with = None
    resent = await api.resend(body["id"])
    assert resent.status_code == 200
    assert (resent.json()["delivery_status"], resent.json()["send_attempts"]) == ("SENT", 2)


async def test_unrecorded_delivery_result_leaves_delivery_pending(
    api: Residents, mailer: FakeMailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulates a crash between SMTP and T2: the invitation must stay valid.
    async def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(invitation_service, "_record_delivery", fail)

    body = await api.invited(await api.unit())

    assert (body["status"], body["delivery_status"], body["send_attempts"]) == ("PENDING", "PENDING", 0)
    monkeypatch.undo()
    assert (await api.resend(body["id"])).json()["delivery_status"] == "SENT"


async def test_resend_rotates_the_token_and_keeps_the_rest(
    api: Residents, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    unit_id = await api.unit()
    created = await api.invited(unit_id)
    before = await row(db_session, "SELECT * FROM resident_invitations WHERE id = :id", id=created["id"])
    old_token = token_of(mailer)

    response = await api.resend(created["id"])

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    after = await row(db_session, "SELECT * FROM resident_invitations WHERE id = :id", id=created["id"])
    new_token = token_of(mailer)
    assert new_token != old_token
    assert after["token_hash"] == sha256(new_token)
    # The previous link no longer matches any invitation.
    old = await row(db_session, "SELECT COUNT(*) AS n FROM resident_invitations WHERE token_hash = :h", h=sha256(old_token))
    assert old["n"] == 0
    # Same invitation, user, unit and expiry (E-1c: resending does not extend it).
    for column in ("id", "user_id", "unit_id", "expires_at", "created_at", "validity_days", "status"):
        assert after[column] == before[column], column
    assert (after["send_attempts"], after["delivery_status"]) == (2, "SENT")
    assert response.json()["expires_at"] == created["expires_at"]
    assert [e.to for e in mailer.sent] == ["residente@example.com", "residente@example.com"]
    [resent] = await audit(db_session, AuditAction.INVITATION_RESENT)
    assert resent.context == {"invitation_id": created["id"], "unit_id": unit_id}


async def test_resend_of_an_expired_invitation_is_rejected(api: Residents, db_session: AsyncSession, mailer: FakeMailSender) -> None:
    created = await api.invited(await api.unit())
    await expire(db_session, created["id"])

    body = error(await api.resend(created["id"]), 409, "INVITATION_NOT_PENDING")
    assert "venció" in body["message"]
    status = (await row(db_session, "SELECT status FROM resident_invitations WHERE id = :id", id=created["id"]))["status"]
    assert status == "EXPIRED"  # detected and recorded
    assert len(mailer.sent) == 1
    error(await api.resend(created["id"]), 409, "INVITATION_NOT_PENDING")


async def test_resend_of_a_used_invitation_is_rejected(api: Residents, db_session: AsyncSession) -> None:
    created = await api.invited(await api.unit())
    await db_session.execute(
        text("UPDATE resident_invitations SET status = 'USED', used_at = UTC_TIMESTAMP(6) WHERE id = :id"),
        {"id": created["id"]},
    )
    body = error(await api.resend(created["id"]), 409, "INVITATION_NOT_PENDING")
    assert "utilizada" in body["message"]


async def test_resend_unknown_invitation(api: Residents) -> None:
    error(await api.resend(999_999_999), 404, "NOT_FOUND")


async def test_pending_list(api: Residents, db_session: AsyncSession) -> None:
    valid = await api.invited(await api.unit(1, 2, 104), "uno@example.com")
    expired = await api.invited(await api.unit(1, 2, 105), "dos@example.com")
    used = await api.invited(await api.unit(1, 2, 106), "tres@example.com")
    await expire(db_session, expired["id"])
    await db_session.execute(
        text("UPDATE resident_invitations SET status = 'USED', used_at = UTC_TIMESTAMP(6) WHERE id = :id"),
        {"id": used["id"]},
    )

    items = await api.pending()

    assert items == [valid]
    assert set(items[0]) == {
        "id", "email", "full_name", "unit", "status", "delivery_status", "send_attempts",
        "validity_days", "created_at", "expires_at",
    }


async def test_pending_list_requires_the_status_filter(api: Residents) -> None:
    error(await api.client.get(f"{API}/resident-invitations"), 422, "VALIDATION_ERROR")
    error(await api.client.get(f"{API}/resident-invitations", params={"status": "USED"}), 422, "VALIDATION_ERROR")


# --- HU-013 integration: an apartment in use cannot be deleted -----------------------------------


async def test_unit_with_pending_invitation_cannot_be_deleted(api: Residents, db_session: AsyncSession) -> None:
    unit_id = await api.unit()
    created = await api.invited(unit_id)

    response = await api.client.delete(f"{API}/units/{unit_id}", params={"version": 1}, headers=api.headers)

    body = error(response, 409, "UNIT_IN_USE")
    assert body["message"] == "No se puede eliminar B1-TO2-APTO-104 porque tiene un residente o una invitación pendiente."
    await expire(db_session, created["id"])
    response = await api.client.delete(f"{API}/units/{unit_id}", params={"version": 1}, headers=api.headers)
    assert response.status_code == 204
