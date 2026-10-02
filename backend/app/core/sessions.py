"""Session token primitives (HU-002). Pure functions, no database access.

The cookie holds only the random session token; the database stores only its
SHA-256 hash. The CSRF token is derived from the session token, so nothing
extra is stored.
"""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

SESSION_COOKIE_NAME = "softph_session"
CSRF_HEADER_NAME = "X-CSRF-Token"
SESSION_TOKEN_BYTES = 32  # 256 bits
TOUCH_INTERVAL = timedelta(minutes=1)


def generate_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_token(token: str) -> str:
    # A fast hash is enough: the token has 256 bits of entropy (no brute force),
    # and a deterministic hash allows an indexed lookup.
    return hashlib.sha256(token.encode()).hexdigest()


# generate_session_token(): 32 random bytes as URL-safe base64, 43 characters.
# Also the shape of the one-time tokens sent by email (HU-003 invitation,
# HU-016 password reset), which reuse the same generator.
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


def hash_well_formed_token(token: str) -> str | None:
    """SHA-256 of a well-formed one-time token; None for anything else.

    A malformed token is answered exactly like an unknown one, without a lookup.
    """
    return hash_token(token) if _TOKEN_PATTERN.fullmatch(token) else None


def derive_csrf_token(session_token: str) -> str:
    return hmac.new(session_token.encode(), b"csrf", hashlib.sha256).hexdigest()


def csrf_token_matches(session_token: str, candidate: str | None) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(derive_csrf_token(session_token).encode(), candidate.encode())


def session_cookie_params(*, secure: bool, max_age_seconds: int) -> dict[str, Any]:
    return {
        "key": SESSION_COOKIE_NAME,
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "path": "/",
        "max_age": max_age_seconds,
    }


def is_session_expired(*, expires_at: datetime, last_seen_at: datetime, now: datetime, idle_ttl: timedelta) -> bool:
    return now >= expires_at or now >= last_seen_at + idle_ttl


def should_touch(last_seen_at: datetime, now: datetime) -> bool:
    """Limits last_seen_at writes to one per minute."""
    return now - last_seen_at >= TOUCH_INTERVAL
