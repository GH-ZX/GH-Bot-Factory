"""Add SaaS plans and tenant subscriptions.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saas_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("entitlements", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_saas_plan_active_public", "saas_plans", ["is_active", "is_public"], unique=False)

    op.create_table(
        "tenant_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "TRIALING",
                "ACTIVE",
                "PAST_DUE",
                "PAUSED",
                "CANCELED",
                name="subscription_status_enum",
                native_enum=False,
            ),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("billing_provider", sa.String(length=50), nullable=True),
        sa.Column("external_customer_id", sa.String(length=255), nullable=True),
        sa.Column("external_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("entitlement_overrides", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("billing_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["plan_id"], ["saas_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_subscription_tenant"),
        sa.UniqueConstraint(
            "billing_provider",
            "external_subscription_id",
            name="uq_tenant_subscription_provider_external_id",
        ),
    )
    op.create_index("ix_tenant_subscriptions_tenant_id", "tenant_subscriptions", ["tenant_id"], unique=False)
    op.create_index("ix_tenant_subscriptions_plan_id", "tenant_subscriptions", ["plan_id"], unique=False)
    op.create_index("ix_tenant_subscriptions_status", "tenant_subscriptions", ["status"], unique=False)
    op.create_index("ix_tenant_subscriptions_external_customer_id", "tenant_subscriptions", ["external_customer_id"], unique=False)
    op.create_index("ix_tenant_subscription_plan_status", "tenant_subscriptions", ["plan_id", "status"], unique=False)
    op.create_index(
        "ix_tenant_subscription_status_period_end",
        "tenant_subscriptions",
        ["status", "current_period_end"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_subscription_status_period_end", table_name="tenant_subscriptions")
    op.drop_index("ix_tenant_subscription_plan_status", table_name="tenant_subscriptions")
    op.drop_index("ix_tenant_subscriptions_external_customer_id", table_name="tenant_subscriptions")
    op.drop_index("ix_tenant_subscriptions_status", table_name="tenant_subscriptions")
    op.drop_index("ix_tenant_subscriptions_plan_id", table_name="tenant_subscriptions")
    op.drop_index("ix_tenant_subscriptions_tenant_id", table_name="tenant_subscriptions")
    op.drop_table("tenant_subscriptions")
    op.drop_index("ix_saas_plan_active_public", table_name="saas_plans")
    op.drop_table("saas_plans")
