from datetime import datetime, timedelta

from app.services.login_throttle import (
    ThrottlePolicy,
    ThrottleState,
    identifier_hash,
    lock_remaining,
    register_failure,
)

NOW = datetime(2026, 9, 30, 12, 0, 0)
POLICY = ThrottlePolicy(max_failed_attempts=5, window=timedelta(minutes=15), lockout=timedelta(minutes=15))


def fail(times: int, start: ThrottleState | None = None, step: timedelta = timedelta(seconds=10)) -> ThrottleState | None:
    state, now = start, NOW
    for _ in range(times):
        state = register_failure(state, now, POLICY)
        now += step
    return state


def test_first_failure_starts_a_window() -> None:
    assert register_failure(None, NOW, POLICY) == ThrottleState(1, NOW, None)


def test_four_failures_do_not_lock() -> None:
    state = fail(4)

    assert state is not None
    assert state.failed_attempts == 4
    assert state.locked_until is None


def test_fifth_failure_within_window_locks_for_lockout_period() -> None:
    state = fail(5)

    assert state is not None
    assert state.failed_attempts == 5
    fifth_attempt_at = NOW + 4 * timedelta(seconds=10)
    assert state.locked_until == fifth_attempt_at + POLICY.lockout


def test_sixth_attempt_is_locked() -> None:
    state = fail(5)
    sixth_attempt_at = NOW + timedelta(minutes=1)

    assert lock_remaining(state, sixth_attempt_at) is not None


def test_lock_expires_after_lockout_period() -> None:
    state = fail(5)
    assert state is not None and state.locked_until is not None

    assert lock_remaining(state, state.locked_until) is None
    assert lock_remaining(state, state.locked_until - timedelta(seconds=1)) == timedelta(seconds=1)


def test_failures_outside_window_restart_the_count() -> None:
    state = fail(4)
    later = NOW + POLICY.window + timedelta(seconds=1)

    assert register_failure(state, later, POLICY) == ThrottleState(1, later, None)


def test_failure_after_expired_lock_restarts_the_count() -> None:
    state = fail(5)
    assert state is not None and state.locked_until is not None

    after = state.locked_until + timedelta(seconds=1)
    assert register_failure(state, after, POLICY) == ThrottleState(1, after, None)


def test_no_state_means_no_lock() -> None:
    assert lock_remaining(None, NOW) is None


def test_identifier_is_sha256_of_normalized_email() -> None:
    assert identifier_hash("admin@example.com") == identifier_hash("admin@example.com")
    assert len(identifier_hash("admin@example.com")) == 64
    assert "admin" not in identifier_hash("admin@example.com")
