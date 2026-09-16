"""Add SaaS provider synchronization observability.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenant_subscriptions", sa.Column("provider_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenant_subscriptions", sa.Column("provider_sync_source", sa.String(length=50), nullable=True))
    op.add_column("tenant_subscriptions", sa.Column("provider_sync_error", sa.String(length=1000), nullable=True))
    op.add_column("tenant_subscriptions", sa.Column("provider_sync_error_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_tenant_subscription_provider_synced_at",
        "tenant_subscriptions",
        ["provider_synced_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_subscription_provider_synced_at", table_name="tenant_subscriptions")
    op.drop_column("tenant_subscriptions", "provider_sync_error_at")
    op.drop_column("tenant_subscriptions", "provider_sync_error")
    op.drop_column("tenant_subscriptions", "provider_sync_source")
    op.drop_column("tenant_subscriptions", "provider_synced_at")
