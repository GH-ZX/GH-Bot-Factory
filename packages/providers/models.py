import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.database import Base, TimestampMixin, UUIDMixin
from packages.tenants.models import JSON_TYPE, Tenant


class ProviderCategory(str, enum.Enum):
    """Canonical business category fulfilled by a tenant provider connection."""

    NUMBER = "NUMBER"
    ACCOUNT = "ACCOUNT"
    GIFT = "GIFT"
    DIGITAL_PRODUCT = "DIGITAL_PRODUCT"
    SERVICE = "SERVICE"
    OTHER = "OTHER"


class ProviderHealthStatus(str, enum.Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class Provider(Base, UUIDMixin, TimestampMixin):
    """Tenant-owned external service provider configuration."""

    __tablename__ = "providers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_tenant_provider_slug"),
        Index("ix_provider_tenant_enabled", "tenant_id", "is_enabled", "priority"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[ProviderCategory] = mapped_column(
        Enum(ProviderCategory, name="provider_category_enum", native_enum=False),
        default=ProviderCategory.DIGITAL_PRODUCT,
        nullable=False,
        index=True,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    health_status: Mapped[ProviderHealthStatus] = mapped_column(
        Enum(ProviderHealthStatus, name="provider_health_enum", native_enum=False),
        default=ProviderHealthStatus.HEALTHY,
        nullable=False,
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_health_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_health_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )

    tenant: Mapped["Tenant"] = relationship("Tenant")
    credentials: Mapped[list["ProviderCredential"]] = relationship(
        "ProviderCredential",
        back_populates="provider",
        cascade="all, delete-orphan",
    )
    product_mappings: Mapped[list["ProviderProductMapping"]] = relationship(
        "ProviderProductMapping",
        back_populates="provider",
        cascade="all, delete-orphan",
    )


class ProviderRoutingStrategy(str, enum.Enum):
    PRIORITY = "PRIORITY"
    LOWEST_COST = "LOWEST_COST"
    AVAILABILITY = "AVAILABILITY"
    HEALTHIEST = "HEALTHIEST"
    WEIGHTED = "WEIGHTED"
    MANUAL = "MANUAL"


class ProviderRoutingPolicy(Base, UUIDMixin, TimestampMixin):
    """Tenant-owned deterministic routing policy for one product/variant scope."""

    __tablename__ = "provider_routing_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "product_id",
            "product_variant_id",
            name="uq_provider_routing_variant_scope",
        ),
        Index(
            "uq_provider_routing_product_default",
            "tenant_id",
            "product_id",
            unique=True,
            sqlite_where=text("product_variant_id IS NULL"),
            postgresql_where=text("product_variant_id IS NULL"),
        ),
        Index(
            "ix_provider_routing_tenant_product",
            "tenant_id",
            "product_id",
            "product_variant_id",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    strategy: Mapped[ProviderRoutingStrategy] = mapped_column(
        Enum(ProviderRoutingStrategy, name="provider_routing_strategy_enum", native_enum=False),
        default=ProviderRoutingStrategy.PRIORITY,
        nullable=False,
    )
    preferred_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    failover_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    weights_json: Mapped[dict[str, int]] = mapped_column(JSON_TYPE, default=dict, nullable=False)



class ProviderOfferSnapshot(Base, UUIDMixin, TimestampMixin):
    """Latest normalized read-only offer observation for one provider mapping."""

    __tablename__ = "provider_offer_snapshots"
    __table_args__ = (
        UniqueConstraint("mapping_id", name="uq_provider_offer_snapshot_mapping"),
        Index(
            "ix_provider_offer_snapshot_tenant_product",
            "tenant_id",
            "product_id",
            "product_variant_id",
        ),
        Index("ix_provider_offer_snapshot_expires", "tenant_id", "expires_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mapping_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_product_mappings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    external_product_id: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    cost_currency: Mapped[str] = mapped_column(String(12), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    stock_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=100000)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)



class ProviderBalanceSnapshot(Base, UUIDMixin, TimestampMixin):
    """Latest observed supplier account balance used for operational low-balance alerts."""

    __tablename__ = "provider_balance_snapshots"
    __table_args__ = (
        UniqueConstraint("provider_id", name="uq_provider_balance_snapshot_provider"),
        Index("ix_provider_balance_tenant_low", "tenant_id", "is_low_balance"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    balance: Mapped[Decimal | None] = mapped_column(Numeric(36, 18), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    low_balance_threshold: Mapped[Decimal | None] = mapped_column(Numeric(36, 18), nullable=True)
    is_low_balance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    provider: Mapped["Provider"] = relationship("Provider")


# Backward-compatible alias for Phase 2 imports
ProviderModel = Provider


class ProviderCredential(Base, UUIDMixin, TimestampMixin):
    """Secure credential pointer for external provider authentication.
    
    Never stores plaintext credentials in the database.
    """

    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint("provider_id", "credential_type", name="uq_provider_credential_type"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credential_type: Mapped[str] = mapped_column(String(50), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)

    provider: Mapped[Provider] = relationship("Provider", back_populates="credentials")


class ProviderProductMapping(Base, UUIDMixin, TimestampMixin):
    """Normalized mapping between an internal Commerce Product and an external provider product."""

    __tablename__ = "provider_product_mappings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "product_id",
            "provider_id",
            "external_product_id",
            name="uq_tenant_prod_provider_ext",
        ),
        Index("ix_ppm_tenant_prod_enabled", "tenant_id", "product_id", "is_enabled"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    external_product_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    cost_currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    priority_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )

    provider: Mapped[Provider] = relationship("Provider", back_populates="product_mappings")
