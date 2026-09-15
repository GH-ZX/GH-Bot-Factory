"""add analytics query indexes

Revision ID: 7b8c9d0e1f23
Revises: 5a6e7f8b9c10
Create Date: 2026-09-15 14:25:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7b8c9d0e1f23"
down_revision: str | None = "5a6e7f8b9c10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_order_tenant_created_status",
        "orders",
        ["tenant_id", "created_at", "status"],
    )
    op.create_index(
        "ix_audit_tenant_created_at",
        "audit_logs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_payment_intent_tenant_created_purpose_status",
        "payment_intents",
        ["tenant_id", "created_at", "purpose", "status"],
    )
    op.create_index(
        "ix_topup_reversal_tenant_completed_status",
        "wallet_topup_reversals",
        ["tenant_id", "completed_at", "status"],
    )
    op.create_index(
        "ix_fulfillment_tenant_started_provider_status",
        "fulfillment_attempts",
        ["tenant_id", "started_at", "provider_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_fulfillment_tenant_started_provider_status",
        table_name="fulfillment_attempts",
    )
    op.drop_index(
        "ix_topup_reversal_tenant_completed_status",
        table_name="wallet_topup_reversals",
    )
    op.drop_index(
        "ix_payment_intent_tenant_created_purpose_status",
        table_name="payment_intents",
    )
    op.drop_index("ix_audit_tenant_created_at", table_name="audit_logs")
    op.drop_index("ix_order_tenant_created_status", table_name="orders")
