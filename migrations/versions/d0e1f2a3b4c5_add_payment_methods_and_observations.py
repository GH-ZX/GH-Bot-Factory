"""Add payment methods and immutable payment observations.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "payment_method_configs",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column(
            "method_type",
            sa.Enum(
                "REGULATED_PROVIDER",
                "CRYPTO_GATEWAY",
                "SELF_CUSTODY",
                "MANUAL_TRANSFER",
                "EXCHANGE_TRANSFER",
                name="payment_method_type_enum",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "verification_mode",
            sa.Enum(
                "PROVIDER_RECONCILIATION",
                "ONCHAIN",
                "MANUAL",
                "HYBRID",
                name="payment_verification_mode_enum",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("provider_name", sa.String(length=50), nullable=True),
        sa.Column("asset", sa.String(length=24), nullable=True),
        sa.Column("network", sa.String(length=64), nullable=True),
        sa.Column("destination_address", sa.String(length=255), nullable=True),
        sa.Column("destination_memo", sa.String(length=255), nullable=True),
        sa.Column("instructions", sa.String(length=2000), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("requires_admin_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("settings_json", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_payment_method_tenant_code"),
    )
    op.create_index("ix_payment_method_configs_tenant_id", "payment_method_configs", ["tenant_id"])
    op.create_index(
        "ix_payment_method_tenant_enabled",
        "payment_method_configs",
        ["tenant_id", "is_enabled"],
    )
    op.create_index(
        "ix_payment_method_tenant_type",
        "payment_method_configs",
        ["tenant_id", "method_type"],
    )

    with op.batch_alter_table("payment_intents") as batch_op:
        batch_op.add_column(sa.Column("payment_method_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_payment_intents_payment_method_id",
            "payment_method_configs",
            ["payment_method_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_payment_intents_payment_method_id",
            ["payment_method_id"],
            unique=False,
        )

    op.create_table(
        "payment_quotes",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("payment_intent_id", sa.Uuid(), nullable=False),
        sa.Column("payment_method_id", sa.Uuid(), nullable=False),
        sa.Column("settlement_amount", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("settlement_currency", sa.String(length=12), nullable=False),
        sa.Column("asset_amount", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("asset", sa.String(length=24), nullable=False),
        sa.Column("network", sa.String(length=64), nullable=False),
        sa.Column("rate", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("rate_source", sa.String(length=100), nullable=False),
        sa.Column("quote_reference", sa.String(length=160), nullable=True),
        sa.Column("quote_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_intent_id"], ["payment_intents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_method_id"], ["payment_method_configs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "quote_fingerprint",
            name="uq_payment_quote_tenant_fingerprint",
        ),
    )
    op.create_index("ix_payment_quotes_tenant_id", "payment_quotes", ["tenant_id"])
    op.create_index("ix_payment_quotes_payment_intent_id", "payment_quotes", ["payment_intent_id"])
    op.create_index("ix_payment_quotes_payment_method_id", "payment_quotes", ["payment_method_id"])
    op.create_index(
        "ix_payment_quote_intent_created",
        "payment_quotes",
        ["payment_intent_id", "created_at"],
    )
    op.create_index(
        "ix_payment_quote_tenant_expires",
        "payment_quotes",
        ["tenant_id", "expires_at"],
    )

    op.create_table(
        "payment_observations",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("payment_intent_id", sa.Uuid(), nullable=False),
        sa.Column("payment_method_id", sa.Uuid(), nullable=False),
        sa.Column("payment_quote_id", sa.Uuid(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "PROVIDER",
                "ONCHAIN",
                "MANUAL",
                "EXCHANGE",
                name="payment_observation_source_enum",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "SUBMITTED",
                "PENDING_VERIFICATION",
                "MANUAL_REVIEW",
                "VERIFIED",
                "REJECTED",
                name="payment_observation_status_enum",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "assurance_level",
            sa.Enum(
                "REGULATED_RECONCILED",
                "ONCHAIN_VERIFIED",
                "GATEWAY_VERIFIED",
                "MANUAL_APPROVED",
                name="payment_assurance_level_enum",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("external_reference", sa.String(length=255), nullable=True),
        sa.Column("asset", sa.String(length=24), nullable=True),
        sa.Column("network", sa.String(length=64), nullable=True),
        sa.Column("destination_address", sa.String(length=255), nullable=True),
        sa.Column("asset_amount", sa.Numeric(precision=36, scale=18), nullable=True),
        sa.Column("settlement_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("settlement_currency", sa.String(length=12), nullable=True),
        sa.Column("confirmations", sa.Integer(), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("details_json", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_intent_id"], ["payment_intents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_method_id"], ["payment_method_configs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_quote_id"], ["payment_quotes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["verified_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "evidence_fingerprint",
            name="uq_payment_observation_tenant_fingerprint",
        ),
    )
    for column in (
        "tenant_id",
        "payment_intent_id",
        "payment_method_id",
        "payment_quote_id",
        "external_reference",
        "verified_by_user_id",
    ):
        op.create_index(
            f"ix_payment_observations_{column}",
            "payment_observations",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_payment_observation_tenant_status",
        "payment_observations",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_payment_observation_intent",
        "payment_observations",
        ["payment_intent_id"],
    )
    op.create_index(
        "ix_payment_observation_method",
        "payment_observations",
        ["payment_method_id"],
    )
    op.create_index(
        "uq_payment_observation_chain_reference",
        "payment_observations",
        ["network", "external_reference"],
        unique=True,
        postgresql_where=sa.text(
            "source = 'ONCHAIN' AND network IS NOT NULL AND external_reference IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "source = 'ONCHAIN' AND network IS NOT NULL AND external_reference IS NOT NULL"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    observation_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM payment_observations")
    ).scalar_one()
    quote_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM payment_quotes")
    ).scalar_one()
    method_reference_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM payment_intents WHERE payment_method_id IS NOT NULL")
    ).scalar_one()
    if observation_count or quote_count or method_reference_count:
        raise RuntimeError(
            "Cannot downgrade Phase 11 payment evidence schema while financial evidence or "
            "payment-method references exist. Export and reconcile financial records first."
        )

    op.drop_index("uq_payment_observation_chain_reference", table_name="payment_observations")
    op.drop_index("ix_payment_observation_method", table_name="payment_observations")
    op.drop_index("ix_payment_observation_intent", table_name="payment_observations")
    op.drop_index("ix_payment_observation_tenant_status", table_name="payment_observations")
    for column in reversed(
        (
            "tenant_id",
            "payment_intent_id",
            "payment_method_id",
            "payment_quote_id",
            "external_reference",
            "verified_by_user_id",
        )
    ):
        op.drop_index(
            f"ix_payment_observations_{column}", table_name="payment_observations"
        )
    op.drop_table("payment_observations")

    op.drop_index("ix_payment_quote_tenant_expires", table_name="payment_quotes")
    op.drop_index("ix_payment_quote_intent_created", table_name="payment_quotes")
    op.drop_index("ix_payment_quotes_payment_method_id", table_name="payment_quotes")
    op.drop_index("ix_payment_quotes_payment_intent_id", table_name="payment_quotes")
    op.drop_index("ix_payment_quotes_tenant_id", table_name="payment_quotes")
    op.drop_table("payment_quotes")

    with op.batch_alter_table("payment_intents") as batch_op:
        batch_op.drop_index("ix_payment_intents_payment_method_id")
        batch_op.drop_constraint("fk_payment_intents_payment_method_id", type_="foreignkey")
        batch_op.drop_column("payment_method_id")

    op.drop_index("ix_payment_method_tenant_type", table_name="payment_method_configs")
    op.drop_index("ix_payment_method_tenant_enabled", table_name="payment_method_configs")
    op.drop_index("ix_payment_method_configs_tenant_id", table_name="payment_method_configs")
    op.drop_table("payment_method_configs")
