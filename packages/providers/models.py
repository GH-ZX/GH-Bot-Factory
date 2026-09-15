import enum
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.database import Base, TimestampMixin, UUIDMixin
from packages.tenants.models import JSON_TYPE, Tenant


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
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    health_status: Mapped[ProviderHealthStatus] = mapped_column(
        Enum(ProviderHealthStatus, name="provider_health_enum", native_enum=False),
        default=ProviderHealthStatus.HEALTHY,
        nullable=False,
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
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
