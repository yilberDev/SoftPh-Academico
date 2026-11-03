"""HU-009: the resident's feedback (service), on the test database.

Laura has four feedback: f1 (COMPLAINT/HIGH), f2 resolved by the administrator
(with a response and an internal note), f3 sent at the same instant as f2, and
f4 stored before HU-009 (no category, no urgency). Pedro has one.
"""

from collections.abc import Iterator
from dataclasses import asdict
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FeedbackNotFoundError
from app.models import Feedback, FeedbackCategory, FeedbackStatus, FeedbackUrgency, User
from app.services import feedback as feedback_service
from app.services.resident_feedback import (
    ResidentFeedbackDetail,
    ResidentFeedbackQuery,
    get_resident_feedback,
    list_resident_feedback,
)
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

RESPONSE = "Respuesta de la administración E2E"
NOTE = "Nota interna: se llamó al vecino"


async def at(db: AsyncSession, feedback_id: int, moment: datetime) -> None:
    await db.execute(text("UPDATE feedback SET created_at = :m WHERE id = :id"), {"m": moment, "id": feedback_id})


@pytest.fixture
async def world(db_session: AsyncSession, make_user: Any) -> dict[str, int]:
    admin = await make_user("admin.feedback@example.com", password_hash="not-a-real-hash")
    community = Community(db_session, admin.id)
    ids = {"admin": admin.id}
    ids["laura"] = await community.resident("Laura Gómez", "1012345678", (1, 1, 101), email="laura@example.com")
    ids["pedro"] = await community.resident("Pedro Ruiz", "1012345679", (1, 1, 102), email="pedro@example.com")

    async def create(resident: str, subject: str | None, message: str, category: str, urgency: str) -> int:
        return await feedback_service.create_feedback(db_session, ids[resident], subject=subject, message=message,
                                                      category=category, urgency=urgency)

    ids["f1"] = await create("laura", "Ruido", "Hay ruido después de las 10 p. m.", "COMPLAINT", "HIGH")
    ids["f2"] = await create("laura", None, "Más bancas en el parque.", "SUGGESTION", "LOW")
    ids["f3"] = await create("laura", "Portería", "Solicito copia de llaves.", "REQUEST", "MEDIUM")
    await db_session.execute(text(
        "INSERT INTO feedback (resident_id, subject, message) VALUES (:r, 'Antes de HU-009', 'Mensaje anterior.')"),
        {"r": ids["laura"]})
    ids["f4"] = int(await db_session.scalar(select(func.max(Feedback.id))) or 0)
    ids["p1"] = await create("pedro", "Agua", "No hay agua en la torre.", "COMPLAINT", "HIGH")

    await at(db_session, ids["f1"], datetime(2026, 10, 3, 15, 0))
    await at(db_session, ids["f2"], datetime(2026, 10, 4, 15, 0))
    await at(db_session, ids["f3"], datetime(2026, 10, 4, 15, 0))  # same instant as f2: the higher id first
    await at(db_session, ids["f4"], datetime(2026, 9, 30, 15, 0))
    await at(db_session, ids["p1"], datetime(2026, 10, 5, 15, 0))
    # The administrator answers and resolves f2 (HU-005): a response and an internal note.
    await db_session.execute(text(
        "UPDATE feedback SET admin_response = :r, resolution_note = :n, status = 'RESOLVED', "
        "resolved_at = '2026-10-05 12:00:00', version = 3 WHERE id = :id"), {"r": RESPONSE, "n": NOTE, "id": ids["f2"]})
    return ids


async def listed(db: AsyncSession, resident_id: int, **query: Any) -> list[int]:
    page = await list_resident_feedback(db, resident_id, ResidentFeedbackQuery(**query))
    return [item.id for item in page.items]


# --- 1-10. List ---------------------------------------------------------------------------------------------


async def test_only_the_residents_feedback_newest_first_with_stable_ties(db_session: AsyncSession, world: dict[str, int]) -> None:
    w = world
    assert await listed(db_session, w["laura"]) == [w["f3"], w["f2"], w["f1"], w["f4"]]
    assert await listed(db_session, w["pedro"]) == [w["p1"]]


