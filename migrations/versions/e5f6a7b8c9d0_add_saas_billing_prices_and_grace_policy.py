"""Add SaaS recurring price catalog and billing grace policy.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tenant_subscriptions") as batch_op:
        batch_op.add_column(sa.Column("grace_ends_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "saas_plan_prices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("external_price_id", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("unit_amount_minor", sa.Integer(), nullable=False),
        sa.Column(
            "interval",
            sa.Enum("MONTH", "YEAR", name="billing_interval_enum", native_enum=False),
            nullable=False,
        ),
        sa.Column("interval_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("interval_count >= 1", name="ck_saas_plan_price_interval_count_positive"),
        sa.CheckConstraint("unit_amount_minor >= 0", name="ck_saas_plan_price_amount_nonnegative"),
        sa.ForeignKeyConstraint(["plan_id"], ["saas_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "external_price_id",
            name="uq_saas_plan_price_provider_external",
        ),
        sa.UniqueConstraint(
            "plan_id",
            "provider",
            "currency",
            "interval",
            "interval_count",
            name="uq_saas_plan_price_slot",
        ),
    )
    op.create_index("ix_saas_plan_prices_plan_id", "saas_plan_prices", ["plan_id"], unique=False)
    op.create_index("ix_saas_plan_prices_provider", "saas_plan_prices", ["provider"], unique=False)
    op.create_index(
        "ix_saas_plan_price_plan_active",
        "saas_plan_prices",
        ["plan_id", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_saas_plan_price_plan_active", table_name="saas_plan_prices")
    op.drop_index("ix_saas_plan_prices_provider", table_name="saas_plan_prices")
    op.drop_index("ix_saas_plan_prices_plan_id", table_name="saas_plan_prices")
    op.drop_table("saas_plan_prices")
    with op.batch_alter_table("tenant_subscriptions") as batch_op:
        batch_op.drop_column("grace_ends_at")
