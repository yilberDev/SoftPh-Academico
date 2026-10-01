"""M-1: Argon2 runs outside the event loop with bounded concurrency."""

import threading
import time
from typing import Any

import anyio
import pytest
from anyio import CapacityLimiter

from app.core import passwords
from app.core.config import get_settings
from app.core.passwords import hash_password, hash_password_async, verify_password, verify_password_async

pytestmark = pytest.mark.anyio

PASSWORD = "una contraseña larga y segura"


class SlowHasher:
    """Stands in for argon2's PasswordHasher: records threads and concurrency."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.threads: set[int] = set()
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def _work(self) -> None:
        with self._lock:
            self.threads.add(threading.get_ident())
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)  # blocking, like Argon2
        with self._lock:
            self.active -= 1

    def verify(self, _hash: str, _password: str) -> bool:
        self._work()
        return True

    def hash(self, _password: str) -> str:
        self._work()
        return "$argon2id$fake"


@pytest.fixture
def slow_hasher(monkeypatch: pytest.MonkeyPatch) -> SlowHasher:
    hasher = SlowHasher(delay=0.1)
    monkeypatch.setattr(passwords, "_hasher", hasher)
    monkeypatch.setattr(passwords, "_limiter", CapacityLimiter(2))
    return hasher


async def test_async_versions_give_the_same_results() -> None:
    hashed = await hash_password_async(PASSWORD)

    assert hashed.startswith("$argon2id$")
    assert await verify_password_async(hashed, PASSWORD) is True
    assert verify_password(hashed, PASSWORD) is True
    assert await verify_password_async(hashed, "otra contraseña") is False
    assert await verify_password_async("not-a-hash", PASSWORD) is False
    assert await verify_password_async(hash_password(PASSWORD), PASSWORD) is True


async def test_argon2_runs_in_a_worker_thread(slow_hasher: SlowHasher) -> None:
    loop_thread = threading.get_ident()

    await verify_password_async("h", "p")
    await hash_password_async("p")

    assert slow_hasher.threads
    assert loop_thread not in slow_hasher.threads


async def test_concurrency_is_capped_by_the_limiter(slow_hasher: SlowHasher) -> None:
    async with anyio.create_task_group() as tg:
        for _ in range(10):
            tg.start_soon(verify_password_async, "h", "p")

    assert slow_hasher.max_active == 2


async def test_event_loop_stays_responsive_during_hashing(slow_hasher: SlowHasher) -> None:
    ticks = 0

    async def ticker(done: anyio.Event) -> None:
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            await anyio.sleep(0.01)

    done = anyio.Event()
    started = time.perf_counter()
    async with anyio.create_task_group() as tg:
        tg.start_soon(ticker, done)
        async with anyio.create_task_group() as work:
            for _ in range(4):
                work.start_soon(verify_password_async, "h", "p")
        done.set()
    elapsed = time.perf_counter() - started

    # 4 jobs of 0.1 s, 2 at a time: ~0.2 s during which the loop keeps ticking.
    assert elapsed >= 0.19
    assert ticks >= 10


async def test_limiter_uses_the_configured_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "_limiter", None)

    assert passwords._argon2_limiter().total_tokens == get_settings().password_hash_max_concurrency == 2


async def test_dummy_hash_is_computed_off_loop_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import auth

    calls: list[Any] = []

    async def fake_hash(password: str) -> str:
        calls.append(password)
        return "$argon2id$dummy"

    monkeypatch.setattr(auth, "_dummy_hash", None)
    monkeypatch.setattr(auth, "hash_password_async", fake_hash)

    assert await auth.dummy_password_hash() == "$argon2id$dummy"
    assert await auth.dummy_password_hash() == "$argon2id$dummy"
    assert len(calls) == 1
