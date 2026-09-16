"""Align historical PostgreSQL JSON columns with the JSONB model contract.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

COLUMNS = {
    "billing_events": ("event_metadata",),
    "financial_resolution_cases": ("metadata_json",),
    "payment_reconciliation_events": ("metadata_json",),
    "platform_audit_logs": ("details",),
    "saas_plan_prices": ("metadata_json",),
    "saas_plans": ("entitlements", "metadata_json"),
    "tenant_subscriptions": ("entitlement_overrides", "billing_metadata"),
    "wallet_topup_reversals": ("metadata_json",),
}


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, columns in COLUMNS.items():
        for column in columns:
            op.alter_column(table, column, existing_type=sa.JSON(), type_=postgresql.JSONB(),
                            postgresql_using=f"{column}::jsonb", existing_nullable=False)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, columns in COLUMNS.items():
        for column in columns:
            op.alter_column(table, column, existing_type=postgresql.JSONB(), type_=sa.JSON(),
                            postgresql_using=f"{column}::json", existing_nullable=False)
