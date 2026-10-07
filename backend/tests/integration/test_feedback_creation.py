"""HU-009: create_feedback on the test database, and HU-005 with the feedback stored before HU-009.

The feedback stored before HU-009 has no category and no urgency (NULL): it is
inserted here with SQL, exactly as HU-005 left it, and the administrator keeps
listing, reading, answering and resolving it without anything being invented.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FeedbackResidentNotValidError, FieldValidationError
from app.models import AuditAction, AuditLog, Feedback, FeedbackStatus, User, UserRole, UserStatus
from app.services import feedback as feedback_service
from tests.integration.conftest import FakeMailSender, sign_in
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

URL = "/api/v1/admin/feedback"
CHECK = 3819  # MySQL: check constraint violated


@pytest.fixture
async def admin(make_user: Any) -> User:
    return await make_user("admin@example.com", password_hash="not-a-real-hash")


@pytest.fixture
async def laura(db_session: AsyncSession, admin: User) -> int:
    return await Community(db_session, admin.id).resident("Laura Gómez", "1012345678", (1, 1, 101), email="laura@example.com")


async def create(db: AsyncSession, resident_id: int, **changes: Any) -> int:
    fields = {"subject": "Ruido", "message": "Hay ruido después de las 10 p. m.", "category": "COMPLAINT",
              "urgency": "HIGH", **changes}
    return await feedback_service.create_feedback(db, resident_id, **fields)


async def count(db: AsyncSession, table: str) -> int:
    return int(await db.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)


# --- create_feedback (domain) -------------------------------------------------------------------------------


async def test_a_new_feedback_is_pending_with_its_category_and_urgency(db_session: AsyncSession, laura: int) -> None:
    feedback_id = await create(db_session, laura, subject="  Ruido  ", message=" Hola.\nHay ruido. ", category="REQUEST",
                               urgency="LOW", ip_address="203.0.113.7")

    row = (await db_session.execute(text(
        "SELECT resident_id, subject, message, category, urgency, status, admin_response, resolution_note, resolved_at, "
        "version FROM feedback WHERE id = :id"), {"id": feedback_id})).one()
    assert tuple(row) == (laura, "Ruido", "Hola.\nHay ruido.", "REQUEST", "LOW", "PENDING", None, None, None, 1)


async def test_subject_is_optional(db_session: AsyncSession, laura: int) -> None:
    feedback_id = await create(db_session, laura, subject=None, category="SUGGESTION", urgency="MEDIUM")
    assert await db_session.scalar(select(Feedback.subject).where(Feedback.id == feedback_id)) is None


async def test_the_creation_is_audited_without_the_texts(db_session: AsyncSession, laura: int) -> None:
    feedback_id = await create(db_session, laura, subject=None, category="OTHER", urgency="MEDIUM", ip_address="203.0.113.7")

    [entry] = (await db_session.scalars(select(AuditLog).where(AuditLog.action == AuditAction.FEEDBACK_CREATED))).all()
    assert (entry.entity_type, entry.entity_id, entry.actor_user_id, entry.ip_address) == ("feedback", feedback_id, laura,
                                                                                           "203.0.113.7")
    assert entry.context == {"category": "OTHER", "urgency": "MEDIUM", "with_subject": False}
    assert "ruido" not in str(entry.context).lower()


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"category": None}, "category"),
        ({"urgency": None}, "urgency"),
        ({"category": "QUEJA"}, "category"),
        ({"urgency": "URGENT"}, "urgency"),
        ({"message": "   "}, "message"),
        ({"message": "a" * 2001}, "message"),
        ({"subject": "a" * 151}, "subject"),
    ],
)
async def test_invalid_fields_store_nothing(db_session: AsyncSession, laura: int, changes: dict[str, Any], field: str) -> None:
    with pytest.raises(FieldValidationError) as error:
        await create(db_session, laura, **changes)
    assert error.value.details[0]["field"] == field  # type: ignore[index]
    assert await count(db_session, "feedback") == 0
    assert await count(db_session, "audit_logs") == 0


async def test_the_limits_are_accepted(db_session: AsyncSession, laura: int) -> None:
    feedback_id = await create(db_session, laura, subject="s" * 150, message="m" * 2000)
    row = (await db_session.execute(select(Feedback.subject, Feedback.message).where(Feedback.id == feedback_id))).one()
    assert (len(row.subject), len(row.message)) == (150, 2000)


async def test_the_caller_cannot_choose_status_response_note_or_dates(db_session: AsyncSession, laura: int) -> None:
    for extra in ({"status": "RESOLVED"}, {"admin_response": "x"}, {"resolution_note": "x"}, {"resolved_at": None}):
        with pytest.raises(TypeError):
            await create(db_session, laura, **extra)
    assert await count(db_session, "feedback") == 0


async def owner(case: str, make_user: Any, admin: User) -> int:
    if case == "administrator":
        return admin.id
    if case == "missing":
        return 999_999
    state = {"pending-activation": {"status": UserStatus.PENDING_ACTIVATION, "password": None},
             "disabled": {"status": UserStatus.DISABLED}, "deleted": {"deleted": True}}[case]
    return (await make_user(f"{case}@example.com", role=UserRole.RESIDENTE, **state)).id


@pytest.mark.parametrize("case", ["administrator", "pending-activation", "disabled", "deleted", "missing"])
async def test_only_an_active_resident_can_own_a_feedback(db_session: AsyncSession, make_user: Any, admin: User,
                                                          case: str) -> None:
    with pytest.raises(FeedbackResidentNotValidError):
        await create(db_session, await owner(case, make_user, admin))
    assert await count(db_session, "feedback") == 0
    assert await count(db_session, "audit_logs") == 0


# --- Rules MySQL enforces itself ---------------------------------------------------------------------------


async def rejects(db: AsyncSession, sql: str, **params: Any) -> None:
    async with db.begin_nested():
        with pytest.raises((IntegrityError, DBAPIError)) as error:
            await db.execute(text(sql), params)
    assert error.value.orig.args[0] == CHECK, error.value.orig  # type: ignore[union-attr]


async def test_the_database_only_accepts_the_approved_values_both_or_neither(db_session: AsyncSession, laura: int) -> None:
    insert = "INSERT INTO feedback (resident_id, message, category, urgency) VALUES (:r, 'Hola', :c, :u)"
    for category in ("COMPLAINT", "SUGGESTION", "REQUEST", "OTHER"):
        for urgency in ("HIGH", "MEDIUM", "LOW"):
            await db_session.execute(text(insert), {"r": laura, "c": category, "u": urgency})
    await db_session.execute(text(insert), {"r": laura, "c": None, "u": None})  # stored before HU-009
    assert await count(db_session, "feedback") == 13

    for category, urgency in (("QUEJA", "HIGH"), ("complaint", "HIGH"), ("", "HIGH"), ("OTHER", "URGENT"),
                              ("OTHER", "high"), ("OTHER", None), (None, "LOW")):
        await rejects(db_session, insert, r=laura, c=category, u=urgency)
    feedback_id = await create(db_session, laura)
    await rejects(db_session, "UPDATE feedback SET category = NULL WHERE id = :f", f=feedback_id)
    await rejects(db_session, "UPDATE feedback SET urgency = 'NONE' WHERE id = :f", f=feedback_id)


# --- HU-005 with the feedback stored before HU-009 (category and urgency NULL) ------------------------------


@pytest.fixture
async def historical(db_session: AsyncSession, laura: int) -> int:
    await db_session.execute(text(
        "INSERT INTO feedback (resident_id, subject, message) VALUES (:r, 'Antes de HU-009', 'Mensaje anterior.')"),
        {"r": laura})
    return int(await db_session.scalar(text("SELECT MAX(id) FROM feedback")) or 0)


async def stored(db: AsyncSession, feedback_id: int) -> tuple[Any, ...]:
    return tuple((await db.execute(text(
        "SELECT subject, message, category, urgency FROM feedback WHERE id = :id"), {"id": feedback_id})).one())


async def test_the_administrator_still_manages_historical_feedback(
    client: AsyncClient, db_session: AsyncSession, admin: User, laura: int, historical: int, mailer: FakeMailSender
) -> None:
    new = await create(db_session, laura, subject="Nuevo")
    headers = await sign_in(client, db_session, admin)

    listed = (await client.get(URL)).json()
    assert [(i["id"], i["subject"], i["status"]) for i in listed["items"]] == [
        (new, "Nuevo", "PENDING"), (historical, "Antes de HU-009", "PENDING")]
    detail = await client.get(f"{URL}/{historical}")
    assert detail.status_code == 200 and detail.json()["message"] == "Mensaje anterior."

    answered = await client.post(f"{URL}/{historical}/response", json={"response": "Lo revisamos.", "version": 1},
                                 headers=headers)
    assert answered.status_code == 200, answered.text
    resolved = await client.post(f"{URL}/{historical}/resolve", json={"resolution_note": "Gestionado.", "version": 2},
                                 headers=headers)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["feedback"]["status"] == FeedbackStatus.RESOLVED
    assert [h["action"] for h in resolved.json()["feedback"]["action_history"]] == [
        "FEEDBACK_RESPONDED", "FEEDBACK_NOTIFICATION_SENT", "FEEDBACK_RESOLVED", "FEEDBACK_NOTIFICATION_SENT"]
    assert len(mailer.sent) == 2

    # Nothing was invented for it, and nothing else of the original message changed.
    assert await stored(db_session, historical) == ("Antes de HU-009", "Mensaje anterior.", None, None)
    assert await stored(db_session, new) == ("Nuevo", "Hay ruido después de las 10 p. m.", "COMPLAINT", "HIGH")
