import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.commerce.models import Order
from packages.core.database import Base, TimestampMixin, UUIDMixin
from packages.providers.models import Provider
from packages.tenants.models import JSON_TYPE, Tenant


class FulfillmentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FulfillmentAttempt(Base, UUIDMixin, TimestampMixin):
    """Tracks an individual, idempotent execution attempt to fulfill an order through an external provider."""

    __tablename__ = "fulfillment_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_fulfillment_idempotency_key"),
        Index("ix_fulfillment_tenant_order", "tenant_id", "order_id", "status"),
        Index("ix_fulfillment_external_order", "provider_id", "external_order_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    status: Mapped[FulfillmentStatus] = mapped_column(
        Enum(FulfillmentStatus, name="fulfillment_status_enum", native_enum=False),
        default=FulfillmentStatus.PENDING,
        nullable=False,
    )
    external_order_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    cost_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    cost_currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )
    response_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )
    error_classification: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    tenant: Mapped["Tenant"] = relationship("Tenant")
    order: Mapped["Order"] = relationship("Order")
    provider: Mapped["Provider | None"] = relationship("Provider")
