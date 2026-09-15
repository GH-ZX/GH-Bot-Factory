"""add order checkout idempotency

Revision ID: c41d6f8e2a10
Revises: b7e3a912f45c
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c41d6f8e2a10"
down_revision: str | Sequence[str] | None = "b7e3a912f45c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("checkout_idempotency_key", sa.String(length=100), nullable=True)
        )
        batch_op.add_column(
            sa.Column("checkout_request_hash", sa.String(length=64), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_order_checkout_idempotency",
            ["tenant_id", "user_id", "checkout_idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_constraint("uq_order_checkout_idempotency", type_="unique")
        batch_op.drop_column("checkout_request_hash")
        batch_op.drop_column("checkout_idempotency_key")
