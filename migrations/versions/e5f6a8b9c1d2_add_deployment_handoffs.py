"""add deployment handoffs table

Revision ID: e5f6a8b9c1d2
Revises: d4e5f6a8b9c1
Create Date: 2026-09-18 02:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a8b9c1d2"
down_revision: str | None = "d4e5f6a8b9c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployment_handoffs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=True),
        sa.Column(
            "license_type",
            sa.Enum("MANAGED", "DEDICATED_DEPLOYMENT", "SOURCE_LICENSE", name="licensetype", native_enum=False, length=30),
            nullable=False,
            server_default="DEDICATED_DEPLOYMENT",
        ),
        sa.Column("license_key", sa.String(length=80), nullable=False),
        sa.Column("licensed_to", sa.String(length=120), nullable=False),
        sa.Column("licensed_domain", sa.String(length=120), nullable=True),
        sa.Column("version_tag", sa.String(length=40), nullable=False, server_default="v0.1.0-phase14.5"),
        sa.Column(
            "status",
            sa.Enum("PREPARING", "READY_FOR_EXPORT", "EXPORTED", "HANDED_OFF", "CANCELLED", name="handoffstatus", native_enum=False, length=25),
            nullable=False,
            server_default="PREPARING",
        ),
        sa.Column("support_plan", sa.String(length=60), nullable=True),
        sa.Column("runtime_deactivated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("runtime_deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("export_checksum", sa.String(length=64), nullable=True),
        sa.Column("export_artifact_path", sa.String(length=255), nullable=True),
        sa.Column("handoff_notes", sa.Text(), nullable=True),
        sa.Column("handed_off_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_deployment_handoffs_tenant_id_tenants"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["quote_id"], ["commercial_quotes.id"], name=op.f("fk_deployment_handoffs_quote_id_commercial_quotes"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployment_handoffs")),
        sa.UniqueConstraint("license_key", name=op.f("uq_deployment_handoffs_license_key")),
    )
    op.create_index(
        "ix_deployment_handoffs_license_key",
        "deployment_handoffs",
        ["license_key"],
        unique=True,
    )
    op.create_index(
        "ix_deployment_handoffs_tenant_status",
        "deployment_handoffs",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_deployment_handoffs_quote",
        "deployment_handoffs",
        ["quote_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_handoffs_quote", table_name="deployment_handoffs")
    op.drop_index("ix_deployment_handoffs_tenant_status", table_name="deployment_handoffs")
    op.drop_index("ix_deployment_handoffs_license_key", table_name="deployment_handoffs")
    op.drop_table("deployment_handoffs")
