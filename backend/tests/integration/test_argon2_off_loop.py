"""M-1: no Argon2 work runs on the event loop thread in real request flows."""

import threading
from collections.abc import Awaitable, Callable

import pytest
from argon2 import PasswordHasher
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import passwords
from app.models import User
from app.schemas.user import AdminCreate
from app.services import auth
from app.services.users import create_first_administrator
from tests.integration.conftest import PASSWORD

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]


class RecordingHasher:
    """Delegates to the real hasher and records the thread of each Argon2 call."""

    def __init__(self, real: PasswordHasher) -> None:
        self.real = real
        self.threads: list[int] = []

    def hash(self, password: str) -> str:
        self.threads.append(threading.get_ident())
        return self.real.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        self.threads.append(threading.get_ident())
        return self.real.verify(password_hash, password)

    def check_needs_rehash(self, password_hash: str) -> bool:
        return self.real.check_needs_rehash(password_hash)  # parsing only, no hashing


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> RecordingHasher:
    hasher = RecordingHasher(PasswordHasher())
    monkeypatch.setattr(passwords, "_hasher", hasher)
    monkeypatch.setattr(auth, "_dummy_hash", None)  # force the dummy hash to be computed
    return hasher


async def test_login_flows_never_hash_on_the_event_loop(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, recorder: RecordingHasher
) -> None:
    await make_user()
    recorder.threads.clear()  # the fixture hashed the test password itself; only app calls count
    loop_thread = threading.get_ident()

    for email, password, status in [
        ("nobody@example.com", "wrong password", 401),  # dummy hash + verify
        ("admin@example.com", "wrong password", 401),
        ("admin@example.com", PASSWORD, 200),
    ]:
        response = await client.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=origin_headers)
        assert response.status_code == status

    assert len(recorder.threads) >= 4  # dummy hash + three verifications
    assert loop_thread not in recorder.threads


async def test_admin_creation_never_hashes_on_the_event_loop(db_session: AsyncSession, recorder: RecordingHasher) -> None:
    data = AdminCreate.model_validate(
        {"full_name": "Ana Pérez", "email": "admin@example.com", "password": PASSWORD, "password_confirmation": PASSWORD}
    )

    await create_first_administrator(db_session, data, audit_source="cli")

    assert recorder.threads
    assert threading.get_ident() not in recorder.threads
