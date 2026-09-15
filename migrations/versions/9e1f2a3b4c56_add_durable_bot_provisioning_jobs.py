"""Add durable bot provisioning jobs.

Revision ID: 9e1f2a3b4c56
Revises: 8a9b0c1d2e34
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy import Text

revision: str = "9e1f2a3b4c56"
down_revision: str | None = "8a9b0c1d2e34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_provisioning_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("bot_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("token_secret_ref", sa.String(length=255), nullable=False),
        sa.Column("expected_username", sa.String(length=100), nullable=True),
        sa.Column("requested_display_name", sa.String(length=100), nullable=True),
        sa.Column("desired_enabled", sa.Boolean(), nullable=False),
        sa.Column("desired_config", sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "RETRY",
                "READY",
                "FAILED",
                "CANCELLED",
                name="bot_provisioning_status_enum",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_telegram_bot_id", sa.BigInteger(), nullable=True),
        sa.Column("verified_username", sa.String(length=100), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_type", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_bot_provision_tenant_idempotency"),
    )
    op.create_index("ix_bot_provision_status_due", "bot_provisioning_jobs", ["status", "next_attempt_at"], unique=False)
    op.create_index("ix_bot_provision_tenant_status", "bot_provisioning_jobs", ["tenant_id", "status"], unique=False)
    op.create_index(op.f("ix_bot_provisioning_jobs_bot_id"), "bot_provisioning_jobs", ["bot_id"], unique=False)
    op.create_index(op.f("ix_bot_provisioning_jobs_requested_by_user_id"), "bot_provisioning_jobs", ["requested_by_user_id"], unique=False)
    op.create_index(op.f("ix_bot_provisioning_jobs_status"), "bot_provisioning_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_bot_provisioning_jobs_tenant_id"), "bot_provisioning_jobs", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_bot_provisioning_jobs_tenant_id"), table_name="bot_provisioning_jobs")
    op.drop_index(op.f("ix_bot_provisioning_jobs_status"), table_name="bot_provisioning_jobs")
    op.drop_index(op.f("ix_bot_provisioning_jobs_requested_by_user_id"), table_name="bot_provisioning_jobs")
    op.drop_index(op.f("ix_bot_provisioning_jobs_bot_id"), table_name="bot_provisioning_jobs")
    op.drop_index("ix_bot_provision_tenant_status", table_name="bot_provisioning_jobs")
    op.drop_index("ix_bot_provision_status_due", table_name="bot_provisioning_jobs")
    op.drop_table("bot_provisioning_jobs")
