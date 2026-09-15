"""add wallet topup reversal saga

Revision ID: e91a3b7c4d52
Revises: d84f2a6c9b31
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e91a3b7c4d52"
down_revision: str | Sequence[str] | None = "d84f2a6c9b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wallet_topup_reversals",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("payment_intent_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("original_provider_payment_id", sa.String(length=100), nullable=False),
        sa.Column("provider_refund_id", sa.String(length=100), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "REQUESTED",
                "FUNDS_RESERVED",
                "PROCESSING",
                "RECONCILIATION_REQUIRED",
                "COMPLETED",
                "MANUAL_REVIEW",
                name="wallet_topup_reversal_status_enum",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_detail", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["payment_intent_id"], ["payment_intents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_topup_reversal_tenant_idempotency"),
        sa.UniqueConstraint("tenant_id", "payment_intent_id", name="uq_topup_reversal_tenant_intent"),
    )
    op.create_index("ix_wallet_topup_reversals_tenant_id", "wallet_topup_reversals", ["tenant_id"])
    op.create_index("ix_wallet_topup_reversals_payment_intent_id", "wallet_topup_reversals", ["payment_intent_id"])
    op.create_index("ix_wallet_topup_reversals_user_id", "wallet_topup_reversals", ["user_id"])
    op.create_index("ix_wallet_topup_reversals_wallet_id", "wallet_topup_reversals", ["wallet_id"])
    op.create_index("ix_wallet_topup_reversals_provider_refund_id", "wallet_topup_reversals", ["provider_refund_id"])
    op.create_index("ix_topup_reversal_status_next", "wallet_topup_reversals", ["status", "next_attempt_at"])
    op.create_index("ix_topup_reversal_tenant_status", "wallet_topup_reversals", ["tenant_id", "status"])
    op.create_index(
        "uq_topup_reversal_debit",
        "ledger_transactions",
        ["wallet_id", "reference_type", "reference_id"],
        unique=True,
        postgresql_where=sa.text(
            "transaction_type = 'DEBIT' AND reference_type = 'WALLET_TOPUP_REVERSAL' AND reference_id IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "transaction_type = 'DEBIT' AND reference_type = 'WALLET_TOPUP_REVERSAL' AND reference_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    reversal_count = connection.execute(sa.text("SELECT COUNT(*) FROM wallet_topup_reversals")).scalar_one()
    if reversal_count:
        raise RuntimeError(
            "Cannot downgrade wallet top-up reversal migration while financial reversal records exist. "
            "Export/reconcile financial data before downgrading."
        )
    op.drop_index("uq_topup_reversal_debit", table_name="ledger_transactions")
    op.drop_index("ix_topup_reversal_tenant_status", table_name="wallet_topup_reversals")
    op.drop_index("ix_topup_reversal_status_next", table_name="wallet_topup_reversals")
    op.drop_index("ix_wallet_topup_reversals_provider_refund_id", table_name="wallet_topup_reversals")
    op.drop_index("ix_wallet_topup_reversals_wallet_id", table_name="wallet_topup_reversals")
    op.drop_index("ix_wallet_topup_reversals_user_id", table_name="wallet_topup_reversals")
    op.drop_index("ix_wallet_topup_reversals_payment_intent_id", table_name="wallet_topup_reversals")
    op.drop_index("ix_wallet_topup_reversals_tenant_id", table_name="wallet_topup_reversals")
    op.drop_table("wallet_topup_reversals")