async def test_a_resident_without_feedback_gets_an_empty_page(db_session: AsyncSession, world: dict[str, int]) -> None:
    newcomer = await Community(db_session, world["admin"]).resident("Nuevo Vecino", "1012345690", None,
                                                                    email="nuevo@example.com")
    page = await list_resident_feedback(db_session, newcomer, ResidentFeedbackQuery())
    assert (page.items, page.page, page.page_size, page.total, page.total_pages) == ((), 1, 10, 0, 0)


async def test_list_rows(db_session: AsyncSession, world: dict[str, int]) -> None:
    page = await list_resident_feedback(db_session, world["laura"], ResidentFeedbackQuery())
    rows = {item.id: item for item in page.items}
    f1 = rows[world["f1"]]
    assert (f1.subject, f1.category, f1.urgency, f1.status, f1.created_at) == (
        "Ruido", FeedbackCategory.COMPLAINT, FeedbackUrgency.HIGH, FeedbackStatus.PENDING, datetime(2026, 10, 3, 15, 0))
    assert (rows[world["f2"]].subject, rows[world["f2"]].status) == (None, FeedbackStatus.RESOLVED)


async def test_pagination_total_and_total_pages(db_session: AsyncSession, world: dict[str, int]) -> None:
    w = world
    first = await list_resident_feedback(db_session, w["laura"], ResidentFeedbackQuery(page=1, page_size=3))
    second = await list_resident_feedback(db_session, w["laura"], ResidentFeedbackQuery(page=2, page_size=3))
    assert ([i.id for i in first.items], first.total, first.total_pages) == ([w["f3"], w["f2"], w["f1"]], 4, 2)
    assert ([i.id for i in second.items], second.page, second.total, second.total_pages) == ([w["f4"]], 2, 4, 2)
    assert await listed(db_session, w["laura"], page=1, page_size=4) == [w["f3"], w["f2"], w["f1"], w["f4"]]


async def test_a_page_beyond_the_last_is_empty(db_session: AsyncSession, world: dict[str, int]) -> None:
    page = await list_resident_feedback(db_session, world["laura"], ResidentFeedbackQuery(page=3, page_size=3))
    assert (page.items, page.page, page.total, page.total_pages) == ((), 3, 4, 2)


# --- 11-15. Ownership ----------------------------------------------------------------------------------------


async def test_a_resident_never_gets_another_residents_feedback(db_session: AsyncSession, world: dict[str, int]) -> None:
    w = world
    assert w["p1"] not in await listed(db_session, w["laura"], page_size=100)
    with pytest.raises(FeedbackNotFoundError):
        await get_resident_feedback(db_session, w["laura"], w["p1"])
    with pytest.raises(FeedbackNotFoundError):
        await get_resident_feedback(db_session, w["pedro"], w["f1"])
    assert (await get_resident_feedback(db_session, w["pedro"], w["p1"])).message == "No hay agua en la torre."


async def test_missing_and_foreign_feedback_look_the_same(db_session: AsyncSession, world: dict[str, int]) -> None:
    missing = int(await db_session.scalar(select(func.max(Feedback.id))) or 0) + 1000
    errors = []
    for feedback_id in (world["p1"], missing, 0, -1):  # another resident's, missing, invalid ids
        with pytest.raises(FeedbackNotFoundError) as error:
            await get_resident_feedback(db_session, world["laura"], feedback_id)
        errors.append((error.value.status_code, error.value.code, error.value.message, error.value.details,
                       str(error.value)))
    assert len(set(map(str, errors))) == 1
    assert errors[0] == (404, "NOT_FOUND", "El feedback no existe.", None, "El feedback no existe.")
    # Nothing of the foreign feedback is in the error.
    assert "agua" not in str(errors).lower() and "Pedro" not in str(errors)


