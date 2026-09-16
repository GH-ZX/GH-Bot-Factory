"""Add normalized provider offer snapshots.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_offer_snapshots",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("mapping_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("product_variant_id", sa.Uuid(), nullable=True),
        sa.Column("external_product_id", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("cost_amount", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("cost_currency", sa.String(length=12), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("stock_quantity", sa.Integer(), nullable=True),
        sa.Column("min_quantity", sa.Integer(), nullable=False),
        sa.Column("max_quantity", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["mapping_id"], ["provider_product_mappings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["product_variant_id"], ["product_variants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mapping_id", name="uq_provider_offer_snapshot_mapping"),
    )
    for column in (
        "tenant_id",
        "provider_id",
        "mapping_id",
        "product_id",
        "product_variant_id",
    ):
        op.create_index(
            f"ix_provider_offer_snapshots_{column}",
            "provider_offer_snapshots",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_provider_offer_snapshot_tenant_product",
        "provider_offer_snapshots",
        ["tenant_id", "product_id", "product_variant_id"],
        unique=False,
    )
    op.create_index(
        "ix_provider_offer_snapshot_expires",
        "provider_offer_snapshots",
        ["tenant_id", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_provider_offer_snapshot_expires", table_name="provider_offer_snapshots")
    op.drop_index(
        "ix_provider_offer_snapshot_tenant_product",
        table_name="provider_offer_snapshots",
    )
    for column in reversed(
        ("tenant_id", "provider_id", "mapping_id", "product_id", "product_variant_id")
    ):
        op.drop_index(
            f"ix_provider_offer_snapshots_{column}",
            table_name="provider_offer_snapshots",
        )
    op.drop_table("provider_offer_snapshots")
