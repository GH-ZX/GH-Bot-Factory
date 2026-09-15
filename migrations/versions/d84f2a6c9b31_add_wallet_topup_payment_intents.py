"""add wallet topup payment intents

Revision ID: d84f2a6c9b31
Revises: c41d6f8e2a10
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d84f2a6c9b31"
down_revision: str | Sequence[str] | None = "c41d6f8e2a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("payment_intents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "purpose",
                sa.Enum(
                    "ORDER_PAYMENT",
                    "WALLET_TOPUP",
                    name="payment_intent_purpose_enum",
                    native_enum=False,
                ),
                server_default="ORDER_PAYMENT",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("checkout_url", sa.String(length=2048), nullable=True))
        batch_op.alter_column(
            "order_id",
            existing_type=sa.Uuid(),
            nullable=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    topup_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM payment_intents WHERE purpose = 'WALLET_TOPUP'")
    ).scalar_one()
    if topup_count:
        raise RuntimeError(
            "Cannot downgrade wallet top-up migration while WALLET_TOPUP payment intents exist. "
            "Export/reconcile financial data before downgrading."
        )

    with op.batch_alter_table("payment_intents", schema=None) as batch_op:
        batch_op.alter_column(
            "order_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
        batch_op.drop_column("checkout_url")
        batch_op.drop_column("purpose")
