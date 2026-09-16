from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "TRIALING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    PAUSED = "PAUSED"
    CANCELED = "CANCELED"


class BillingEventStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    APPLIED = "APPLIED"
    IGNORED = "IGNORED"
    FAILED = "FAILED"


class BillingInterval(str, enum.Enum):
    MONTH = "MONTH"
    YEAR = "YEAR"


class SaaSPlan(Base, UUIDMixin, TimestampMixin):
    """Operator-owned plan definition. Billing providers reference this domain record."""

    __tablename__ = "saas_plans"
    __table_args__ = (
        Index("ix_saas_plan_active_public", "is_active", "is_public"),
    )

    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    entitlements: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    subscriptions: Mapped[list[TenantSubscription]] = relationship(
        "TenantSubscription",
        back_populates="plan",
    )
    prices: Mapped[list[SaaSPlanPrice]] = relationship(
        "SaaSPlanPrice",
        back_populates="plan",
        cascade="all, delete-orphan",
    )


class SaaSPlanPrice(Base, UUIDMixin, TimestampMixin):
    """Non-secret provider price reference for a recurring SaaS plan.

    Provider API keys remain environment/vault owned. Only public catalog identifiers and
    display-safe commercial metadata are persisted here.
    """

    __tablename__ = "saas_plan_prices"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_price_id",
            name="uq_saas_plan_price_provider_external",
        ),
        UniqueConstraint(
            "plan_id",
            "provider",
            "currency",
            "interval",
            "interval_count",
            name="uq_saas_plan_price_slot",
        ),
        CheckConstraint("unit_amount_minor >= 0", name="ck_saas_plan_price_amount_nonnegative"),
        CheckConstraint("interval_count >= 1", name="ck_saas_plan_price_interval_count_positive"),
        Index("ix_saas_plan_price_plan_active", "plan_id", "is_active"),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("saas_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_price_id: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    unit_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    interval: Mapped[BillingInterval] = mapped_column(
        Enum(BillingInterval, name="billing_interval_enum", native_enum=False),
        nullable=False,
    )
    interval_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    plan: Mapped[SaaSPlan] = relationship("SaaSPlan", back_populates="prices")


class TenantSubscription(Base, UUIDMixin, TimestampMixin):
    """One current commercial subscription record per tenant.

    Provider identifiers are opaque integration references. Entitlement overrides are
    explicit operator/billing-system exceptions layered on top of plan entitlements.
    """

    __tablename__ = "tenant_subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_subscription_tenant"),
        UniqueConstraint(
            "billing_provider",
            "external_subscription_id",
            name="uq_tenant_subscription_provider_external_id",
        ),
        Index("ix_tenant_subscription_plan_status", "plan_id", "status"),
        Index("ix_tenant_subscription_status_period_end", "status", "current_period_end"),
        Index("ix_tenant_subscription_provider_synced_at", "provider_synced_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("saas_plans.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status_enum", native_enum=False),
        default=SubscriptionStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    billing_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_sync_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider_sync_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    provider_sync_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    entitlement_overrides: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    billing_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    plan: Mapped[SaaSPlan] = relationship("SaaSPlan", back_populates="subscriptions")


class BillingEvent(Base, UUIDMixin, TimestampMixin):
    """Durable provider-agnostic billing event deduplication boundary.

    Raw provider payloads are intentionally not stored here. Provider adapters normalize
    signed webhook data and persist only an event fingerprint plus allowlisted metadata.
    """

    __tablename__ = "billing_events"
    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_billing_event_provider_external"),
        Index("ix_billing_event_status_created", "status", "created_at"),
        Index("ix_billing_event_tenant_created", "tenant_id", "created_at"),
    )

    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[BillingEventStatus] = mapped_column(
        Enum(BillingEventStatus, name="billing_event_status_enum", native_enum=False),
        default=BillingEventStatus.RECEIVED,
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenant_subscriptions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class PlatformAuditLog(Base, UUIDMixin, TimestampMixin):
    """Append-only control-plane audit evidence outside tenant RBAC."""

    __tablename__ = "platform_audit_logs"
    __table_args__ = (
        Index("ix_platform_audit_action_created", "action", "created_at"),
        Index("ix_platform_audit_tenant_created", "tenant_id", "created_at"),
    )

    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="LOCAL_PLATFORM_TOKEN")
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
