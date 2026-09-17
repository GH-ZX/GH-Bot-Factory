"""add commercial quotes and quote lines

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-09-18 01:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a8"
down_revision: str | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commercial_quotes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quote_number", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("inquiry_id", sa.Uuid(), nullable=True),
        sa.Column("customer_name", sa.String(length=120), nullable=False),
        sa.Column("customer_contact", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "SENT", "ACCEPTED", "REJECTED", "EXPIRED", "SUPERSEDED", name="quotestatus", native_enum=False, length=20),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("total_one_time", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("total_monthly", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["inquiry_id"], ["customer_inquiries.id"], name=op.f("fk_commercial_quotes_inquiry_id_customer_inquiries"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commercial_quotes")),
    )
    op.create_index(
        "ix_commercial_quotes_number_version",
        "commercial_quotes",
        ["quote_number", "version"],
        unique=True,
    )
    op.create_index(
        "ix_commercial_quotes_status_created",
        "commercial_quotes",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_commercial_quotes_inquiry",
        "commercial_quotes",
        ["inquiry_id"],
        unique=False,
    )

    op.create_table(
        "commercial_quote_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=False, server_default="general"),
        sa.Column("item_type", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["quote_id"], ["commercial_quotes.id"], name=op.f("fk_commercial_quote_lines_quote_id_commercial_quotes"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commercial_quote_lines")),
    )
    op.create_index(
        "ix_commercial_quote_lines_quote_id",
        "commercial_quote_lines",
        ["quote_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_commercial_quote_lines_quote_id", table_name="commercial_quote_lines")
    op.drop_table("commercial_quote_lines")
    op.drop_index("ix_commercial_quotes_inquiry", table_name="commercial_quotes")
    op.drop_index("ix_commercial_quotes_status_created", table_name="commercial_quotes")
    op.drop_index("ix_commercial_quotes_number_version", table_name="commercial_quotes")
    op.drop_table("commercial_quotes")