async def test_the_resident_is_always_required(db_session: AsyncSession, world: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="resident_id"):
        await get_resident_feedback(db_session, None, world["f1"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="resident_id"):
        await list_resident_feedback(db_session, 0, ResidentFeedbackQuery())


# --- 16-22. Detail and 23-26. Privacy -----------------------------------------------------------------------


async def test_detail(db_session: AsyncSession, world: dict[str, int]) -> None:
    detail = await get_resident_feedback(db_session, world["laura"], world["f1"])
    assert detail == ResidentFeedbackDetail(
        id=world["f1"], subject="Ruido", message="Hay ruido después de las 10 p. m.", category=FeedbackCategory.COMPLAINT,
        urgency=FeedbackUrgency.HIGH, status=FeedbackStatus.PENDING, created_at=datetime(2026, 10, 3, 15, 0),
        admin_response=None, resolved_at=None,
    )


async def test_a_resolved_feedback_shows_the_response_and_the_resolution_date_but_never_the_internal_note(
    db_session: AsyncSession, world: dict[str, int]
) -> None:
    detail = await get_resident_feedback(db_session, world["laura"], world["f2"])
    assert (detail.status, detail.subject, detail.message) == (FeedbackStatus.RESOLVED, None, "Más bancas en el parque.")
    # HU-005 3.2 / HU-017 1: the response and the resolution date reach the resident's detail.
    assert (detail.admin_response, detail.resolved_at) == (RESPONSE, datetime(2026, 10, 5, 12, 0))
    admin_email = await db_session.scalar(select(User.email).where(User.id == world["admin"]))
    for absent in (NOTE, "resolution_note", "Laura", "laura@example.com", "Pedro", "No hay agua", admin_email,
                   "resident_id", "version"):
        assert absent not in str(asdict(detail)), absent
    # The list carries none of the administrator's fields.
    page = await list_resident_feedback(db_session, world["laura"], ResidentFeedbackQuery(page_size=100))
    listed_dump = str([asdict(item) for item in page.items])
    for absent in (NOTE, RESPONSE, "resolution_note", "admin_response", "resolved_at", "2026, 10, 5, 12", admin_email):
        assert absent not in listed_dump, absent


# --- 27-29. Feedback stored before HU-009 --------------------------------------------------------------------


async def test_historical_feedback_keeps_null_category_and_urgency(db_session: AsyncSession, world: dict[str, int]) -> None:
    detail = await get_resident_feedback(db_session, world["laura"], world["f4"])
    assert (detail.category, detail.urgency, detail.subject, detail.message, detail.status) == (
        None, None, "Antes de HU-009", "Mensaje anterior.", FeedbackStatus.PENDING)
    [row] = [i for i in (await list_resident_feedback(db_session, world["laura"], ResidentFeedbackQuery())).items
             if i.id == world["f4"]]
    assert (row.category, row.urgency) == (None, None)


# --- 30-32. Reading changes nothing -------------------------------------------------------------------------


async def snapshot(db: AsyncSession) -> tuple[Any, ...]:
    rows = (await db.execute(text(
        "SELECT id, subject, message, category, urgency, status, admin_response, resolution_note, version, created_at, "
        "updated_at, resolved_at FROM feedback ORDER BY id"))).all()
    audits = await db.scalar(text("SELECT COUNT(*) FROM audit_logs"))
    return tuple(map(tuple, rows)), audits


async def test_reading_changes_nothing(db_session: AsyncSession, world: dict[str, int]) -> None:
    before = await snapshot(db_session)
    await list_resident_feedback(db_session, world["laura"], ResidentFeedbackQuery())
    await get_resident_feedback(db_session, world["laura"], world["f2"])
    with pytest.raises(FeedbackNotFoundError):
        await get_resident_feedback(db_session, world["laura"], world["p1"])
    assert await snapshot(db_session) == before  # rows, versions and audit entries


# --- Queries ------------------------------------------------------------------------------------------------


@pytest.fixture
def statements() -> Iterator[list[str]]:
    seen: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if not statement.lstrip().upper().startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


async def test_the_number_of_queries_does_not_grow_with_the_rows(
    db_session: AsyncSession, world: dict[str, int], statements: list[str]
) -> None:
    await listed(db_session, world["pedro"])  # 1 feedback
    assert len(statements) == 2
    statements.clear()
    await listed(db_session, world["laura"])  # 4 feedback
    assert len(statements) == 2  # one COUNT, one page
    assert all("resident_id" in statement for statement in statements)
    assert not any("admin_response" in statement or "resolution_note" in statement for statement in statements)
    statements.clear()
    await listed(db_session, world["laura"], page=9)
    assert len(statements) == 1  # beyond the last page: only the COUNT
    statements.clear()
    await get_resident_feedback(db_session, world["laura"], world["f1"])
    assert len(statements) == 1
    assert "resolution_note" not in statements[0]  # the internal note is never read; the response is (HU-005 3.2)
    assert "users" not in statements[0]  # no join: nothing about people is read
