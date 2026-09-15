"""Add singleton system install state for first-run web setup.

Revision ID: a1b2c3d4e5f6
Revises: 9e1f2a3b4c56
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "9e1f2a3b4c56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_install_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("is_initialized", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("initialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO system_install_state (id, is_initialized) VALUES (1, false)"
        )
    )


def downgrade() -> None:
    op.drop_table("system_install_state")
