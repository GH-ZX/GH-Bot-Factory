from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
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


class PricingScope(str, enum.Enum):
    GLOBAL = "GLOBAL"
    CATEGORY = "CATEGORY"
    PRODUCT = "PRODUCT"
    VARIANT = "VARIANT"


class MarkupMode(str, enum.Enum):
    FIXED = "FIXED"
    PERCENT = "PERCENT"
    MIXED = "MIXED"


class PricingTier(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "pricing_tiers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_pricing_tier_tenant_code"),
        Index("ix_pricing_tier_tenant_active", "tenant_id", "is_active"),
        Index(
            "uq_pricing_tier_one_default",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_default = true AND is_active = true"),
            sqlite_where=text("is_default = 1 AND is_active = 1"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UserPricingTier(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_pricing_tiers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_user_pricing_tier"),
        Index("ix_user_pricing_tier_tenant", "tenant_id", "tier_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pricing_tiers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tier: Mapped[PricingTier] = relationship("PricingTier")


class PricingRule(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "pricing_rules"
    __table_args__ = (
        Index("ix_pricing_rule_tenant_active", "tenant_id", "is_active", "priority"),
        Index("ix_pricing_rule_variant", "tenant_id", "product_variant_id", "tier_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    scope: Mapped[PricingScope] = mapped_column(
        Enum(PricingScope, name="pricing_scope_enum", native_enum=False), nullable=False
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), nullable=True, index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True
    )
    product_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    tier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pricing_tiers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    markup_mode: Mapped[MarkupMode] = mapped_column(
        Enum(MarkupMode, name="markup_mode_enum", native_enum=False), nullable=False
    )
    markup_percent: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal(0))
    markup_fixed: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal(0))
    minimum_margin: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal(0))
    rounding_increment: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0.01"))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    tier: Mapped[PricingTier | None] = relationship("PricingTier")


class CommercePriceQuote(Base, UUIDMixin, TimestampMixin):
    """Immutable customer-price quote derived from supplier observation + pricing rule."""

    __tablename__ = "commerce_price_quotes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "quote_fingerprint", name="uq_commerce_price_quote_fingerprint"),
        Index("ix_commerce_price_quote_user_variant", "tenant_id", "user_id", "product_variant_id"),
        Index("ix_commerce_price_quote_expires", "tenant_id", "expires_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_offer_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_offer_snapshots.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pricing_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pricing_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pricing_tier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pricing_tiers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    supplier_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    supplier_currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    sell_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    quote_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderItemEconomics(Base, UUIDMixin, TimestampMixin):
    """Immutable-at-sale economics plus actual provider cost attribution after fulfillment."""

    __tablename__ = "order_item_economics"
    __table_args__ = (
        UniqueConstraint("order_item_id", name="uq_order_item_economics_item"),
        Index("ix_order_economics_tenant_provider", "tenant_id", "provider_id"),
        Index("ix_order_economics_bot", "tenant_id", "bot_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bots.id", ondelete="SET NULL"), nullable=True, index=True
    )
    price_quote_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("commerce_price_quotes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pricing_tier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pricing_tiers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pricing_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pricing_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sale_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    sale_currency: Mapped[str] = mapped_column(String(12), nullable=False)
    estimated_supplier_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    estimated_cost_currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    actual_supplier_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    actual_cost_currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    gross_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    attributed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
