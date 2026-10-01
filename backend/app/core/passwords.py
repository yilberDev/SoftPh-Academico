from anyio import CapacityLimiter, to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# argon2-cffi defaults: Argon2id with the RFC 9106 low-memory profile.
_hasher = PasswordHasher()

# Created on first use from PASSWORD_HASH_MAX_CONCURRENCY.
_limiter: CapacityLimiter | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was created with different Argon2 parameters (cheap: no hashing)."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def _argon2_limiter() -> CapacityLimiter:
    global _limiter
    if _limiter is None:
        from app.core.config import get_settings

        _limiter = CapacityLimiter(get_settings().password_hash_max_concurrency)
    return _limiter


async def hash_password_async(password: str) -> str:
    """hash_password in a worker thread, so the event loop keeps serving requests.

    The limiter caps simultaneous Argon2 operations and therefore their memory.
    """
    return await to_thread.run_sync(hash_password, password, limiter=_argon2_limiter())


async def verify_password_async(password_hash: str, password: str) -> bool:
    """verify_password in a worker thread (same result, bounded concurrency)."""
    return await to_thread.run_sync(verify_password, password_hash, password, limiter=_argon2_limiter())
