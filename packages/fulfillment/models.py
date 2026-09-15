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

from packages.commerce.models import Order, OrderItem
from packages.core.database import Base, TimestampMixin, UUIDMixin
from packages.providers.models import Provider
from packages.tenants.models import JSON_TYPE, Tenant


class FulfillmentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    RETRYING = "RETRYING"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FulfillmentAttempt(Base, UUIDMixin, TimestampMixin):
    """Tracks an individual, idempotent execution attempt to fulfill an order through an external provider."""

    __tablename__ = "fulfillment_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_fulfillment_idempotency_key"),
        Index("ix_fulfillment_tenant_order", "tenant_id", "order_id", "status"),
        Index("ix_fulfillment_external_order", "provider_id", "external_order_id"),
        Index(
            "ix_fulfillment_tenant_started_provider_status",
            "tenant_id",
            "started_at",
            "provider_id",
            "status",
        ),
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
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"),
        nullable=True,
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
    order_item: Mapped["OrderItem | None"] = relationship("OrderItem")
    provider: Mapped["Provider | None"] = relationship("Provider")


class FulfillmentJobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class FulfillmentJobRecord(Base, UUIDMixin, TimestampMixin):
    """Durable fulfillment job record persisted in database to survive process crashes."""

    __tablename__ = "fulfillment_jobs"
    __table_args__ = (
        Index("ix_fulfillment_jobs_tenant_status", "tenant_id", "status"),
        Index("ix_fulfillment_jobs_order", "order_id"),
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
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[FulfillmentJobStatus] = mapped_column(
        Enum(FulfillmentJobStatus, name="fulfillment_job_status_enum", native_enum=False),
        default=FulfillmentJobStatus.QUEUED,
        nullable=False,
        index=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    failure_classification: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    manual_requeue_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        server_default="0",
    )
    last_requeued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_requeued_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship("Tenant")
    order: Mapped["Order"] = relationship("Order")
