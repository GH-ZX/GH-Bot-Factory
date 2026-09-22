"""Delivery plans and tenant-owned issue/release links.

Revision ID: c93eb541da62
Revises: b82da430c951
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c93eb541da62"
down_revision = "b82da430c951"
branch_labels = None
depends_on = None


def common():
    return [sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade():
    op.add_column("deployment_handoffs", sa.Column("delivery_details", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), server_default="{}", nullable=False))
    op.create_table("customer_releases", *common(),
        sa.Column("version_label", sa.String(80), nullable=False),
        sa.Column("image", sa.String(255), nullable=False),
        sa.Column("release_notes", sa.Text(), nullable=False),
        sa.Column("migration_notes", sa.Text(), nullable=False),
        sa.Column("rollback_notes", sa.Text(), nullable=False),
        sa.UniqueConstraint("tenant_id", "version_label", name="uq_customer_release_version"))
    op.create_table("release_issues", *common(),
        sa.Column("release_id", sa.Uuid(), sa.ForeignKey("customer_releases.id"), nullable=False),
        sa.Column("issue_id", sa.Uuid(), sa.ForeignKey("maintenance_issues.id"), nullable=False),
        sa.UniqueConstraint("tenant_id", "release_id", "issue_id", name="uq_release_issue"))
    with op.batch_alter_table("maintenance_issues") as batch:
        batch.add_column(sa.Column("affected_version", sa.String(80)))
        batch.add_column(sa.Column("source_case_id", sa.Uuid()))
        batch.create_foreign_key("fk_maintenance_source_case", "support_cases", ["source_case_id"], ["id"])
    with op.batch_alter_table("customer_updates") as batch:
        batch.add_column(sa.Column("release_id", sa.Uuid()))
        batch.create_foreign_key("fk_update_release", "customer_releases", ["release_id"], ["id"])
    if op.get_bind().dialect.name == "postgresql":
        for table in ("customer_releases", "release_issues"):
            op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))


def downgrade():
    with op.batch_alter_table("customer_updates") as batch:
        batch.drop_constraint("fk_update_release", type_="foreignkey")
        batch.drop_column("release_id")
    with op.batch_alter_table("maintenance_issues") as batch:
        batch.drop_constraint("fk_maintenance_source_case", type_="foreignkey")
        batch.drop_column("source_case_id")
        batch.drop_column("affected_version")
    op.drop_table("release_issues")
    op.drop_table("customer_releases")
    op.drop_column("deployment_handoffs", "delivery_details")
