"""Clean legacy payment index drift before canonical CI migration checks.

Revision ID: 8a9b0c1d2e34
Revises: 7b8c9d0e1f23
Create Date: 2026-09-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8a9b0c1d2e34"
down_revision: str | None = "7b8c9d0e1f23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_PRIMARY_KEY_INDEXES = (
    ("payment_intents", "ix_payment_intents_id"),
    ("payment_transactions", "ix_payment_transactions_id"),
    ("payment_provider_configs", "ix_payment_provider_configs_id"),
    ("payment_webhook_events", "ix_payment_webhook_events_id"),
)


def upgrade() -> None:
    # These indexes duplicate each table's primary-key index and are not declared by
    # current ORM metadata. Keeping them causes Alembic autogenerate drift forever.
    for table_name, index_name in LEGACY_PRIMARY_KEY_INDEXES:
        op.drop_index(index_name, table_name=table_name)


def downgrade() -> None:
    for table_name, index_name in LEGACY_PRIMARY_KEY_INDEXES:
        op.create_index(index_name, table_name, ["id"], unique=False)
