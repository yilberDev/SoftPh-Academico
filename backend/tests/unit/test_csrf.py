import pytest

from app.core.csrf import origin_allowed

ALLOWED = "http://localhost:4321"


@pytest.mark.parametrize(
    ("origin", "referer", "expected"),
    [
        ("http://localhost:4321", None, True),
        ("http://localhost:4321", "https://evil.example/x", True),  # Origin wins
        ("https://evil.example", "http://localhost:4321/login", False),  # Origin wins
        ("http://127.0.0.1:4321", None, False),  # different origin
        ("http://localhost:4321.evil.example", None, False),
        ("null", None, False),
        (None, "http://localhost:4321/login?x=1", True),  # Referer fallback
        (None, "https://evil.example/login", False),
        (None, "not a url", False),
        (None, None, False),  # both missing: rejected
        ("", "", False),
    ],
)
def test_origin_check(origin: str | None, referer: str | None, expected: bool) -> None:
    assert origin_allowed(origin, referer, ALLOWED) is expected


def test_allowed_origin_tolerates_trailing_slash_in_configuration() -> None:
    assert origin_allowed("http://localhost:4321", None, "http://localhost:4321/") is True
