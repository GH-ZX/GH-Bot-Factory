from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.database import Base, TimestampMixin, UUIDMixin


class SupportCase(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "support_cases"
    __table_args__ = (
        Index("ix_support_tenant_status", "tenant_id", "status", "created_at"),
        UniqueConstraint("tenant_id", "warranty_item_id", name="uq_warranty_item_claim"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    warranty_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("order_items.id"))
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="SUPPORT")
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    warranty_terms: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)


class SupportMessage(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "support_messages"
    __table_args__ = (Index("ix_support_message_case", "tenant_id", "case_id", "created_at"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_cases.id"), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_staff: Mapped[bool] = mapped_column(Boolean, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class Coupon(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_coupon_tenant_code"),
        CheckConstraint(
            "percent > 0 AND percent <= 90 AND minimum_amount >= 0 AND max_uses > 0 AND used_count >= 0 AND used_count <= max_uses",
            name="ck_coupon_limits",
        ),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minimum_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal(0)
    )
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CouponRedemption(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "coupon_redemptions"
    __table_args__ = (UniqueConstraint("tenant_id", "order_id", name="uq_coupon_order"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    coupon_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("coupons.id"), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class Announcement(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "announcements"
    __table_args__ = (Index("ix_announcement_tenant_status", "tenant_id", "status"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str] = mapped_column(String(20), nullable=False, default="ALL")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnnouncementDelivery(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "announcement_deliveries"
    __table_args__ = (
        UniqueConstraint("announcement_id", "binding_id", name="uq_announcement_recipient"),
        Index("ix_announcement_delivery_queue", "status", "next_attempt_at"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    announcement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("announcements.id"), nullable=False
    )
    binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant_telegram_users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(60))
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
