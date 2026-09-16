import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.database import Base, TimestampMixin, UUIDMixin
from packages.tenants.models import JSON_TYPE


class BotProvisioningStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    READY = "READY"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BotProvisioningJob(Base, UUIDMixin, TimestampMixin):
    """Durable request to verify Telegram identity and converge a tenant Bot record."""

    __tablename__ = "bot_provisioning_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_bot_provision_tenant_idempotency"),
        Index("ix_bot_provision_status_due", "status", "next_attempt_at"),
        Index("ix_bot_provision_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    bot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bots.id", ondelete="SET NULL"), nullable=True, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    token_secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    requested_display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    desired_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    desired_config: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    status: Mapped[BotProvisioningStatus] = mapped_column(
        Enum(BotProvisioningStatus, name="bot_provisioning_status_enum", native_enum=False),
        default=BotProvisioningStatus.PENDING,
        nullable=False,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    verified_telegram_bot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    verified_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    bot = relationship("Bot")
