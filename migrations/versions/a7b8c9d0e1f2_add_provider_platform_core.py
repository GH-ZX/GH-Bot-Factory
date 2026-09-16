"""Add provider platform category and connection-health observability.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

provider_category_enum = sa.Enum(
    "NUMBER",
    "ACCOUNT",
    "GIFT",
    "DIGITAL_PRODUCT",
    "SERVICE",
    "OTHER",
    name="provider_category_enum",
    native_enum=False,
)


def upgrade() -> None:
    op.add_column(
        "providers",
        sa.Column(
            "category",
            provider_category_enum,
            nullable=False,
            server_default="DIGITAL_PRODUCT",
        ),
    )
    op.add_column("providers", sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("providers", sa.Column("last_health_latency_ms", sa.Float(), nullable=True))
    op.add_column("providers", sa.Column("last_health_message", sa.String(length=500), nullable=True))
    op.create_index("ix_providers_category", "providers", ["category"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_providers_category", table_name="providers")
    op.drop_column("providers", "last_health_message")
    op.drop_column("providers", "last_health_latency_ms")
    op.drop_column("providers", "last_health_check_at")
    op.drop_column("providers", "category")
