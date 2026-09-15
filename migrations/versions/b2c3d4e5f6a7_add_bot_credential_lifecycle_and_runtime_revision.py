"""Add bot credential lifecycle and runtime revision fields.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bots", sa.Column("credential_status", sa.String(length=32), server_default="CONFIGURED", nullable=False))
    op.add_column("bots", sa.Column("credential_version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("bots", sa.Column("credential_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bots", sa.Column("credential_rotated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bots", sa.Column("credential_last_error_type", sa.String(length=100), nullable=True))
    op.add_column("bots", sa.Column("runtime_revision", sa.Integer(), server_default="1", nullable=False))
    op.add_column("bots", sa.Column("release_channel", sa.String(length=16), server_default="STABLE", nullable=False))


def downgrade() -> None:
    op.drop_column("bots", "release_channel")
    op.drop_column("bots", "runtime_revision")
    op.drop_column("bots", "credential_last_error_type")
    op.drop_column("bots", "credential_rotated_at")
    op.drop_column("bots", "credential_verified_at")
    op.drop_column("bots", "credential_version")
    op.drop_column("bots", "credential_status")
