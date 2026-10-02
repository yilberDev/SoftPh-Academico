"""HU-019: activación de la cuenta del residente (verify + activate).

The invitation is created with the real HU-003 flow (fake mailer); the
resident then uses the token from the email, anonymously.
"""

import hashlib
import re
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import verify_password
from app.models import AuditAction, AuditLog
from tests.integration.conftest import FakeMailSender

pytestmark = pytest.mark.anyio

API = "/api/v1"
TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]+)")
PASSWORD = "una clave segura 2026"
EMAIL = "residente@example.com"


class Flow:
    """Admin creates the invitation (HU-003); the resident activates it anonymously (HU-019)."""

    def __init__(self, client: AsyncClient, admin: dict[str, str], mailer: FakeMailSender) -> None:
        self.client = client
        self.admin = admin
        self.mailer = mailer
        self.origin = {"Origin": admin["Origin"]}
        self.unit_id = 0
        self.invitation_id = 0
        self.token = ""
        self.admin_cookie = client.cookies.get("softph_session")

    async def unit(self, number: int) -> int:
        blocks = (await self.as_admin().get(f"{API}/blocks")).json()["items"]
        block = blocks[0] if blocks else (await self.client.post(f"{API}/blocks", json={"number": 4}, headers=self.admin)).json()
        towers = (await self.client.get(f"{API}/blocks/{block['id']}/towers")).json()["items"]
        tower = towers[0] if towers else (
            await self.client.post(f"{API}/blocks/{block['id']}/towers", json={"number": 2}, headers=self.admin)
        ).json()
        response = await self.client.post(f"{API}/towers/{tower['id']}/units", json={"number": number}, headers=self.admin)
        return response.json()["id"]

    def as_admin(self) -> AsyncClient:
        self.client.cookies.set("softph_session", self.admin_cookie or "")
        return self.client

    async def invite(self, email: str = EMAIL, number: int = 101) -> str:
        self.as_admin()
        self.unit_id = await self.unit(number)
        response = await self.client.post(
            f"{API}/resident-invitations",
            json={"email": email, "full_name": "Laura Gómez", "unit_id": self.unit_id, "validity_days": 7},
            headers=self.admin,
        )
        assert response.status_code == 201, response.text
        self.invitation_id = response.json()["id"]
        self.token = TOKEN_RE.search(self.mailer.sent[-1].body).group(1)  # type: ignore[union-attr]
        self.client.cookies.clear()  # from now on: the anonymous resident
        return self.token

    async def verify(self, token: Any = None, headers: dict[str, str] | None = None) -> Response:
        body = {"token": self.token if token is None else token}
        return await self.client.post(f"{API}/resident-invitations/verify", json=body, headers=headers or self.origin)

    async def activate(self, headers: dict[str, str] | None = None, **overrides: Any) -> Response:
        body = {
            "token": self.token,
            "document_number": "cc1234567",
            "phone": "+57 300 123 4567",
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            **overrides,
        }
        return await self.client.post(f"{API}/resident-invitations/activate", json=body, headers=headers or self.origin)


@pytest.fixture
async def flow(client: AsyncClient, admin_headers: dict[str, str], mailer: FakeMailSender) -> Flow:
    flow = Flow(client, admin_headers, mailer)
    await flow.invite()
    return flow


async def one(db: AsyncSession, sql: str, **params: Any) -> Any:
    return (await db.execute(text(sql), params)).mappings().one()


async def state(db: AsyncSession, flow: Flow) -> dict[str, Any]:
    user = await one(
        db,
        "SELECT u.id, u.role, u.status, u.password_hash, u.document_number, u.phone, u.deleted_at FROM users u "
        "JOIN resident_invitations i ON i.user_id = u.id WHERE i.id = :id",
        id=flow.invitation_id,
    )
    invitation = await one(db, "SELECT * FROM resident_invitations WHERE id = :id", id=flow.invitation_id)
    residences = (await db.execute(text("SELECT * FROM resident_units"))).mappings().all()
    return {"user": dict(user), "invitation": dict(invitation), "residences": [dict(r) for r in residences]}


async def actions(db: AsyncSession, *names: AuditAction) -> list[AuditLog]:
    return list((await db.scalars(select(AuditLog).where(AuditLog.action.in_(names)).order_by(AuditLog.id))).all())


