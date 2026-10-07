"""HU-005: two administrators at the same time (independent connections, real commits).

Each change locks the feedback row (FOR UPDATE) and checks the version the
administrator saw: exactly one change wins; the other gets VERSION_CONFLICT.
"""

import asyncio
from collections.abc import Awaitable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AppError, FeedbackVersionConflictError
from app.models import User, UserRole, UserStatus
from app.schemas.feedback import FeedbackResolution, FeedbackResponse
from app.services import feedback as feedback_service
from app.services.structure import Actor
from tests.integration.conftest import FakeMailSender
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def setup(committed_session_factory: Factory) -> dict[str, int]:
    async with committed_session_factory() as session:
        admins = [User(email=f"admin{n}@example.com", full_name=f"Admin {name}", role=UserRole.ADMINISTRADOR,
                       status=UserStatus.ACTIVE if n == 1 else UserStatus.DISABLED, password_hash="x")
                  for n, name in ((1, "Uno"), (2, "Dos"))]
        session.add_all(admins)
        await session.flush()
        resident = await Community(session, admins[0].id).resident("Laura Gómez", "1012345678", (1, 1, 101))
        feedback_id = await feedback_service.create_feedback(session, resident, subject="Ruido", message="Hay ruido.",
                                                             category="COMPLAINT", urgency="MEDIUM")
        ids = {"admin1": admins[0].id, "admin2": admins[1].id, "feedback": feedback_id}
        await session.commit()
    return ids


async def run(factory: Factory, operation: Any, *args: Any) -> Any:
    async with factory() as session:
        try:
            return await operation(session, *args, mailer=FakeMailSender())
        except AppError as exc:
            return exc


async def row(factory: Factory, feedback_id: int) -> Any:
    async with factory() as session:
        return (await session.execute(text(
            "SELECT status, version, admin_response, resolution_note FROM feedback WHERE id = :id"), {"id": feedback_id})).one()


async def audit_count(factory: Factory, action: str) -> int:
    async with factory() as session:
        return int(await session.scalar(text("SELECT COUNT(*) FROM audit_logs WHERE action = :a"), {"a": action}) or 0)


async def gather(*coroutines: Awaitable[Any]) -> list[Any]:
    try:
        return list(await asyncio.wait_for(asyncio.gather(*coroutines), timeout=60))
    except DBAPIError as exc:
        pytest.fail(f"deadlock or database error: {exc.orig}")


def both_read_the_same_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both administrators reach the lock at the same moment, with the version they saw (1)."""
    barrier = asyncio.Barrier(2)
    real = feedback_service._lock

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        await barrier.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(feedback_service, "_lock", wrapper)


async def test_two_administrators_resolve_at_the_same_time(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    both_read_the_same_version(monkeypatch)
    feedback_id = setup["feedback"]

    results = await gather(
        run(committed_session_factory, feedback_service.resolve, feedback_id,
            FeedbackResolution(resolution_note="Nota del administrador uno", version=1), Actor(setup["admin1"], None)),
        run(committed_session_factory, feedback_service.resolve, feedback_id,
            FeedbackResolution(resolution_note="Nota del administrador dos", version=1), Actor(setup["admin2"], None)),
    )

    winners = [r for r in results if not isinstance(r, Exception)]
    assert len(winners) == 1 and sum(isinstance(r, FeedbackVersionConflictError) for r in results) == 1
    status, version, _, note = await row(committed_session_factory, feedback_id)
    assert (status, version) == ("RESOLVED", 2) and note == winners[0].feedback.resolution_note  # only one final resolution
    assert await audit_count(committed_session_factory, "FEEDBACK_RESOLVED") == 1


async def test_response_and_resolution_at_the_same_time(
    committed_session_factory: Factory, setup: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    both_read_the_same_version(monkeypatch)
    feedback_id = setup["feedback"]

    responded, resolved = await gather(
        run(committed_session_factory, feedback_service.respond, feedback_id,
            FeedbackResponse(response="Respuesta", version=1), Actor(setup["admin1"], None)),
        run(committed_session_factory, feedback_service.resolve, feedback_id,
            FeedbackResolution(resolution_note="Nota", version=1), Actor(setup["admin2"], None)),
    )

    # One change only: never a response written over a resolution made at the same time.
    assert [isinstance(r, FeedbackVersionConflictError) for r in (responded, resolved)].count(True) == 1
    status, version, response, note = await row(committed_session_factory, feedback_id)
    assert version == 2
    assert (status, response, note) in {("PENDING", "Respuesta", None), ("RESOLVED", None, "Nota")}


async def test_many_responses_in_sequence_never_lose_an_update(
    committed_session_factory: Factory, setup: dict[str, int]
) -> None:
    feedback_id = setup["feedback"]
    for version in range(1, 6):
        result = await run(committed_session_factory, feedback_service.respond, feedback_id,
                           FeedbackResponse(response=f"Respuesta {version}", version=version), Actor(setup["admin1"], None))
        assert result.feedback.version == version + 1
    assert (await row(committed_session_factory, feedback_id))[1:3] == (6, "Respuesta 5")
    assert await audit_count(committed_session_factory, "FEEDBACK_RESPONDED") == 5
