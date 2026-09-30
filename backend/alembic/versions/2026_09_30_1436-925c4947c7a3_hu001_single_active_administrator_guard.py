"""hu001 single active administrator guard

Revision ID: 925c4947c7a3
Revises: 633f6e19d008
Create Date: 2026-09-30 14:36:40.355189

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '925c4947c7a3'
down_revision: Union[str, Sequence[str], None] = '633f6e19d008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """HU-001 (D3): the database allows at most one active administrator.

    active_administrator_marker is a virtual generated column: 1 for an active,
    non-deleted administrator and NULL otherwise. UNIQUE accepts many NULLs but
    a single 1. Non-destructive: no data is modified. Creating the UNIQUE
    constraint fails if more than one active administrator already exists;
    MySQL DDL is not transactional, so the column would then remain and the
    duplicates must be resolved before running the upgrade again.
    """
    op.add_column('users', sa.Column('active_administrator_marker', sa.SmallInteger(), sa.Computed("CASE WHEN role = 'ADMINISTRADOR' AND status = 'ACTIVE' AND deleted_at IS NULL THEN 1 ELSE NULL END", persisted=False), nullable=True))
    op.create_unique_constraint(op.f('uq_users_active_administrator_marker'), 'users', ['active_administrator_marker'])


def downgrade() -> None:
    op.drop_constraint(op.f('uq_users_active_administrator_marker'), 'users', type_='unique')
    op.drop_column('users', 'active_administrator_marker')
