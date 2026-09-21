from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.database import Base, TimestampMixin, UUIDMixin
from packages.tenants.models import JSON_TYPE, Tenant


class InquiryStatus(str, enum.Enum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    QUOTED = "QUOTED"
    CONVERTED = "CONVERTED"
    ARCHIVED = "ARCHIVED"


class ContactMethod(str, enum.Enum):
    TELEGRAM = "TELEGRAM"
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"


class QuoteStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class CustomerInquiry(Base, UUIDMixin, TimestampMixin):
    """Prospective customer configuration inquiry captured before tenant creation."""

    __tablename__ = "customer_inquiries"
    __table_args__ = (
        Index("ix_customer_inquiries_status_created", "status", "created_at"),
        Index("ix_customer_inquiries_contact", "contact_method", "contact_handle"),
    )

    contact_method: Mapped[ContactMethod] = mapped_column(
        Enum(ContactMethod, native_enum=False, length=20),
        nullable=False,
        default=ContactMethod.TELEGRAM,
    )
    contact_handle: Mapped[str] = mapped_column(String(120), nullable=False)
    project_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    estimated_quote: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    status: Mapped[InquiryStatus] = mapped_column(
        Enum(InquiryStatus, native_enum=False, length=20),
        nullable=False,
        default=InquiryStatus.NEW,
    )
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    quotes: Mapped[list[CommercialQuote]] = relationship(
        "CommercialQuote", back_populates="inquiry", order_by="CommercialQuote.version.desc()"
    )


class CommercialQuote(Base, UUIDMixin, TimestampMixin):
    """Versioned commercial proposal created from an inquiry or custom platform deal."""

    __tablename__ = "commercial_quotes"
    __table_args__ = (
        Index("ix_commercial_quotes_number_version", "quote_number", "version", unique=True),
        Index("ix_commercial_quotes_status_created", "status", "created_at"),
        Index("ix_commercial_quotes_inquiry", "inquiry_id"),
    )

    quote_number: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    inquiry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customer_inquiries.id", ondelete="SET NULL"), nullable=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scope_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_contact: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[QuoteStatus] = mapped_column(
        Enum(QuoteStatus, native_enum=False, length=20),
        nullable=False,
        default=QuoteStatus.DRAFT,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    total_one_time: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    total_monthly: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lines: Mapped[list[CommercialQuoteLine]] = relationship(
        "CommercialQuoteLine",
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="CommercialQuoteLine.created_at",
    )
    inquiry: Mapped[CustomerInquiry | None] = relationship("CustomerInquiry", back_populates="quotes")
    tenant: Mapped[Tenant | None] = relationship("Tenant")


class CommercialQuoteLine(Base, UUIDMixin, TimestampMixin):
    """Itemized deliverable or pricing item inside a commercial quote."""

    __tablename__ = "commercial_quote_lines"
    __table_args__ = (Index("ix_commercial_quote_lines_quote_id", "quote_id"),)

    quote_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commercial_quotes.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False, default="general")
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "one_time" or "recurring"
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    quote: Mapped[CommercialQuote] = relationship("CommercialQuote", back_populates="lines")


class IntegrationLifecycle(str, enum.Enum):
    DRAFT = "DRAFT"
    SANDBOX_REVIEW = "SANDBOX_REVIEW"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"


class IntegrationOfferingModel(Base, UUIDMixin, TimestampMixin):
    """Platform-managed catalog of third-party integration offerings."""

    __tablename__ = "integration_offerings"
    __table_args__ = (
        Index("ix_integration_offerings_key", "key", unique=True),
        Index("ix_integration_offerings_category_lifecycle", "category", "lifecycle"),
    )

    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="OTHER")
    adapter_key: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle: Mapped[IntegrationLifecycle] = mapped_column(
        Enum(IntegrationLifecycle, native_enum=False, length=20),
        nullable=False,
        default=IntegrationLifecycle.ACTIVE,
    )
    setup_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    monthly_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    required_credentials: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    supported_templates: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    features: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    requirements: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    docs_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TenantIntegrationEntitlement(Base, UUIDMixin, TimestampMixin):
    """Tenant-specific commercial entitlement granting access to configure an integration."""

    __tablename__ = "tenant_integration_entitlements"
    __table_args__ = (
        Index("ix_tenant_integration_entitlements_unique", "tenant_id", "integration_key", unique=True),
        Index("ix_tenant_integration_entitlements_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    integration_key: Mapped[str] = mapped_column(String(50), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    granted_by: Mapped[str] = mapped_column(String(40), nullable=False, default="OPERATOR")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    tenant: Mapped[Tenant] = relationship("Tenant")


class LicenseType(str, enum.Enum):
    MANAGED = "MANAGED"
    DEDICATED_DEPLOYMENT = "DEDICATED_DEPLOYMENT"
    SOURCE_LICENSE = "SOURCE_LICENSE"


class HandoffStatus(str, enum.Enum):
    PREPARING = "PREPARING"
    READY_FOR_EXPORT = "READY_FOR_EXPORT"
    EXPORTED = "EXPORTED"
    HANDED_OFF = "HANDED_OFF"
    CANCELLED = "CANCELLED"


class DeploymentHandoff(Base, UUIDMixin, TimestampMixin):
    """Formal commercial delivery and license evidence for self-hosted or dedicated deployments."""

    __tablename__ = "deployment_handoffs"
    __table_args__ = (
        Index("ix_deployment_handoffs_license_key", "license_key", unique=True),
        Index("ix_deployment_handoffs_tenant_status", "tenant_id", "status"),
        Index("ix_deployment_handoffs_quote", "quote_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    quote_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("commercial_quotes.id", ondelete="SET NULL"), nullable=True
    )
    license_type: Mapped[LicenseType] = mapped_column(
        Enum(LicenseType, native_enum=False, length=30),
        nullable=False,
        default=LicenseType.DEDICATED_DEPLOYMENT,
    )
    license_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    licensed_to: Mapped[str] = mapped_column(String(120), nullable=False)
    licensed_domain: Mapped[str | None] = mapped_column(String(120), nullable=True)
    version_tag: Mapped[str] = mapped_column(String(40), nullable=False, default="v0.1.0-phase14.5")
    status: Mapped[HandoffStatus] = mapped_column(
        Enum(HandoffStatus, native_enum=False, length=25),
        nullable=False,
        default=HandoffStatus.PREPARING,
    )
    support_plan: Mapped[str | None] = mapped_column(String(60), nullable=True)
    runtime_deactivated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    runtime_deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    export_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    export_artifact_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    handoff_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    handed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship("Tenant")
    quote: Mapped[CommercialQuote | None] = relationship("CommercialQuote")