async def expire(db: AsyncSession, invitation_id: int) -> None:
    await db.execute(
        text(
            "UPDATE resident_invitations SET created_at = UTC_TIMESTAMP(6) - INTERVAL 8 DAY, "
            "expires_at = UTC_TIMESTAMP(6) - INTERVAL 1 SECOND WHERE id = :id"
        ),
        {"id": invitation_id},
    )


def error(response: Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code
    return body


# --- verify (CA-4: accept the link within its validity) ------------------------------------


async def test_verify_valid_invitation(flow: Flow) -> None:
    response = await flow.verify()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body | {"expires_at": None} == {
        "email": EMAIL, "full_name": "Laura Gómez", "unit_code": "B4-TO2-APTO-101", "expires_at": None,
    }
    assert body["expires_at"].endswith("Z")


async def test_verify_is_repeatable_and_changes_nothing(flow: Flow, db_session: AsyncSession) -> None:
    before = await state(db_session, flow)
    assert (await flow.verify()).status_code == 200
    assert (await flow.verify()).status_code == 200
    assert await state(db_session, flow) == before


@pytest.mark.parametrize(
    "token",
    ["", "abc", "x" * 43, "!" * 43, "a" * 42, "a" * 44, "a" * 512],
    ids=["empty", "short", "unknown-well-formed", "bad-chars", "42-chars", "44-chars", "max-length"],
)
async def test_verify_invalid_token(flow: Flow, token: str) -> None:
    body = error(await flow.verify(token), 404, "INVITATION_INVALID")
    assert "no es válido" in body["message"]


async def test_verify_manipulated_token(flow: Flow) -> None:
    tampered = flow.token[:-1] + ("A" if flow.token[-1] != "A" else "B")
    error(await flow.verify(tampered), 404, "INVITATION_INVALID")
    error(await flow.verify(flow.token + "x"), 404, "INVITATION_INVALID")


@pytest.mark.parametrize("body", [{}, {"token": 123}, {"token": None}, {"token": "a" * 513}, {"token": "x", "email": EMAIL}])
async def test_verify_malformed_body(flow: Flow, body: dict[str, Any]) -> None:
    response = await flow.client.post(f"{API}/resident-invitations/verify", json=body, headers=flow.origin)
    error(response, 422, "VALIDATION_ERROR")


async def test_verify_expired_invitation(flow: Flow, db_session: AsyncSession) -> None:
    await expire(db_session, flow.invitation_id)

    body = error(await flow.verify(), 410, "INVITATION_EXPIRED")
    assert "ya no se encuentra vigente" in body["message"]
    current = await state(db_session, flow)
    assert current["invitation"]["status"] == "EXPIRED"
    [expired] = await actions(db_session, AuditAction.INVITATION_EXPIRED)
    assert (expired.entity_type, expired.entity_id, expired.actor_user_id) == ("resident_invitation", flow.invitation_id, None)
    assert expired.context == {"invitation_id": flow.invitation_id}

    error(await flow.verify(), 410, "INVITATION_EXPIRED")
    assert len(await actions(db_session, AuditAction.INVITATION_EXPIRED)) == 1  # recorded once


async def test_verify_used_invitation(flow: Flow) -> None:
    assert (await flow.activate()).status_code == 200
    body = error(await flow.verify(), 410, "INVITATION_USED")
    assert "ya fue utilizado" in body["message"]


# --- activate (CA-5, CA-6, CA-8) -------------------------------------------------------------


async def test_activation(flow: Flow, db_session: AsyncSession) -> None:
    before = await state(db_session, flow)

    response = await flow.activate()

    assert response.status_code == 200
    assert response.json() == {"status": "ACTIVATED"}
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers  # no session (login is HU-007)
    after = await state(db_session, flow)
    user, invitation = after["user"], after["invitation"]
    assert (user["role"], user["status"], user["deleted_at"]) == ("RESIDENTE", "ACTIVE", None)
    assert user["password_hash"].startswith("$argon2id$")
    assert user["password_hash"] != PASSWORD and PASSWORD not in user["password_hash"]
    assert verify_password(user["password_hash"], PASSWORD)
    assert (user["document_number"], user["phone"]) == ("CC1234567", "+573001234567")
    assert (invitation["status"], invitation["used_at"] is not None) == ("USED", True)
    assert invitation["expires_at"] == before["invitation"]["expires_at"]  # kept as history
    assert invitation["token_hash"] == before["invitation"]["token_hash"]
    [residence] = after["residences"]
    assert (residence["user_id"], residence["unit_id"], residence["invitation_id"]) == (
        user["id"], flow.unit_id, flow.invitation_id
    )
    assert residence["ended_at"] is None and residence["started_at"] == invitation["used_at"]
    sessions = await one(db_session, "SELECT COUNT(*) AS n FROM sessions WHERE user_id = :u", u=user["id"])
    assert sessions["n"] == 0


async def test_phone_is_optional(flow: Flow, db_session: AsyncSession) -> None:
    assert (await flow.activate(phone=None)).status_code == 200
    assert (await state(db_session, flow))["user"]["phone"] is None


async def test_phone_can_be_omitted(flow: Flow) -> None:
    response = await flow.client.post(
        f"{API}/resident-invitations/activate",
        json={"token": flow.token, "document_number": "CC1234567", "password": PASSWORD, "password_confirmation": PASSWORD},
        headers=flow.origin,
    )
    assert response.status_code == 200


async def test_activation_is_audited(flow: Flow, db_session: AsyncSession) -> None:
    await flow.activate()

    [activated] = await actions(db_session, AuditAction.RESIDENT_ACTIVATED)
    user_id = (await state(db_session, flow))["user"]["id"]
    assert (activated.entity_type, activated.entity_id, activated.actor_user_id) == ("user", user_id, user_id)
    assert activated.context == {"invitation_id": flow.invitation_id, "unit_id": flow.unit_id}
    assert activated.ip_address == "127.0.0.1"


async def test_second_use_is_rejected(flow: Flow, db_session: AsyncSession) -> None:
    assert (await flow.activate()).status_code == 200
    after_first = await state(db_session, flow)

    error(await flow.activate(document_number="CC7654321", password="otra clave segura 99", password_confirmation="otra clave segura 99"), 410, "INVITATION_USED")

    assert await state(db_session, flow) == after_first  # no second password, no second residence
    [rejected] = await actions(db_session, AuditAction.INVITATION_REJECTED)
    assert rejected.context == {"reason": "used"} and rejected.entity_id == flow.invitation_id


async def test_activate_expired_invitation(flow: Flow, db_session: AsyncSession) -> None:
    await expire(db_session, flow.invitation_id)

    error(await flow.activate(), 410, "INVITATION_EXPIRED")

    current = await state(db_session, flow)
    assert current["invitation"]["status"] == "EXPIRED" and current["invitation"]["used_at"] is None
    assert current["user"]["status"] == "PENDING_ACTIVATION" and current["user"]["password_hash"] is None
    assert current["residences"] == []
    assert [a.action for a in await actions(db_session, AuditAction.INVITATION_EXPIRED, AuditAction.INVITATION_REJECTED)] == [
        AuditAction.INVITATION_EXPIRED, AuditAction.INVITATION_REJECTED
    ]
    error(await flow.activate(), 410, "INVITATION_EXPIRED")  # the EXPIRED row is never reactivated


async def test_unknown_or_manipulated_token_on_activate(flow: Flow, db_session: AsyncSession) -> None:
    before = await state(db_session, flow)
    error(await flow.activate(token="x" * 43), 404, "INVITATION_INVALID")
    error(await flow.activate(token=flow.token[:-1] + ("A" if flow.token[-1] != "A" else "B")), 404, "INVITATION_INVALID")
    error(await flow.activate(token=""), 404, "INVITATION_INVALID")
    assert await state(db_session, flow) == before
    rejected = await actions(db_session, AuditAction.INVITATION_REJECTED)
    assert [(r.context, r.entity_id) for r in rejected] == [({"reason": "invalid"}, None)] * 3


async def test_resend_invalidates_the_previous_token(flow: Flow, db_session: AsyncSession) -> None:
    old_token = flow.token
    flow.as_admin()
    assert (await flow.client.post(f"{API}/resident-invitations/{flow.invitation_id}/resend", headers=flow.admin)).status_code == 200
    flow.client.cookies.clear()
    new_token = TOKEN_RE.search(flow.mailer.sent[-1].body).group(1)  # type: ignore[union-attr]

    error(await flow.verify(old_token), 404, "INVITATION_INVALID")
    error(await flow.activate(token=old_token), 404, "INVITATION_INVALID")
    flow.token = new_token
    assert (await flow.activate()).status_code == 200


# --- Rules that need the database (CA-5) -------------------------------------------------------


async def test_duplicate_document_changes_nothing(flow: Flow, db_session: AsyncSession) -> None:
    await db_session.execute(
        text(
            "INSERT INTO users (email, full_name, role, status, document_number) "
            "VALUES ('otro@example.com', 'Otra Persona', 'RESIDENTE', 'PENDING_ACTIVATION', 'CC1234567')"
        )
    )
    before = await state(db_session, flow)

    body = error(await flow.activate(document_number=" cc1234567 "), 409, "DOCUMENT_ALREADY_REGISTERED")

    assert body["message"] == "El número de documento ya está registrado."
    assert await state(db_session, flow) == before
    assert (await flow.activate(document_number="CC9999999")).status_code == 200  # still usable


async def test_password_equal_to_email_is_rejected(flow: Flow, db_session: AsyncSession) -> None:
    before = await state(db_session, flow)
    body = error(await flow.activate(password=f" {EMAIL.upper()} ", password_confirmation=f" {EMAIL.upper()} "), 422, "VALIDATION_ERROR")
    assert body["details"] == [{"field": "password", "message": "La contraseña no puede ser igual al correo electrónico."}]
    assert await state(db_session, flow) == before


@pytest.mark.parametrize(
    ("update", "status"),
    [
        ("status = 'ACTIVE', password_hash = 'not-a-real-hash'", "ACTIVE"),
        ("status = 'DISABLED'", "DISABLED"),
        ("deleted_at = UTC_TIMESTAMP(6)", "PENDING_ACTIVATION"),
        ("role = 'ADMINISTRADOR'", "PENDING_ACTIVATION"),
    ],
    ids=["active", "disabled", "deleted", "not-a-resident"],
)
async def test_user_not_pending_is_rejected(flow: Flow, db_session: AsyncSession, update: str, status: str) -> None:
    await db_session.execute(
        text(f"UPDATE users SET {update} WHERE email = :email"), {"email": EMAIL}
    )
    before = await state(db_session, flow)

    error(await flow.verify(), 404, "INVITATION_INVALID")
    error(await flow.activate(), 404, "INVITATION_INVALID")

    after = await state(db_session, flow)
    assert after == before  # no status conversion, no residence, invitation still PENDING
    assert after["user"]["status"] == status and after["invitation"]["status"] == "PENDING"
    [rejected] = await actions(db_session, AuditAction.INVITATION_REJECTED)
    assert rejected.context == {"reason": "user_not_pending"}


async def test_occupied_unit_is_rejected(flow: Flow, db_session: AsyncSession) -> None:
    # Another resident already lives there (simulated with SQL; the PENDING invitation
    # normally prevents it).
    await db_session.execute(
        text(
            "INSERT INTO users (email, full_name, role, status, password_hash) "
            "VALUES ('vecino@example.com', 'Otro Vecino', 'RESIDENTE', 'ACTIVE', 'not-a-real-hash')"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO resident_invitations (token_hash, user_id, unit_id, invited_by_user_id, validity_days, status, "
            "created_at, expires_at, used_at) SELECT REPEAT('d', 64), u.id, :unit, u.id, 7, 'USED', UTC_TIMESTAMP(6), "
            "UTC_TIMESTAMP(6) + INTERVAL 1 DAY, UTC_TIMESTAMP(6) FROM users u WHERE u.email = 'vecino@example.com'"
        ),
        {"unit": flow.unit_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO resident_units (user_id, unit_id, invitation_id, started_at) "
            "SELECT user_id, unit_id, id, UTC_TIMESTAMP(6) FROM resident_invitations WHERE token_hash = REPEAT('d', 64)"
        )
    )
    before = await state(db_session, flow)

    error(await flow.activate(), 409, "UNIT_NOT_AVAILABLE")

    assert await state(db_session, flow) == before
    [rejected] = await actions(db_session, AuditAction.INVITATION_REJECTED)
    assert rejected.context == {"reason": "unit_not_available"}


# --- Validation (CA-4: complete the required information) ----------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field", "message"),
    [
        ({"document_number": ""}, "document_number", "El número de documento es obligatorio."),
        ({"document_number": "   "}, "document_number", "El número de documento es obligatorio."),
        ({"document_number": None}, "document_number", "El número de documento es obligatorio."),
        ({"document_number": "12-34"}, "document_number", "El número de documento debe tener entre 5 y 20 letras o dígitos, sin espacios ni símbolos."),
        ({"phone": "12ab"}, "phone", "El teléfono debe tener entre 7 y 15 dígitos, con un «+» inicial opcional."),
        ({"password": "corta", "password_confirmation": "corta"}, "password", "La contraseña debe tener entre 12 y 128 caracteres."),
        ({"password": "x" * 129, "password_confirmation": "x" * 129}, "password", "La contraseña debe tener entre 12 y 128 caracteres."),
        ({"password": " " * 12, "password_confirmation": " " * 12}, "password", "La contraseña no puede estar formada solo por espacios."),
        ({"password_confirmation": "otra clave distinta"}, "password_confirmation", "La confirmación no coincide con la contraseña."),
        ({"user_id": 1}, "user_id", "Este campo no está permitido."),
        ({"unit_id": 1}, "unit_id", "Este campo no está permitido."),
        ({"invitation_id": 1}, "invitation_id", "Este campo no está permitido."),
        ({"email": "otro@example.com"}, "email", "Este campo no está permitido."),
    ],
)
async def test_invalid_activation_data(
    flow: Flow, db_session: AsyncSession, overrides: dict[str, Any], field: str, message: str
) -> None:
    before = await state(db_session, flow)

    response = await flow.activate(**overrides)

    body = error(response, 422, "VALIDATION_ERROR")
    assert body["details"] == [{"field": field, "message": message}]
    assert flow.token not in response.text and "corta" not in response.text
    assert await state(db_session, flow) == before


async def test_password_limits(flow: Flow) -> None:
    exact = "a" * 128
    assert (await flow.activate(password=exact, password_confirmation=exact)).status_code == 200


# --- Integration with HU-003, HU-013 and HU-002 ---------------------------------------------------


async def test_activated_unit_is_occupied_and_email_is_registered(flow: Flow) -> None:
    await flow.activate()
    client = flow.as_admin()

    available = (await client.get(f"{API}/units/available")).json()["items"]
    assert flow.unit_id not in [u["unit_id"] for u in available]
    response = await client.delete(f"{API}/units/{flow.unit_id}", params={"version": 1}, headers=flow.admin)
    error(response, 409, "UNIT_IN_USE")
    other_unit = await flow.unit(102)
    response = await client.post(
        f"{API}/resident-invitations",
        json={"email": EMAIL, "full_name": "Laura Gómez", "unit_id": other_unit, "validity_days": 7},
        headers=flow.admin,
    )
    error(response, 409, "EMAIL_ALREADY_REGISTERED")
    assert [i["id"] for i in (await client.get(f"{API}/resident-invitations", params={"status": "PENDING"})).json()["items"]] == []


async def test_resident_logs_in_only_after_activation(flow: Flow) -> None:
    # HU-019 activates the account; HU-007 lets the resident log in with it.
    # (Before HU-007 this test asserted a 401 even after the activation.)
    body = {"email": EMAIL, "password": PASSWORD}
    error(await flow.client.post(f"{API}/auth/login", json=body, headers=flow.origin), 401, "INVALID_CREDENTIALS")

    await flow.activate()
    response = await flow.client.post(f"{API}/auth/login", json=body, headers=flow.origin)

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "RESIDENTE"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def test_only_the_hash_is_stored(flow: Flow, db_session: AsyncSession) -> None:
    await flow.activate()
    current = await state(db_session, flow)
    assert current["invitation"]["token_hash"] == sha256(flow.token)
    dump = str(current) + str((await db_session.execute(text("SELECT * FROM audit_logs"))).all())
    assert flow.token not in dump and PASSWORD not in dump
