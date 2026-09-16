"""Add platform control-plane audit and durable billing events.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False, server_default="LOCAL_PLATFORM_TOKEN"),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_audit_logs_tenant_id", "platform_audit_logs", ["tenant_id"], unique=False)
    op.create_index(
        "ix_platform_audit_action_created",
        "platform_audit_logs",
        ["action", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_platform_audit_tenant_created",
        "platform_audit_logs",
        ["tenant_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "billing_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED",
                "APPLIED",
                "IGNORED",
                "FAILED",
                name="billing_event_status_enum",
                native_enum=False,
            ),
            nullable=False,
            server_default="RECEIVED",
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["subscription_id"], ["tenant_subscriptions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "external_event_id", name="uq_billing_event_provider_external"),
    )
    op.create_index("ix_billing_events_provider", "billing_events", ["provider"], unique=False)
    op.create_index("ix_billing_events_event_type", "billing_events", ["event_type"], unique=False)
    op.create_index("ix_billing_events_status", "billing_events", ["status"], unique=False)
    op.create_index("ix_billing_events_tenant_id", "billing_events", ["tenant_id"], unique=False)
    op.create_index("ix_billing_events_subscription_id", "billing_events", ["subscription_id"], unique=False)
    op.create_index(
        "ix_billing_event_status_created",
        "billing_events",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_billing_event_tenant_created",
        "billing_events",
        ["tenant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_billing_event_tenant_created", table_name="billing_events")
    op.drop_index("ix_billing_event_status_created", table_name="billing_events")
    op.drop_index("ix_billing_events_subscription_id", table_name="billing_events")
    op.drop_index("ix_billing_events_tenant_id", table_name="billing_events")
    op.drop_index("ix_billing_events_status", table_name="billing_events")
    op.drop_index("ix_billing_events_event_type", table_name="billing_events")
    op.drop_index("ix_billing_events_provider", table_name="billing_events")
    op.drop_table("billing_events")

    op.drop_index("ix_platform_audit_tenant_created", table_name="platform_audit_logs")
    op.drop_index("ix_platform_audit_action_created", table_name="platform_audit_logs")
    op.drop_index("ix_platform_audit_logs_tenant_id", table_name="platform_audit_logs")
    op.drop_table("platform_audit_logs")
