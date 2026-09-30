from app.core.passwords import hash_password, verify_password

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
