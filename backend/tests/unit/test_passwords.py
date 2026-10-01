from argon2 import PasswordHasher

from app.core.passwords import hash_password, needs_rehash, verify_password

PASSWORD = "una contraseña larga y segura"


def test_hash_uses_argon2id() -> None:
    assert hash_password(PASSWORD).startswith("$argon2id$")


def test_hash_never_contains_the_plain_password() -> None:
    assert PASSWORD not in hash_password(PASSWORD)


def test_same_password_produces_different_hashes() -> None:
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_verify_accepts_correct_password() -> None:
    assert verify_password(hash_password(PASSWORD), PASSWORD) is True


def test_verify_rejects_wrong_password() -> None:
    assert verify_password(hash_password(PASSWORD), "otra contraseña larga") is False


def test_verify_rejects_invalid_hash_without_raising() -> None:
    assert verify_password("not-a-hash", PASSWORD) is False


def test_needs_rehash_is_false_for_current_parameters() -> None:
    assert needs_rehash(hash_password(PASSWORD)) is False


def test_needs_rehash_is_true_for_older_parameters() -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSWORD)

    assert needs_rehash(weak) is True


def test_needs_rehash_ignores_invalid_hash() -> None:
    assert needs_rehash("not-a-hash") is False
