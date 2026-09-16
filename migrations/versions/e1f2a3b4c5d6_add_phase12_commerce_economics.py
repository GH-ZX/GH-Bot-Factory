"""Add Phase 12 commerce economics, multi-asset ledger and flexible deposits.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    ]


def upgrade() -> None:
    with op.batch_alter_table("payment_method_configs") as batch:
        batch.add_column(sa.Column("auto_credit_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("auto_credit_target", sa.String(length=32), nullable=False, server_default="ASSET_WALLET"))

    op.create_index(
        "uq_flexible_deposit_settlement",
        "ledger_transactions",
        ["wallet_id", "reference_type", "reference_id"],
        unique=True,
        postgresql_where=sa.text(
            "transaction_type = 'CREDIT' AND reference_type = 'FLEXIBLE_DEPOSIT_SETTLEMENT' AND reference_id IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "transaction_type = 'CREDIT' AND reference_type = 'FLEXIBLE_DEPOSIT_SETTLEMENT' AND reference_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_wallet_hold_capture",
        "ledger_transactions",
        ["wallet_id", "reference_type", "reference_id"],
        unique=True,
        postgresql_where=sa.text(
            "transaction_type = 'DEBIT' AND reference_type = 'WALLET_HOLD_CAPTURE' AND reference_id IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "transaction_type = 'DEBIT' AND reference_type = 'WALLET_HOLD_CAPTURE' AND reference_id IS NOT NULL"
        ),
    )

    op.create_table(
        "asset_wallets",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("asset", sa.String(length=24), nullable=False),
        sa.Column("network", sa.String(length=64), nullable=False, server_default="OFFCHAIN"),
        sa.Column("balance", sa.Numeric(36, 18), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", "asset", "network", name="uq_asset_wallet_identity"),
    )
    op.create_index("ix_asset_wallets_tenant_id", "asset_wallets", ["tenant_id"])
    op.create_index("ix_asset_wallets_user_id", "asset_wallets", ["user_id"])
    op.create_index("ix_asset_wallet_tenant_user", "asset_wallets", ["tenant_id", "user_id"])

    op.create_table(
        "asset_ledger_transactions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=False),
        sa.Column(
            "transaction_type",
            sa.Enum("CREDIT", "DEBIT", "CONVERSION_IN", "CONVERSION_OUT", "ADJUSTMENT", name="asset_ledger_transaction_type_enum", native_enum=False),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(36, 18), nullable=False),
        sa.Column("balance_before", sa.Numeric(36, 18), nullable=False),
        sa.Column("balance_after", sa.Numeric(36, 18), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("reference_type", sa.String(length=64), nullable=True),
        sa.Column("reference_id", sa.String(length=160), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_id"], ["asset_wallets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_asset_ledger_idempotency"),
    )
    op.create_index("ix_asset_ledger_transactions_tenant_id", "asset_ledger_transactions", ["tenant_id"])
    op.create_index("ix_asset_ledger_transactions_wallet_id", "asset_ledger_transactions", ["wallet_id"])
    op.create_index("ix_asset_ledger_transactions_reference_type", "asset_ledger_transactions", ["reference_type"])
    op.create_index("ix_asset_ledger_transactions_reference_id", "asset_ledger_transactions", ["reference_id"])
    op.create_index("ix_asset_ledger_wallet_created", "asset_ledger_transactions", ["wallet_id", "created_at"])
    op.create_index("ix_asset_ledger_tenant_ref", "asset_ledger_transactions", ["tenant_id", "reference_type", "reference_id"])

    op.create_table(
        "wallet_holds",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.Enum("ACTIVE", "CAPTURED", "RELEASED", "EXPIRED", name="wallet_hold_status_enum", native_enum=False), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("reference_type", sa.String(length=64), nullable=False),
        sa.Column("reference_id", sa.String(length=160), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_wallet_hold_idempotency"),
    )
    op.create_index("ix_wallet_holds_tenant_id", "wallet_holds", ["tenant_id"])
    op.create_index("ix_wallet_holds_wallet_id", "wallet_holds", ["wallet_id"])
    op.create_index("ix_wallet_hold_wallet_status", "wallet_holds", ["wallet_id", "status"])
    op.create_index("ix_wallet_hold_expires", "wallet_holds", ["status", "expires_at"])

    op.create_table(
        "fx_policies",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("from_asset", sa.String(length=24), nullable=False),
        sa.Column("from_network", sa.String(length=64), nullable=False, server_default="ANY"),
        sa.Column("to_currency", sa.String(length=12), nullable=False),
        sa.Column("mode", sa.Enum("PARITY", "FIXED_RATE", name="fx_policy_mode_enum", native_enum=False), nullable=False),
        sa.Column("rate", sa.Numeric(36, 18), nullable=False),
        sa.Column("max_auto_credit_amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "from_asset", "from_network", "to_currency", name="uq_fx_policy_scope"),
    )
    op.create_index("ix_fx_policies_tenant_id", "fx_policies", ["tenant_id"])
    op.create_index("ix_fx_policy_tenant_enabled", "fx_policies", ["tenant_id", "is_enabled"])

    op.create_table(
        "flexible_deposit_sessions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("payment_method_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_deposit_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.Enum("CREATED", "PENDING", "PROCESSING", "SETTLED_REVIEW", "CREDITED", "EXPIRED", "CANCELLED", "UNKNOWN", "REVERSED", name="flexible_deposit_status_enum", native_enum=False), nullable=False),
        sa.Column("checkout_url", sa.String(length=2048), nullable=True),
        sa.Column("asset", sa.String(length=24), nullable=True),
        sa.Column("network", sa.String(length=64), nullable=True),
        sa.Column("amount_received", sa.Numeric(36, 18), nullable=True),
        sa.Column("fee_amount", sa.Numeric(36, 18), nullable=True),
        sa.Column("auto_credit_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_credit_target", sa.Enum("ASSET_WALLET", "SETTLEMENT_WALLET", name="auto_credit_target_enum", native_enum=False), nullable=False, server_default="ASSET_WALLET"),
        sa.Column("auto_credit_basis", sa.String(length=32), nullable=False, server_default="NET_AFTER_FEE"),
        sa.Column("settlement_currency", sa.String(length=12), nullable=True),
        sa.Column("fx_policy_id", sa.Uuid(), nullable=True),
        sa.Column("credited_amount", sa.Numeric(36, 18), nullable=True),
        sa.Column("credited_asset", sa.String(length=24), nullable=True),
        sa.Column("credited_currency", sa.String(length=12), nullable=True),
        sa.Column("credited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("provider_snapshot_json", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("policy_snapshot_json", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_method_id"], ["payment_method_configs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fx_policy_id"], ["fx_policies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_flexible_deposit_idempotency"),
        sa.UniqueConstraint("tenant_id", "provider", "provider_deposit_id", name="uq_flexible_deposit_provider_id"),
    )
    for column in ("tenant_id", "user_id", "payment_method_id", "fx_policy_id"):
        op.create_index(f"ix_flexible_deposit_sessions_{column}", "flexible_deposit_sessions", [column])
    op.create_index("ix_flexible_deposit_tenant_user", "flexible_deposit_sessions", ["tenant_id", "user_id", "created_at"])
    op.create_index("ix_flexible_deposit_status", "flexible_deposit_sessions", ["status", "updated_at"])

    op.create_table(
        "provider_balance_snapshots",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("balance", sa.Numeric(36, 18), nullable=True),
        sa.Column("currency", sa.String(length=12), nullable=True),
        sa.Column("low_balance_threshold", sa.Numeric(36, 18), nullable=True),
        sa.Column("is_low_balance", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", name="uq_provider_balance_snapshot_provider"),
    )
    op.create_index("ix_provider_balance_snapshots_tenant_id", "provider_balance_snapshots", ["tenant_id"])
    op.create_index("ix_provider_balance_snapshots_provider_id", "provider_balance_snapshots", ["provider_id"])
    op.create_index("ix_provider_balance_tenant_low", "provider_balance_snapshots", ["tenant_id", "is_low_balance"])

    op.create_table(
        "pricing_tiers",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_pricing_tier_tenant_code"),
    )
    op.create_index("ix_pricing_tiers_tenant_id", "pricing_tiers", ["tenant_id"])
    op.create_index("ix_pricing_tier_tenant_active", "pricing_tiers", ["tenant_id", "is_active"])
    op.create_index(
        "uq_pricing_tier_one_default",
        "pricing_tiers",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true AND is_active = true"),
        sqlite_where=sa.text("is_default = 1 AND is_active = 1"),
    )

    op.create_table(
        "user_pricing_tiers",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tier_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tier_id"], ["pricing_tiers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_user_pricing_tier"),
    )
    op.create_index("ix_user_pricing_tiers_tenant_id", "user_pricing_tiers", ["tenant_id"])
    op.create_index("ix_user_pricing_tiers_user_id", "user_pricing_tiers", ["user_id"])
    op.create_index("ix_user_pricing_tiers_tier_id", "user_pricing_tiers", ["tier_id"])
    op.create_index("ix_user_pricing_tier_tenant", "user_pricing_tiers", ["tenant_id", "tier_id"])

    op.create_table(
        "pricing_rules",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("scope", sa.Enum("GLOBAL", "CATEGORY", "PRODUCT", "VARIANT", name="pricing_scope_enum", native_enum=False), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("product_variant_id", sa.Uuid(), nullable=True),
        sa.Column("tier_id", sa.Uuid(), nullable=True),
        sa.Column("markup_mode", sa.Enum("FIXED", "PERCENT", "MIXED", name="markup_mode_enum", native_enum=False), nullable=False),
        sa.Column("markup_percent", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("markup_fixed", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("minimum_margin", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("rounding_increment", sa.Numeric(18, 6), nullable=False, server_default="0.01"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tier_id"], ["pricing_tiers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("tenant_id", "category_id", "product_id", "product_variant_id", "tier_id"):
        op.create_index(f"ix_pricing_rules_{column}", "pricing_rules", [column])
    op.create_index("ix_pricing_rule_tenant_active", "pricing_rules", ["tenant_id", "is_active", "priority"])
    op.create_index("ix_pricing_rule_variant", "pricing_rules", ["tenant_id", "product_variant_id", "tier_id"])

    op.create_table(
        "commerce_price_quotes",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("product_variant_id", sa.Uuid(), nullable=False),
        sa.Column("provider_offer_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("pricing_rule_id", sa.Uuid(), nullable=True),
        sa.Column("pricing_tier_id", sa.Uuid(), nullable=True),
        sa.Column("supplier_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("supplier_currency", sa.String(length=12), nullable=True),
        sa.Column("sell_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("quote_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_offer_snapshot_id"], ["provider_offer_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pricing_rule_id"], ["pricing_rules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pricing_tier_id"], ["pricing_tiers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "quote_fingerprint", name="uq_commerce_price_quote_fingerprint"),
    )
    for column in ("tenant_id", "user_id", "product_variant_id", "provider_offer_snapshot_id", "pricing_rule_id", "pricing_tier_id"):
        op.create_index(f"ix_commerce_price_quotes_{column}", "commerce_price_quotes", [column])
    op.create_index("ix_commerce_price_quote_user_variant", "commerce_price_quotes", ["tenant_id", "user_id", "product_variant_id"])
    op.create_index("ix_commerce_price_quote_expires", "commerce_price_quotes", ["tenant_id", "expires_at"])

    op.create_table(
        "order_item_economics",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("order_item_id", sa.Uuid(), nullable=False),
        sa.Column("bot_id", sa.Uuid(), nullable=True),
        sa.Column("price_quote_id", sa.Uuid(), nullable=True),
        sa.Column("pricing_tier_id", sa.Uuid(), nullable=True),
        sa.Column("pricing_rule_id", sa.Uuid(), nullable=True),
        sa.Column("provider_id", sa.Uuid(), nullable=True),
        sa.Column("sale_amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("sale_currency", sa.String(length=12), nullable=False),
        sa.Column("estimated_supplier_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("estimated_cost_currency", sa.String(length=12), nullable=True),
        sa.Column("actual_supplier_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("actual_cost_currency", sa.String(length=12), nullable=True),
        sa.Column("gross_profit", sa.Numeric(18, 6), nullable=True),
        sa.Column("attributed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["price_quote_id"], ["commerce_price_quotes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pricing_tier_id"], ["pricing_tiers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pricing_rule_id"], ["pricing_rules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_item_id", name="uq_order_item_economics_item"),
    )
    for column in ("tenant_id", "order_id", "order_item_id", "bot_id", "price_quote_id", "pricing_tier_id", "pricing_rule_id", "provider_id"):
        op.create_index(f"ix_order_item_economics_{column}", "order_item_economics", [column])
    op.create_index("ix_order_economics_tenant_provider", "order_item_economics", ["tenant_id", "provider_id"])
    op.create_index("ix_order_economics_bot", "order_item_economics", ["tenant_id", "bot_id"])


def downgrade() -> None:
    connection = op.get_bind()
    protected_tables = (
        "asset_ledger_transactions",
        "wallet_holds",
        "flexible_deposit_sessions",
        "commerce_price_quotes",
        "order_item_economics",
        "pricing_rules",
        "user_pricing_tiers",
        "pricing_tiers",
        "provider_balance_snapshots",
        "fx_policies",
    )
    populated = []
    for table in protected_tables:
        count = connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        if count:
            populated.append(f"{table}={count}")
    if populated:
        raise RuntimeError(
            "Cannot downgrade Phase 12 while commerce/financial evidence exists: " + ", ".join(populated)
        )

    op.drop_table("order_item_economics")
    op.drop_table("commerce_price_quotes")
    op.drop_table("pricing_rules")
    op.drop_table("user_pricing_tiers")
    op.drop_table("pricing_tiers")
    op.drop_table("provider_balance_snapshots")
    op.drop_table("flexible_deposit_sessions")
    op.drop_table("fx_policies")
    op.drop_table("wallet_holds")
    op.drop_table("asset_ledger_transactions")
    op.drop_table("asset_wallets")
    op.drop_index("uq_wallet_hold_capture", table_name="ledger_transactions")
    op.drop_index("uq_flexible_deposit_settlement", table_name="ledger_transactions")
    with op.batch_alter_table("payment_method_configs") as batch:
        batch.drop_column("auto_credit_target")
        batch.drop_column("auto_credit_enabled")
