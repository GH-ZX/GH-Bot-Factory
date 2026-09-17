"""link commercial quotes to tenants

Revision ID: c3d4e5f6a8b9
Revises: b2c3d4e5f6a8
Create Date: 2026-09-18 02:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a8b9"
down_revision: str | None = "b2c3d4e5f6a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("commercial_quotes", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_commercial_quotes_tenant_id"),
        "commercial_quotes",
        ["tenant_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_commercial_quotes_tenant_id_tenants"),
        "commercial_quotes",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_commercial_quotes_tenant_id_tenants"),
        "commercial_quotes",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_commercial_quotes_tenant_id"), table_name="commercial_quotes")
    op.drop_column("commercial_quotes", "tenant_id")
