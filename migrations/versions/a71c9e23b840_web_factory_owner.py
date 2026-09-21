"""Explicit installation web operator; no implicit tenant-role escalation.

Revision ID: a71c9e23b840
Revises: 0ce5d2c98e7f
"""
import sqlalchemy as sa
from alembic import op

revision = "a71c9e23b840"
down_revision = "0ce5d2c98e7f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("system_install_state") as batch:
        batch.add_column(sa.Column("operator_user_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_install_operator_user", "users", ["operator_user_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("system_install_state") as batch:
        batch.drop_constraint("fk_install_operator_user", type_="foreignkey")
        batch.drop_column("operator_user_id")
