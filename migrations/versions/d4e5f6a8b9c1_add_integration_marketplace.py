"""add integration marketplace tables

Revision ID: d4e5f6a8b9c1
Revises: c3d4e5f6a8b9
Create Date: 2026-09-18 02:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a8b9c1"
down_revision: str | None = "c3d4e5f6a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "integration_offerings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False, server_default="OTHER"),
        sa.Column("adapter_key", sa.String(length=60), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "lifecycle",
            sa.Enum("DRAFT", "SANDBOX_REVIEW", "ACTIVE", "DEPRECATED", "RETIRED", name="integrationlifecycle", native_enum=False, length=20),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("setup_fee", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("monthly_fee", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("required_credentials", json_type, nullable=False),
        sa.Column("supported_templates", json_type, nullable=False),
        sa.Column("features", json_type, nullable=False),
        sa.Column("requirements", json_type, nullable=False),
        sa.Column("docs_url", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_offerings")),
        sa.UniqueConstraint("key", name=op.f("uq_integration_offerings_key")),
    )
    op.create_index(
        "ix_integration_offerings_key",
        "integration_offerings",
        ["key"],
        unique=True,
    )
    op.create_index(
        "ix_integration_offerings_category_lifecycle",
        "integration_offerings",
        ["category", "lifecycle"],
        unique=False,
    )

    op.create_table(
        "tenant_integration_entitlements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("integration_key", sa.String(length=50), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("granted_by", sa.String(length=40), nullable=False, server_default="OPERATOR"),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_integration_entitlements_tenant_id_tenants"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_integration_entitlements")),
    )
    op.create_index(
        "ix_tenant_integration_entitlements_unique",
        "tenant_integration_entitlements",
        ["tenant_id", "integration_key"],
        unique=True,
    )
    op.create_index(
        "ix_tenant_integration_entitlements_tenant",
        "tenant_integration_entitlements",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_integration_entitlements_tenant", table_name="tenant_integration_entitlements")
    op.drop_index("ix_tenant_integration_entitlements_unique", table_name="tenant_integration_entitlements")
    op.drop_table("tenant_integration_entitlements")
    op.drop_index("ix_integration_offerings_category_lifecycle", table_name="integration_offerings")
    op.drop_index("ix_integration_offerings_key", table_name="integration_offerings")
    op.drop_table("integration_offerings")
