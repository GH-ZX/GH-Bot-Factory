from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import case, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.commerce.economics_models import (
    CommercePriceQuote,
    MarkupMode,
    OrderItemEconomics,
    PricingRule,
    PricingScope,
    PricingTier,
    UserPricingTier,
)
from packages.commerce.models import Product, ProductVariant
from packages.factory.business_profiles import business_profile_from_config
from packages.providers.models import ProviderOfferSnapshot
from packages.telegram.models import Bot


@dataclass(frozen=True, slots=True)
class PriceDecision:
    sell_price: Decimal
    currency: str
    supplier_cost: Decimal | None
    supplier_currency: str | None
    offer_snapshot_id: uuid.UUID | None
    pricing_rule_id: uuid.UUID | None
    pricing_tier_id: uuid.UUID | None
    source: str


class PricingService:
    """Tenant-scoped pricing engine with explicit supplier-cost and tier contracts.

    Cross-currency supplier costs are never converted implicitly. If the fresh supplier
    observation currency differs from the catalog currency, the engine falls back to the
    authoritative catalog price unless/until an explicit commerce FX source is added.
    """

    def __init__(self, *, quote_ttl_seconds: int = 120) -> None:
        self.quote_ttl_seconds = max(15, min(int(quote_ttl_seconds), 900))

    async def resolve_tier(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        bot_id: uuid.UUID | None = None,
    ) -> PricingTier | None:
        assigned = await session.scalar(
            select(PricingTier)
            .join(UserPricingTier, UserPricingTier.tier_id == PricingTier.id)
            .where(
                UserPricingTier.tenant_id == tenant_id,
                UserPricingTier.user_id == user_id,
                PricingTier.tenant_id == tenant_id,
                PricingTier.is_active.is_(True),
            )
        )
        if assigned is not None:
            return assigned
        if bot_id is not None:
            bot = await session.get(Bot, bot_id)
            if bot is not None and bot.tenant_id == tenant_id and bot.deleted_at is None:
                profile = business_profile_from_config(bot.config)
                if profile.default_pricing_tier_id is not None:
                    bot_tier = await session.scalar(
                        select(PricingTier).where(
                            PricingTier.id == profile.default_pricing_tier_id,
                            PricingTier.tenant_id == tenant_id,
                            PricingTier.is_active.is_(True),
                        )
                    )
                    if bot_tier is not None:
                        return bot_tier
        return await session.scalar(
            select(PricingTier)
            .where(
                PricingTier.tenant_id == tenant_id,
                PricingTier.is_active.is_(True),
                PricingTier.is_default.is_(True),
            )
            .order_by(PricingTier.priority.asc(), PricingTier.id.asc())
        )

    async def _best_fresh_offer(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        variant: ProductVariant,
    ) -> ProviderOfferSnapshot | None:
        now = datetime.now(UTC)
        # SQLite can round-trip timezone-aware datetimes as naive. SQL comparison itself is
        # still safe because timestamps are written as UTC by contract.
        stmt = (
            select(ProviderOfferSnapshot)
            .where(
                ProviderOfferSnapshot.tenant_id == tenant_id,
                ProviderOfferSnapshot.product_id == variant.product_id,
                or_(
                    ProviderOfferSnapshot.product_variant_id == variant.id,
                    ProviderOfferSnapshot.product_variant_id.is_(None),
                ),
                ProviderOfferSnapshot.is_available.is_(True),
                ProviderOfferSnapshot.expires_at > now,
                ProviderOfferSnapshot.cost_currency == variant.currency,
            )
            .order_by(
                ProviderOfferSnapshot.cost_amount.asc(),
                ProviderOfferSnapshot.provider_id.asc(),
            )
        )
        return (await session.execute(stmt)).scalars().first()

    async def _resolve_rule(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        variant: ProductVariant,
        tier: PricingTier | None,
    ) -> PricingRule | None:
        product = variant.product
        tier_id = tier.id if tier is not None else None
        specificity = case(
            (PricingRule.scope == PricingScope.VARIANT, 0),
            (PricingRule.scope == PricingScope.PRODUCT, 1),
            (PricingRule.scope == PricingScope.CATEGORY, 2),
            else_=3,
        )
        stmt = (
            select(PricingRule)
            .where(
                PricingRule.tenant_id == tenant_id,
                PricingRule.is_active.is_(True),
                or_(PricingRule.tier_id == tier_id, PricingRule.tier_id.is_(None)),
                or_(
                    (PricingRule.scope == PricingScope.VARIANT)
                    & (PricingRule.product_variant_id == variant.id),
                    (PricingRule.scope == PricingScope.PRODUCT)
                    & (PricingRule.product_id == product.id),
                    (PricingRule.scope == PricingScope.CATEGORY)
                    & (PricingRule.category_id == product.category_id),
                    PricingRule.scope == PricingScope.GLOBAL,
                ),
            )
            .order_by(
                # tier-specific beats generic within the same scope
                specificity.asc(),
                (PricingRule.tier_id.is_(None)).asc(),
                PricingRule.priority.asc(),
                PricingRule.id.asc(),
            )
        )
        return (await session.execute(stmt)).scalars().first()

    @staticmethod
    def _round_up(value: Decimal, increment: Decimal) -> Decimal:
        if increment <= 0:
            raise ValueError("Pricing rounding increment must be positive.")
        units = (value / increment).to_integral_value(rounding=ROUND_CEILING)
        return units * increment

    @classmethod
    def apply_rule(cls, *, cost: Decimal, rule: PricingRule) -> Decimal:
        if cost < 0:
            raise ValueError("Supplier cost cannot be negative.")
        percent_component = cost * (rule.markup_percent / Decimal(100))
        if rule.markup_mode == MarkupMode.PERCENT:
            price = cost + percent_component
        elif rule.markup_mode == MarkupMode.FIXED:
            price = cost + rule.markup_fixed
        elif rule.markup_mode == MarkupMode.MIXED:
            price = cost + percent_component + rule.markup_fixed
        else:
            raise ValueError("Unsupported markup mode.")
        price = max(price, cost + rule.minimum_margin)
        return cls._round_up(price, rule.rounding_increment)

    async def decide(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        variant: ProductVariant,
        bot_id: uuid.UUID | None = None,
    ) -> PriceDecision:
        if "product" not in variant.__dict__:
            await session.refresh(variant, attribute_names=["product"])
        tier = await self.resolve_tier(
            session, tenant_id=tenant_id, user_id=user_id, bot_id=bot_id
        )
        rule = await self._resolve_rule(
            session,
            tenant_id=tenant_id,
            variant=variant,
            tier=tier,
        )
        offer = await self._best_fresh_offer(session, tenant_id=tenant_id, variant=variant)
        if rule is None or offer is None:
            return PriceDecision(
                sell_price=variant.price,
                currency=variant.currency,
                supplier_cost=offer.cost_amount if offer is not None else None,
                supplier_currency=offer.cost_currency if offer is not None else None,
                offer_snapshot_id=offer.id if offer is not None else None,
                pricing_rule_id=None,
                pricing_tier_id=tier.id if tier else None,
                source="CATALOG_FALLBACK",
            )
        price = self.apply_rule(cost=offer.cost_amount, rule=rule)
        return PriceDecision(
            sell_price=price,
            currency=variant.currency,
            supplier_cost=offer.cost_amount,
            supplier_currency=offer.cost_currency,
            offer_snapshot_id=offer.id,
            pricing_rule_id=rule.id,
            pricing_tier_id=tier.id if tier else None,
            source="SUPPLIER_MARKUP",
        )

    async def quote(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        variant: ProductVariant,
        bot_id: uuid.UUID | None = None,
    ) -> tuple[CommercePriceQuote, PriceDecision]:
        decision = await self.decide(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            variant=variant,
            bot_id=bot_id,
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=self.quote_ttl_seconds)
        payload = {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "variant_id": str(variant.id),
            "bot_id": str(bot_id or ""),
            "offer": str(decision.offer_snapshot_id or ""),
            "rule": str(decision.pricing_rule_id or ""),
            "tier": str(decision.pricing_tier_id or ""),
            "cost": str(decision.supplier_cost or ""),
            "cost_currency": decision.supplier_currency or "",
            "price": str(decision.sell_price),
            "currency": decision.currency,
            "expires_bucket": int(expires_at.timestamp()) // self.quote_ttl_seconds,
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        existing = await session.scalar(
            select(CommercePriceQuote).where(
                CommercePriceQuote.tenant_id == tenant_id,
                CommercePriceQuote.quote_fingerprint == fingerprint,
            )
        )
        if existing is not None:
            return existing, decision
        quote = CommercePriceQuote(
            tenant_id=tenant_id,
            user_id=user_id,
            product_variant_id=variant.id,
            provider_offer_snapshot_id=decision.offer_snapshot_id,
            pricing_rule_id=decision.pricing_rule_id,
            pricing_tier_id=decision.pricing_tier_id,
            supplier_cost=decision.supplier_cost,
            supplier_currency=decision.supplier_currency,
            sell_price=decision.sell_price,
            currency=decision.currency,
            quote_fingerprint=fingerprint,
            expires_at=expires_at,
        )
        try:
            async with session.begin_nested():
                session.add(quote)
                await session.flush()
        except IntegrityError:
            quote = await session.scalar(select(CommercePriceQuote).where(
                CommercePriceQuote.tenant_id == tenant_id,
                CommercePriceQuote.quote_fingerprint == fingerprint,
            ))
            if quote is None:
                raise
        return quote, decision

    @staticmethod
    async def attribute_actual_cost(
        session: AsyncSession,
        *,
        order_item_id: uuid.UUID,
        provider_id: uuid.UUID | None,
        actual_cost: Decimal,
        actual_currency: str,
    ) -> OrderItemEconomics | None:
        econ = await session.scalar(
            select(OrderItemEconomics).where(OrderItemEconomics.order_item_id == order_item_id)
        )
        if econ is None:
            return None
        econ.provider_id = provider_id
        econ.actual_supplier_cost = actual_cost
        econ.actual_cost_currency = actual_currency
        if actual_currency == econ.sale_currency:
            econ.gross_profit = econ.sale_amount - actual_cost
        else:
            econ.gross_profit = None
        econ.attributed_at = datetime.now(UTC)
        await session.flush()
        return econ


async def load_variants_for_pricing(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    variant_ids: list[uuid.UUID],
) -> list[ProductVariant]:
    return list(
        (
            await session.execute(
                select(ProductVariant)
                .join(Product, ProductVariant.product_id == Product.id)
                .where(
                    ProductVariant.id.in_(variant_ids),
                    Product.tenant_id == tenant_id,
                    ProductVariant.is_active.is_(True),
                    Product.is_active.is_(True),
                    Product.deleted_at.is_(None),
                    ProductVariant.deleted_at.is_(None),
                )
                .options(selectinload(ProductVariant.product))
            )
        ).scalars().all()
    )
