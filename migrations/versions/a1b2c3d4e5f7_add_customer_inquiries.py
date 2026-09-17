"""add customer inquiries table

Revision ID: a1b2c3d4e5f7
Revises: f2a3b4c5d6e7
Create Date: 2026-09-18 01:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "customer_inquiries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "contact_method",
            sa.Enum("TELEGRAM", "WHATSAPP", "EMAIL", name="contactmethod", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("contact_handle", sa.String(length=120), nullable=False),
        sa.Column("project_notes", sa.Text(), nullable=True),
        sa.Column("configuration", json_type, nullable=False),
        sa.Column("estimated_quote", json_type, nullable=False),
        sa.Column(
            "status",
            sa.Enum("NEW", "CONTACTED", "QUOTED", "CONVERTED", "ARCHIVED", name="inquirystatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_inquiries")),
    )
    op.create_index(
        "ix_customer_inquiries_status_created",
        "customer_inquiries",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_customer_inquiries_contact",
        "customer_inquiries",
        ["contact_method", "contact_handle"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customer_inquiries_ip_hash"),
        "customer_inquiries",
        ["ip_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_customer_inquiries_ip_hash"), table_name="customer_inquiries")
    op.drop_index("ix_customer_inquiries_contact", table_name="customer_inquiries")
    op.drop_index("ix_customer_inquiries_status_created", table_name="customer_inquiries")
    op.drop_table("customer_inquiries")
