import base64
import hashlib
from datetime import datetime, timedelta

import pytest

from app.core.sessions import (
    SESSION_COOKIE_NAME,
    csrf_token_matches,
    derive_csrf_token,
    generate_session_token,
    hash_token,
    is_session_expired,
    session_cookie_params,
    should_touch,
)

NOW = datetime(2026, 9, 30, 12, 0, 0)
IDLE = timedelta(minutes=30)


# --- tokens -----------------------------------------------------------------


def test_token_has_256_bits_and_is_url_safe() -> None:
    token = generate_session_token()

    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert len(raw) == 32
    assert all(c.isalnum() or c in "-_" for c in token)


def test_tokens_are_unique() -> None:
    assert len({generate_session_token() for _ in range(1000)}) == 1000


# --- session hash -------------------------------------------------------------


def test_hash_is_sha256_hex_and_deterministic() -> None:
    token = generate_session_token()

    assert hash_token(token) == hashlib.sha256(token.encode()).hexdigest()
    assert hash_token(token) == hash_token(token)
    assert len(hash_token(token)) == 64


def test_hash_does_not_contain_the_token() -> None:
    token = generate_session_token()

    assert token not in hash_token(token)


# --- CSRF ---------------------------------------------------------------------


def test_csrf_token_is_bound_to_the_session() -> None:
    first, second = generate_session_token(), generate_session_token()

    assert derive_csrf_token(first) == derive_csrf_token(first)
    assert derive_csrf_token(first) != derive_csrf_token(second)
    assert derive_csrf_token(first) != first


@pytest.mark.parametrize("candidate", [None, "", "abc", "ñandú-no-ascii"])
def test_csrf_rejects_missing_or_wrong_token(candidate: str | None) -> None:
    assert csrf_token_matches(generate_session_token(), candidate) is False


def test_csrf_rejects_token_of_another_session() -> None:
    assert csrf_token_matches(generate_session_token(), derive_csrf_token(generate_session_token())) is False


def test_csrf_accepts_matching_token() -> None:
    token = generate_session_token()

    assert csrf_token_matches(token, derive_csrf_token(token)) is True


# --- cookie -------------------------------------------------------------------


@pytest.mark.parametrize("secure", [True, False])
def test_cookie_attributes(secure: bool) -> None:
    params = session_cookie_params(secure=secure, max_age_seconds=28800)

    assert params == {
        "key": SESSION_COOKIE_NAME,
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "path": "/",
        "max_age": 28800,
    }
    assert "domain" not in params


# --- expiration ---------------------------------------------------------------


def test_active_session_is_not_expired() -> None:
    assert not is_session_expired(expires_at=NOW + timedelta(hours=1), last_seen_at=NOW, now=NOW, idle_ttl=IDLE)


def test_absolute_expiration() -> None:
    assert is_session_expired(expires_at=NOW, last_seen_at=NOW, now=NOW, idle_ttl=IDLE)


def test_idle_expiration() -> None:
    assert is_session_expired(
        expires_at=NOW + timedelta(hours=8), last_seen_at=NOW - IDLE, now=NOW, idle_ttl=IDLE
    )


def test_just_before_idle_limit_is_still_valid() -> None:
    assert not is_session_expired(
        expires_at=NOW + timedelta(hours=8), last_seen_at=NOW - IDLE + timedelta(seconds=1), now=NOW, idle_ttl=IDLE
    )


def test_last_seen_is_written_at_most_once_per_minute() -> None:
    assert should_touch(NOW - timedelta(seconds=59), NOW) is False
    assert should_touch(NOW - timedelta(minutes=1), NOW) is True
