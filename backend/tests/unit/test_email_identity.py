"""A-1: canonical email identity (strip → NFC → lower)."""

import pytest

from app.core.emails import normalize_email
from app.services.login_throttle import identifier_hash

CANONICAL = "admin@example.com"

SAME_IDENTITY = [
    "ADMIN@Example.COM",  # case
    "  admin@example.com  ",  # spaces
    "\tadmin@example.com\n",  # tab / newline
    " admin@example.com ",  # NBSP / em space
]

DIFFERENT_IDENTITY = [
    "ádmin@example.com",  # accent
    "admin@exámple.com",  # accent in the domain
    "ａｄｍｉｎ@example.com",  # full-width
    "admın@example.com",  # dotless i
    "ad min@example.com",  # inner space
]


@pytest.mark.parametrize("variant", SAME_IDENTITY)
def test_equivalent_variants_share_the_identity(variant: str) -> None:
    assert normalize_email(variant) == CANONICAL
    assert identifier_hash(variant) == identifier_hash(CANONICAL)


@pytest.mark.parametrize("variant", DIFFERENT_IDENTITY)
def test_non_equivalent_variants_are_different_emails(variant: str) -> None:
    assert normalize_email(variant) != CANONICAL
    assert identifier_hash(variant) != identifier_hash(CANONICAL)


def test_composed_and_decomposed_forms_are_the_same_email() -> None:
    composed = "josé@example.com"  # é as one code point
    decomposed = "josé@example.com"  # e + combining acute accent

    assert composed != decomposed
    assert normalize_email(composed) == normalize_email(decomposed) == composed
    assert identifier_hash(composed) == identifier_hash(decomposed)


def test_accent_is_not_removed() -> None:
    assert normalize_email("JOSÉ@example.com") == "josé@example.com"


@pytest.mark.parametrize(
    "value",
    [*SAME_IDENTITY, *DIFFERENT_IDENTITY, "JOSÉ@X.CO", "İ@x.co", "ẞ@x.co", "Ωmega@x.co", ""],
)
def test_normalization_is_idempotent(value: str) -> None:
    once = normalize_email(value)

    assert normalize_email(once) == once


def test_identifier_is_computed_from_the_value_sent_by_the_client() -> None:
    # identifier_hash canonicalizes itself; callers cannot pass a non-canonical key.
    assert identifier_hash("  ADMIN@example.com") == identifier_hash("admin@example.com")
