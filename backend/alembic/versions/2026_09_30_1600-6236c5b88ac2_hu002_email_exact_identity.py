"""hu002 email exact identity

Revision ID: 6236c5b88ac2
Revises: 5cde3a1e780c
Create Date: 2026-09-30 16:00:12.179565

Security fix A-1 (HU-002): users.email was compared with utf8mb4_0900_ai_ci,
so accent/width variants matched the same user while the login throttle
counted them as different emails. The column now compares byte by byte
(utf8mb4_0900_bin, NO PAD) and two CHECK constraints add an extra defense for
the canonical form (the application rule is normalize_email()).

Both directions validate everything first and then apply a single
ALTER TABLE statement, which InnoDB executes atomically: a failed check
changes nothing, and there is no partially applied state between statements.
"""
import unicodedata
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6236c5b88ac2'
down_revision: Union[str, Sequence[str], None] = '5cde3a1e780c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EXACT_COLLATION = "utf8mb4_0900_bin"
PREVIOUS_COLLATION = "utf8mb4_0900_ai_ci"
CHECK_CANONICAL = "ck_users_email_canonical"
CHECK_ASCII = "ck_users_email_ascii"


def _canonical(value: str) -> str:
    # Frozen copy of app.core.emails.normalize_email at this revision.
    stripped = unicodedata.normalize("NFC", value.strip())
    return unicodedata.normalize("NFC", stripped.lower())


def _email_collation(bind: sa.Connection) -> str | None:
    return bind.execute(
        sa.text(
            "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'email'"
        )
    ).scalar()


def _existing_checks(bind: sa.Connection) -> set[str]:
    return set(
        bind.execute(
            sa.text(
                "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND CONSTRAINT_TYPE = 'CHECK' "
                "AND CONSTRAINT_NAME IN (:canonical, :ascii)"
            ),
            {"canonical": CHECK_CANONICAL, "ascii": CHECK_ASCII},
        ).scalars()
    )


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Validate (no structural change yet).
    collation = _email_collation(bind)
    if collation != PREVIOUS_COLLATION:
        raise RuntimeError(f"users.email has collation {collation!r}; expected {PREVIOUS_COLLATION!r}. Nothing was changed.")
    if _existing_checks(bind):
        raise RuntimeError("The email CHECK constraints already exist. Nothing was changed.")
    invalid_ids = [
        row.id
        for row in bind.execute(sa.text("SELECT id, email FROM users"))
        if row.email != _canonical(row.email) or not row.email.isascii()
    ]
    if invalid_ids:
        raise RuntimeError(
            "Emails that are not canonical (strip + NFC + lower) or not ASCII in users.id "
            f"{invalid_ids}. Fix them manually before upgrading; they are not rewritten "
            "automatically because that could merge identities. Nothing was changed."
        )

    # 2. One atomic statement. Stricter comparison cannot create UNIQUE duplicates.
    op.execute(
        f"ALTER TABLE users "
        f"MODIFY email VARCHAR(254) CHARACTER SET utf8mb4 COLLATE {EXACT_COLLATION} NOT NULL, "
        f"ADD CONSTRAINT {CHECK_CANONICAL} CHECK (email = LOWER(email) AND email = TRIM(email)), "
        f"ADD CONSTRAINT {CHECK_ASCII} CHECK (CHAR_LENGTH(email) = LENGTH(email))"
    )


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Emails that the previous (accent- and case-insensitive) collation
    #    would treat as duplicates would break uq_users_email.
    duplicates = bind.execute(
        sa.text(
            f"SELECT GROUP_CONCAT(id ORDER BY id) FROM users "
            f"GROUP BY email COLLATE {PREVIOUS_COLLATION} HAVING COUNT(*) > 1"
        )
    ).scalars().all()
    if duplicates:
        raise RuntimeError(
            f"Downgrade aborted: users.id groups {duplicates} would be duplicates under "
            f"{PREVIOUS_COLLATION} and break uq_users_email. Nothing was changed."
        )

    # 2. The structure must be the one this revision created.
    collation = _email_collation(bind)
    if collation != EXACT_COLLATION:
        raise RuntimeError(f"users.email has collation {collation!r}; expected {EXACT_COLLATION!r}. Nothing was changed.")
    missing = {CHECK_CANONICAL, CHECK_ASCII} - _existing_checks(bind)
    if missing:
        raise RuntimeError(f"Downgrade aborted: CHECK constraints {sorted(missing)} are missing. Nothing was changed.")

    # 3. Only now change the structure, in one atomic statement: drop the
    #    CHECK constraints and restore the previous collation together.
    op.execute(
        f"ALTER TABLE users "
        f"DROP CHECK {CHECK_CANONICAL}, "
        f"DROP CHECK {CHECK_ASCII}, "
        f"MODIFY email VARCHAR(254) CHARACTER SET utf8mb4 COLLATE {PREVIOUS_COLLATION} NOT NULL"
    )
