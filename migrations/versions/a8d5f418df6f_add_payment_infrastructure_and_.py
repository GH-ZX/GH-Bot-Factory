"""add_payment_infrastructure_and_settlement_idempotency

Revision ID: a8d5f418df6f
Revises: 867840fa9063
Create Date: 2026-09-15 09:11:51.295286

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a8d5f418df6f'
down_revision: str | Sequence[str] | None = '867840fa9063'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # 1. Check for legacy duplicate settlements safely before applying unique index
    dup_query = sa.text("""
        SELECT wallet_id, reference_type, reference_id, COUNT(*) as cnt
        FROM ledger_transactions
        WHERE transaction_type = 'CREDIT'
          AND reference_type = 'PAYMENT_SETTLEMENT'
          AND reference_id IS NOT NULL
        GROUP BY wallet_id, reference_type, reference_id
        HAVING COUNT(*) > 1
    """)
    duplicates = bind.execute(dup_query).fetchall()
    if duplicates:
        details = [
            f"wallet={row[0]}, ref_type={row[1]}, ref_id={row[2]}, count={row[3]}"
            for row in duplicates
        ]
        raise RuntimeError(
            f"Cannot apply migration {revision}: found {len(duplicates)} duplicate settlement group(s) "
            f"in ledger_transactions: {'; '.join(details)}. "
            "A unique constraint cannot be created without violating database integrity. "
            "Manual accounting audit and reconciliation is required. No financial records were deleted."
        )

    with op.batch_alter_table('ledger_transactions', schema=None) as batch_op:
        batch_op.create_index(
            'uq_settlement_idempotency',
            ['wallet_id', 'reference_type', 'reference_id'],
            unique=True,
            postgresql_where=sa.text("transaction_type = 'CREDIT' AND reference_type = 'PAYMENT_SETTLEMENT' AND reference_id IS NOT NULL"),
            sqlite_where=sa.text("transaction_type = 'CREDIT' AND reference_type = 'PAYMENT_SETTLEMENT' AND reference_id IS NOT NULL"),
        )

    # 2. Create payment_intents table
    op.create_table(
        'payment_intents',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('order_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('provider_payment_id', sa.String(length=100), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'CREATED', 'PENDING', 'PROCESSING', 'SUCCEEDED', 'FAILED', 'EXPIRED', 'UNKNOWN', 'CANCELLED',
                name='payment_intent_status_enum',
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('idempotency_key', sa.String(length=100), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata_json', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'idempotency_key', name='uq_payment_intent_tenant_idempotency'),
    )
    with op.batch_alter_table('payment_intents', schema=None) as batch_op:
        batch_op.create_index('ix_payment_intents_id', ['id'], unique=False)
        batch_op.create_index('ix_payment_intents_order_id', ['order_id'], unique=False)
        batch_op.create_index('ix_payment_intents_provider_payment_id', ['provider_payment_id'], unique=False)
        batch_op.create_index('ix_payment_intents_tenant_id', ['tenant_id'], unique=False)
        batch_op.create_index('ix_payment_intents_user_id', ['user_id'], unique=False)
        batch_op.create_index('ix_payment_intent_tenant_status', ['tenant_id', 'status'], unique=False)
        batch_op.create_index('ix_payment_intent_tenant_order', ['tenant_id', 'order_id'], unique=False)
        batch_op.create_index('ix_payment_intent_provider_id', ['provider', 'provider_payment_id'], unique=False)
        batch_op.create_index(
            'uq_active_order_payment_intent',
            ['tenant_id', 'order_id'],
            unique=True,
            postgresql_where=sa.text("status IN ('CREATED', 'PENDING', 'PROCESSING')"),
            sqlite_where=sa.text("status IN ('CREATED', 'PENDING', 'PROCESSING')"),
        )

    # 3. Create payment_transactions table
    op.create_table(
        'payment_transactions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('payment_intent_id', sa.Uuid(), nullable=False),
        sa.Column(
            'transaction_type',
            sa.Enum(
                'AUTHORIZATION', 'CAPTURE', 'SETTLEMENT', 'REFUND', 'VOID',
                name='payment_tx_type_enum',
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('provider_tx_id', sa.String(length=100), nullable=True),
        sa.Column('reference_id', sa.String(length=100), nullable=True),
        sa.Column('metadata_json', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['payment_intent_id'], ['payment_intents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('payment_transactions', schema=None) as batch_op:
        batch_op.create_index('ix_payment_transactions_id', ['id'], unique=False)
        batch_op.create_index('ix_payment_transactions_tenant_id', ['tenant_id'], unique=False)
        batch_op.create_index('ix_payment_transactions_payment_intent_id', ['payment_intent_id'], unique=False)
        batch_op.create_index('ix_pay_tx_tenant_intent', ['tenant_id', 'payment_intent_id'], unique=False)
        batch_op.create_index('ix_pay_tx_provider_tx', ['provider_tx_id'], unique=False)

    # 4. Create payment_provider_configs table
    op.create_table(
        'payment_provider_configs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('provider_name', sa.String(length=50), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False),
        sa.Column('credentials_ref', sa.String(length=255), nullable=False),
        sa.Column('webhook_secret_ref', sa.String(length=255), nullable=True),
        sa.Column('settings_json', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'provider_name', name='uq_tenant_payment_provider'),
    )
    with op.batch_alter_table('payment_provider_configs', schema=None) as batch_op:
        batch_op.create_index('ix_payment_provider_configs_id', ['id'], unique=False)
        batch_op.create_index('ix_payment_provider_configs_tenant_id', ['tenant_id'], unique=False)
        batch_op.create_index('ix_payment_provider_configs_provider_name', ['provider_name'], unique=False)
        batch_op.create_index('ix_pay_prov_cfg_tenant_enabled', ['tenant_id', 'is_enabled'], unique=False)

    # 5. Create payment_webhook_events table
    op.create_table(
        'payment_webhook_events',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('provider_event_id', sa.String(length=100), nullable=False),
        sa.Column('payment_intent_id', sa.Uuid(), nullable=True),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('signature_verified', sa.Boolean(), nullable=False),
        sa.Column('processed', sa.Boolean(), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('payload_hash', sa.String(length=64), nullable=False),
        sa.Column('payload_json', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['payment_intent_id'], ['payment_intents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'provider', 'provider_event_id', name='uq_webhook_tenant_provider_event'),
    )
    with op.batch_alter_table('payment_webhook_events', schema=None) as batch_op:
        batch_op.create_index('ix_payment_webhook_events_id', ['id'], unique=False)
        batch_op.create_index('ix_payment_webhook_events_tenant_id', ['tenant_id'], unique=False)
        batch_op.create_index('ix_payment_webhook_events_payment_intent_id', ['payment_intent_id'], unique=False)
        batch_op.create_index('ix_webhook_tenant_processed', ['tenant_id', 'processed'], unique=False)
        batch_op.create_index('ix_webhook_intent', ['payment_intent_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('payment_webhook_events')
    op.drop_table('payment_provider_configs')
    op.drop_table('payment_transactions')
    op.drop_table('payment_intents')

    with op.batch_alter_table('ledger_transactions', schema=None) as batch_op:
        batch_op.drop_index('uq_settlement_idempotency')
