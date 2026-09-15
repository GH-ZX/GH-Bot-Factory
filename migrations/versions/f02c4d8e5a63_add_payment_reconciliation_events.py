"""add payment reconciliation events

Revision ID: f02c4d8e5a63
Revises: e91a3b7c4d52
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f02c4d8e5a63"
down_revision: str | Sequence[str] | None = "e91a3b7c4d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_reconciliation_events",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_event_id", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payment_intent_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PROCESSED",
                "MANUAL_REVIEW",
                "IGNORED",
                name="payment_reconciliation_event_status_enum",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("classification", sa.String(length=100), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_intent_id"], ["payment_intents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_event_id",
            "event_type",
            name="uq_payment_reconciliation_provider_event",
        ),
    )
    op.create_index(
        "ix_payment_reconciliation_events_tenant_id",
        "payment_reconciliation_events",
        ["tenant_id"],
    )
    op.create_index(
        "ix_payment_reconciliation_events_payment_intent_id",
        "payment_reconciliation_events",
        ["payment_intent_id"],
    )
    op.create_index(
        "ix_payment_reconciliation_tenant_status",
        "payment_reconciliation_events",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_payment_reconciliation_intent",
        "payment_reconciliation_events",
        ["payment_intent_id"],
    )
    op.create_index(
        "uq_topup_reversal_payment_tx",
        "payment_transactions",
        ["tenant_id", "payment_intent_id", "reference_id"],
        unique=True,
        postgresql_where=sa.text(
            "transaction_type = 'REFUND' AND reference_id LIKE 'topup_reversal:%'"
        ),
        sqlite_where=sa.text(
            "transaction_type = 'REFUND' AND reference_id LIKE 'topup_reversal:%'"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    event_count = connection.execute(sa.text("SELECT COUNT(*) FROM payment_reconciliation_events")).scalar_one()
    if event_count:
        raise RuntimeError(
            "Cannot downgrade payment reconciliation migration while audit events exist. "
            "Export/reconcile the audit trail before downgrading."
        )
    op.drop_index("uq_topup_reversal_payment_tx", table_name="payment_transactions")
    op.drop_index("ix_payment_reconciliation_intent", table_name="payment_reconciliation_events")
    op.drop_index("ix_payment_reconciliation_tenant_status", table_name="payment_reconciliation_events")
    op.drop_index("ix_payment_reconciliation_events_payment_intent_id", table_name="payment_reconciliation_events")
    op.drop_index("ix_payment_reconciliation_events_tenant_id", table_name="payment_reconciliation_events")
    op.drop_table("payment_reconciliation_events")
