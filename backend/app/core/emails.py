"""Canonical email form (HU-001, HU-002).

Application canonicalization: strip → NFC → lower (NFC again, because lowering
can produce non-NFC sequences). Every store, lookup and login throttle
identifier uses normalize_email(), and MySQL compares users.email byte by byte
(utf8mb4_0900_bin), so the database and the throttle agree on what "the same
email" is.

The database CHECK constraints (LOWER/TRIM/ASCII) are an extra defense, not a
substitute: MySQL TRIM() only removes U+0020 spaces, unlike Python strip().
"""

import unicodedata


def normalize_email(value: str) -> str:
    stripped = unicodedata.normalize("NFC", value.strip())
    return unicodedata.normalize("NFC", stripped.lower())
