"""add_refund_idempotency_unique_index

Revision ID: 867840fa9063
Revises: 8bfc9b56eb94
Create Date: 2026-09-15 04:25:10.765441

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '867840fa9063'
down_revision: str | Sequence[str] | None = '8bfc9b56eb94'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    
    # Check for legacy duplicate refunds safely before applying unique index
    dup_query = sa.text("""
        SELECT wallet_id, reference_type, reference_id, COUNT(*) as cnt
        FROM ledger_transactions
        WHERE transaction_type = 'REFUND'
          AND reference_type IS NOT NULL
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
            f"Cannot apply migration {revision}: found {len(duplicates)} duplicate refund group(s) "
            f"in ledger_transactions: {'; '.join(details)}. "
            "A unique constraint cannot be created without violating database integrity. "
            "Manual accounting audit and reconciliation is required. No financial records were deleted."
        )

    with op.batch_alter_table('ledger_transactions', schema=None) as batch_op:
        batch_op.create_index(
            'uq_refund_idempotency',
            ['wallet_id', 'reference_type', 'reference_id'],
            unique=True,
            postgresql_where=sa.text("transaction_type = 'REFUND' AND reference_type IS NOT NULL AND reference_id IS NOT NULL"),
            sqlite_where=sa.text("transaction_type = 'REFUND' AND reference_type IS NOT NULL AND reference_id IS NOT NULL"),
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('ledger_transactions', schema=None) as batch_op:
        batch_op.drop_index('uq_refund_idempotency')

