from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.database import Base, TimestampMixin, UUIDMixin


class MaintenanceIssue(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "maintenance_issues"
    __table_args__ = (Index("ix_maintenance_issue_tenant", "tenant_id", "created_at"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)
    scope: Mapped[str] = mapped_column(String(24), default="UNDECIDED", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    fix_version: Mapped[str | None] = mapped_column(String(80))
    affected_version: Mapped[str | None] = mapped_column(String(80))
    source_case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_cases.id"))


class MaintenanceEvent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "maintenance_events"
    __table_args__ = (Index("ix_maintenance_event_issue", "tenant_id", "issue_id", "created_at"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("maintenance_issues.id"), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class DiagnosticGrant(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "diagnostic_grants"
    __table_args__ = (Index("ix_diagnostic_grant_digest", "token_digest", unique=True),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("maintenance_issues.id"), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class CustomerUpdate(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "customer_updates"
    __table_args__ = (Index("ix_customer_update_tenant", "tenant_id", "created_at"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("maintenance_issues.id"), nullable=False)
    release_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer_releases.id"))
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    previous_image: Mapped[str] = mapped_column(String(255), nullable=False)
    release_notes: Mapped[str] = mapped_column(Text, nullable=False)
    migration_notes: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PROPOSED", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    backup_reference: Mapped[str | None] = mapped_column(String(255))
    evidence: Mapped[str | None] = mapped_column(Text)


class CustomerRelease(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "customer_releases"
    __table_args__ = (UniqueConstraint("tenant_id", "version_label", name="uq_customer_release_version"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    release_notes: Mapped[str] = mapped_column(Text, nullable=False)
    migration_notes: Mapped[str] = mapped_column(Text, nullable=False)
    rollback_notes: Mapped[str] = mapped_column(Text, nullable=False)


class ReleaseIssue(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "release_issues"
    __table_args__ = (UniqueConstraint("tenant_id", "release_id", "issue_id", name="uq_release_issue"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_releases.id"), nullable=False)
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("maintenance_issues.id"), nullable=False)
