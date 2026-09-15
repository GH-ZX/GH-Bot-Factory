"""add fulfillment operations metadata

Revision ID: 19c7d8e41f02
Revises: f02c4d8e5a63
Create Date: 2026-09-15 12:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "19c7d8e41f02"
down_revision: str | None = "f02c4d8e5a63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("fulfillment_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("failure_classification", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "manual_requeue_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(sa.Column("last_requeued_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_requeued_by", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_fulfillment_jobs_last_requeued_by_users",
            "users",
            ["last_requeued_by"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_fulfillment_jobs_failure_classification",
            ["failure_classification"],
            unique=False,
        )
        batch_op.create_index(
            "ix_fulfillment_jobs_last_requeued_by",
            ["last_requeued_by"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("fulfillment_jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_fulfillment_jobs_last_requeued_by")
        batch_op.drop_index("ix_fulfillment_jobs_failure_classification")
        batch_op.drop_constraint(
            "fk_fulfillment_jobs_last_requeued_by_users",
            type_="foreignkey",
        )
        batch_op.drop_column("last_requeued_by")
        batch_op.drop_column("last_requeued_at")
        batch_op.drop_column("manual_requeue_count")
        batch_op.drop_column("failure_classification")
