"""add financial resolution cases

Revision ID: 5a6e7f8b9c10
Revises: 19c7d8e41f02
Create Date: 2026-09-15 13:55:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime
import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "5a6e7f8b9c10"
down_revision: str | None = "19c7d8e41f02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _as_uuid(value: object | None) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def upgrade() -> None:
    op.create_table(
        "financial_resolution_cases",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=160), nullable=False),
        sa.Column("case_type", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="HIGH"),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "IN_PROGRESS",
                "RESOLVED",
                name="financial_resolution_case_status_enum",
                native_enum=False,
            ),
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column("payment_intent_id", sa.Uuid(), nullable=True),
        sa.Column("wallet_id", sa.Uuid(), nullable=True),
        sa.Column("reversal_id", sa.Uuid(), nullable=True),
        sa.Column("reconciliation_event_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_to_user_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("resolution_code", sa.String(length=100), nullable=True),
        sa.Column("resolution_note", sa.String(length=1000), nullable=True),
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_intent_id"], ["payment_intents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reversal_id"], ["wallet_topup_reversals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["reconciliation_event_id"], ["payment_reconciliation_events.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["assigned_to_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "source_key", name="uq_financial_case_tenant_source"),
    )
    op.create_index("ix_financial_resolution_cases_tenant_id", "financial_resolution_cases", ["tenant_id"])
    op.create_index("ix_financial_resolution_cases_payment_intent_id", "financial_resolution_cases", ["payment_intent_id"])
    op.create_index("ix_financial_resolution_cases_wallet_id", "financial_resolution_cases", ["wallet_id"])
    op.create_index("ix_financial_resolution_cases_reversal_id", "financial_resolution_cases", ["reversal_id"])
    op.create_index(
        "ix_financial_resolution_cases_reconciliation_event_id",
        "financial_resolution_cases",
        ["reconciliation_event_id"],
    )
    op.create_index("ix_financial_resolution_cases_assigned_to_user_id", "financial_resolution_cases", ["assigned_to_user_id"])
    op.create_index("ix_financial_resolution_cases_resolved_by_user_id", "financial_resolution_cases", ["resolved_by_user_id"])
    op.create_index("ix_financial_case_tenant_status", "financial_resolution_cases", ["tenant_id", "status"])
    op.create_index("ix_financial_case_tenant_type", "financial_resolution_cases", ["tenant_id", "case_type"])
    op.create_index("ix_financial_case_wallet", "financial_resolution_cases", ["wallet_id"])
    op.create_index("ix_financial_case_assignee", "financial_resolution_cases", ["assigned_to_user_id"])

    # Backfill existing manual-review reversals so upgrades do not hide unresolved money movement.
    bind = op.get_bind()
    case_table = sa.table(
        "financial_resolution_cases",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("source_key", sa.String()),
        sa.column("case_type", sa.String()),
        sa.column("severity", sa.String()),
        sa.column("status", sa.String()),
        sa.column("payment_intent_id", sa.Uuid()),
        sa.column("wallet_id", sa.Uuid()),
        sa.column("reversal_id", sa.Uuid()),
        sa.column("reconciliation_event_id", sa.Uuid()),
        sa.column("version", sa.Integer()),
        sa.column("metadata_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    reversal_case_by_intent: dict[str, uuid.UUID] = {}
    reversals = bind.execute(
        sa.text(
            "SELECT id, tenant_id, payment_intent_id, wallet_id, last_error_code, "
            "last_error_detail, metadata_json FROM wallet_topup_reversals WHERE status = 'MANUAL_REVIEW'"
        )
    ).mappings()
    for row in reversals:
        case_id = uuid.uuid4()
        intent_key = str(row["payment_intent_id"])
        reversal_case_by_intent[intent_key] = case_id
        bind.execute(
            sa.insert(case_table).values(
                id=case_id,
                tenant_id=_as_uuid(row["tenant_id"]),
                source_key=f"reversal:{row['id']}",
                case_type=row["last_error_code"] or "TOPUP_REVERSAL_MANUAL_REVIEW",
                severity="CRITICAL",
                status="OPEN",
                payment_intent_id=_as_uuid(row["payment_intent_id"]),
                wallet_id=_as_uuid(row["wallet_id"]),
                reversal_id=_as_uuid(row["id"]),
                reconciliation_event_id=None,
                version=1,
                metadata_json={"backfilled": True, "last_error_detail": row["last_error_detail"]},
                created_at=now,
                updated_at=now,
            )
        )

    events = bind.execute(
        sa.text(
            "SELECT id, tenant_id, payment_intent_id, provider, provider_event_id, event_type, "
            "classification, metadata_json FROM payment_reconciliation_events WHERE requires_review IS TRUE"
        )
    ).mappings()
    for row in events:
        existing_case_id = reversal_case_by_intent.get(str(row["payment_intent_id"])) if row["payment_intent_id"] else None
        if existing_case_id is not None:
            bind.execute(
                sa.update(case_table)
                .where(case_table.c.id == existing_case_id)
                .values(
                    reconciliation_event_id=_as_uuid(row["id"]),
                    case_type=row["classification"],
                    metadata_json={
                        "backfilled": True,
                        "provider": row["provider"],
                        "provider_event_id": row["provider_event_id"],
                        "event_type": row["event_type"],
                    },
                    updated_at=now,
                )
            )
            continue

        wallet_id = None
        if row["payment_intent_id"] is not None:
            wallet_id = bind.execute(
                sa.text(
                    "SELECT w.id FROM payment_intents pi JOIN wallets w "
                    "ON w.tenant_id = pi.tenant_id AND w.user_id = pi.user_id AND w.currency = pi.currency "
                    "WHERE pi.id = :intent_id LIMIT 1"
                ),
                {"intent_id": str(row["payment_intent_id"])},
            ).scalar_one_or_none()
        bind.execute(
            sa.insert(case_table).values(
                id=uuid.uuid4(),
                tenant_id=_as_uuid(row["tenant_id"]),
                source_key=f"event:{row['id']}",
                case_type=row["classification"],
                severity="CRITICAL" if wallet_id is not None else "HIGH",
                status="OPEN",
                payment_intent_id=_as_uuid(row["payment_intent_id"]),
                wallet_id=_as_uuid(wallet_id),
                reversal_id=None,
                reconciliation_event_id=_as_uuid(row["id"]),
                version=1,
                metadata_json={
                    "backfilled": True,
                    "provider": row["provider"],
                    "provider_event_id": row["provider_event_id"],
                    "event_type": row["event_type"],
                },
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    case_count = bind.execute(sa.text("SELECT COUNT(*) FROM financial_resolution_cases")).scalar_one()
    if case_count:
        raise RuntimeError(
            "Cannot downgrade while financial resolution cases exist. Export and resolve the audit workflow first."
        )
    op.drop_index("ix_financial_case_assignee", table_name="financial_resolution_cases")
    op.drop_index("ix_financial_case_wallet", table_name="financial_resolution_cases")
    op.drop_index("ix_financial_case_tenant_type", table_name="financial_resolution_cases")
    op.drop_index("ix_financial_case_tenant_status", table_name="financial_resolution_cases")
    op.drop_index("ix_financial_resolution_cases_resolved_by_user_id", table_name="financial_resolution_cases")
    op.drop_index("ix_financial_resolution_cases_assigned_to_user_id", table_name="financial_resolution_cases")
    op.drop_index("ix_financial_resolution_cases_reconciliation_event_id", table_name="financial_resolution_cases")
    op.drop_index("ix_financial_resolution_cases_reversal_id", table_name="financial_resolution_cases")
    op.drop_index("ix_financial_resolution_cases_wallet_id", table_name="financial_resolution_cases")
    op.drop_index("ix_financial_resolution_cases_payment_intent_id", table_name="financial_resolution_cases")
    op.drop_index("ix_financial_resolution_cases_tenant_id", table_name="financial_resolution_cases")
    op.drop_table("financial_resolution_cases")
