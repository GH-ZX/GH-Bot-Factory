"""Add tenant provider routing policies.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")

routing_strategy_enum = sa.Enum(
    "PRIORITY",
    "LOWEST_COST",
    "AVAILABILITY",
    "HEALTHIEST",
    "WEIGHTED",
    "MANUAL",
    name="provider_routing_strategy_enum",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "provider_routing_policies",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("product_variant_id", sa.Uuid(), nullable=True),
        sa.Column("strategy", routing_strategy_enum, nullable=False, server_default="PRIORITY"),
        sa.Column("preferred_provider_id", sa.Uuid(), nullable=True),
        sa.Column("failover_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("weights_json", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["product_variant_id"], ["product_variants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["preferred_provider_id"], ["providers.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "product_id",
            "product_variant_id",
            name="uq_provider_routing_variant_scope",
        ),
    )
    op.create_index(
        "ix_provider_routing_policies_tenant_id",
        "provider_routing_policies",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_provider_routing_policies_product_id",
        "provider_routing_policies",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "ix_provider_routing_policies_product_variant_id",
        "provider_routing_policies",
        ["product_variant_id"],
        unique=False,
    )
    op.create_index(
        "ix_provider_routing_policies_preferred_provider_id",
        "provider_routing_policies",
        ["preferred_provider_id"],
        unique=False,
    )
    op.create_index(
        "ix_provider_routing_tenant_product",
        "provider_routing_policies",
        ["tenant_id", "product_id", "product_variant_id"],
        unique=False,
    )
    op.create_index(
        "uq_provider_routing_product_default",
        "provider_routing_policies",
        ["tenant_id", "product_id"],
        unique=True,
        sqlite_where=sa.text("product_variant_id IS NULL"),
        postgresql_where=sa.text("product_variant_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_provider_routing_product_default", table_name="provider_routing_policies")
    op.drop_index("ix_provider_routing_tenant_product", table_name="provider_routing_policies")
    op.drop_index(
        "ix_provider_routing_policies_preferred_provider_id",
        table_name="provider_routing_policies",
    )
    op.drop_index(
        "ix_provider_routing_policies_product_variant_id",
        table_name="provider_routing_policies",
    )
    op.drop_index("ix_provider_routing_policies_product_id", table_name="provider_routing_policies")
    op.drop_index("ix_provider_routing_policies_tenant_id", table_name="provider_routing_policies")
    op.drop_table("provider_routing_policies")
