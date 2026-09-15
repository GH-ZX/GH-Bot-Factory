import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.database import Base, TimestampMixin, UUIDMixin
from packages.tenants.models import JSON_TYPE


class ProviderModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "providers"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tenant_configs: Mapped[list["TenantProviderConfig"]] = relationship(
        "TenantProviderConfig",
        back_populates="provider",
        cascade="all, delete-orphan",
    )


class TenantProviderConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "tenant_provider_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_id", name="uq_tenant_provider"),
        Index("ix_tenant_provider_priority", "tenant_id", "priority"),
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
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    markup_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    credentials: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    health_status: Mapped[str] = mapped_column(String(50), default="HEALTHY", nullable=False)

    provider: Mapped[ProviderModel] = relationship("ProviderModel", back_populates="tenant_configs")
