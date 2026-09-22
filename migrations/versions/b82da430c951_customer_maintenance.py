"""Customer-controlled support and update records.

Revision ID: b82da430c951
Revises: a71c9e23b840
"""
import sqlalchemy as sa
from alembic import op

revision = "b82da430c951"
down_revision = "a71c9e23b840"
branch_labels = None
depends_on = None


def common():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade():
    op.create_table("maintenance_issues", *common(),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("scope", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fix_version", sa.String(80)),
    )
    op.create_index("ix_maintenance_issue_tenant", "maintenance_issues", ["tenant_id", "created_at"])
    op.create_table("maintenance_events", *common(),
        sa.Column("issue_id", sa.Uuid(), sa.ForeignKey("maintenance_issues.id"), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_index("ix_maintenance_event_issue", "maintenance_events", ["tenant_id", "issue_id", "created_at"])
    op.create_table("diagnostic_grants", *common(),
        sa.Column("issue_id", sa.Uuid(), sa.ForeignKey("maintenance_issues.id"), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
    )
    op.create_index("ix_diagnostic_grant_digest", "diagnostic_grants", ["token_digest"], unique=True)
    op.create_table("customer_updates", *common(),
        sa.Column("issue_id", sa.Uuid(), sa.ForeignKey("maintenance_issues.id"), nullable=False),
        sa.Column("version_label", sa.String(80), nullable=False),
        sa.Column("image", sa.String(255), nullable=False),
        sa.Column("previous_image", sa.String(255), nullable=False),
        sa.Column("release_notes", sa.Text(), nullable=False),
        sa.Column("migration_notes", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("backup_reference", sa.String(255)),
        sa.Column("evidence", sa.Text()),
    )
    op.create_index("ix_customer_update_tenant", "customer_updates", ["tenant_id", "created_at"])

    if op.get_bind().dialect.name == "postgresql":
        for table in ("maintenance_issues", "maintenance_events", "diagnostic_grants", "customer_updates"):
            op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))


def downgrade():
    for table in ("customer_updates", "diagnostic_grants", "maintenance_events", "maintenance_issues"):
        op.drop_table(table)
