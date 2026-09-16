from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.economics import PricingService
from packages.commerce.economics_models import (
    MarkupMode,
    PricingRule,
    PricingScope,
    PricingTier,
    UserPricingTier,
)
from packages.commerce.models import Category, Product, ProductVariant
from packages.core.exceptions import InsufficientFundsError
from packages.payments.economics import WalletHoldService
from packages.payments.service import LedgerService
from packages.providers.models import (
    Provider,
    ProviderCategory,
    ProviderOfferSnapshot,
    ProviderProductMapping,
)
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


async def _tenant_user(session: AsyncSession, prefix: str) -> tuple[Tenant, User]:
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(name=f"{prefix} Tenant", slug=f"{prefix}-{suffix}", is_active=True)
    user = User(username=f"{prefix}_{suffix}", is_active=True)
    session.add_all([tenant, user])
    await session.flush()
    return tenant, user


async def _catalog_with_offer(
    session: AsyncSession,
    tenant: Tenant,
    *,
    catalog_price: Decimal = Decimal("99.00"),
    cost: Decimal = Decimal("10.00"),
    cost_currency: str = "USD",
) -> tuple[ProductVariant, ProviderOfferSnapshot]:
    category = Category(tenant_id=tenant.id, name="Games", slug=f"games-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(category)
    await session.flush()
    product = Product(
        tenant_id=tenant.id,
        category_id=category.id,
        title="Gift Code",
        is_active=True,
        metadata_json={},
    )
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku=f"SKU-{uuid.uuid4().hex[:6]}",
        title="10 USD",
        price=catalog_price,
        currency="USD",
        stock_quantity=100,
        is_active=True,
        attributes={},
    )
    provider = Provider(
        tenant_id=tenant.id,
        name="Supplier",
        slug=f"supplier-{uuid.uuid4().hex[:6]}",
        provider_type="mock",
        category=ProviderCategory.GIFT,
        is_enabled=True,
        priority=1,
        metadata_json={},
    )
    session.add_all([variant, provider])
    await session.flush()
    mapping = ProviderProductMapping(
        tenant_id=tenant.id,
        provider_id=provider.id,
        product_id=product.id,
        product_variant_id=variant.id,
        external_product_id=f"ext-{uuid.uuid4().hex[:8]}",
        is_enabled=True,
        cost_price=cost,
        cost_currency=cost_currency,
        provider_metadata={},
    )
    session.add(mapping)
    await session.flush()
    now = datetime.now(UTC)
    offer = ProviderOfferSnapshot(
        tenant_id=tenant.id,
        provider_id=provider.id,
        mapping_id=mapping.id,
        product_id=product.id,
        product_variant_id=variant.id,
        external_product_id=mapping.external_product_id,
        display_name=variant.title,
        cost_amount=cost,
        cost_currency=cost_currency,
        is_available=True,
        stock_quantity=50,
        min_quantity=1,
        max_quantity=10,
        observed_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    session.add(offer)
    await session.flush()
    await session.refresh(variant, attribute_names=["product"])
    return variant, offer


async def test_tier_specific_supplier_markup_is_authoritative(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session, "pricing")
    variant, offer = await _catalog_with_offer(db_session, tenant, cost=Decimal("10.00"))
    tier = PricingTier(
        tenant_id=tenant.id,
        code="vip",
        display_name="VIP",
        priority=10,
        is_default=False,
        is_active=True,
    )
    db_session.add(tier)
    await db_session.flush()
    db_session.add(UserPricingTier(tenant_id=tenant.id, user_id=user.id, tier_id=tier.id))
    db_session.add(
        PricingRule(
            tenant_id=tenant.id,
            name="VIP +15% + 0.50",
            scope=PricingScope.VARIANT,
            product_variant_id=variant.id,
            tier_id=tier.id,
            markup_mode=MarkupMode.MIXED,
            markup_percent=Decimal(15),
            markup_fixed=Decimal("0.50"),
            minimum_margin=Decimal("1.00"),
            rounding_increment=Decimal("0.05"),
            priority=1,
            is_active=True,
        )
    )
    await db_session.flush()

    quote, decision = await PricingService().quote(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        variant=variant,
    )

    assert decision.source == "SUPPLIER_MARKUP"
    assert decision.offer_snapshot_id == offer.id
    assert decision.supplier_cost == Decimal("10.000000")
    assert decision.sell_price == Decimal("12.00")
    assert quote.sell_price == Decimal("12.00")
    assert quote.supplier_cost == Decimal("10.000000")


async def test_cross_currency_offer_never_drives_price_without_explicit_fx(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session, "crossfx")
    variant, _offer = await _catalog_with_offer(
        db_session,
        tenant,
        catalog_price=Decimal("30.00"),
        cost=Decimal("8.00"),
        cost_currency="EUR",
    )
    db_session.add(
        PricingRule(
            tenant_id=tenant.id,
            name="Global 10%",
            scope=PricingScope.GLOBAL,
            markup_mode=MarkupMode.PERCENT,
            markup_percent=Decimal(10),
            markup_fixed=Decimal(0),
            minimum_margin=Decimal(0),
            rounding_increment=Decimal("0.01"),
            priority=1,
            is_active=True,
        )
    )
    await db_session.flush()

    decision = await PricingService().decide(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        variant=variant,
    )
    assert decision.source == "CATALOG_FALLBACK"
    assert decision.sell_price == Decimal("30.00")
    assert decision.supplier_cost is None


async def test_wallet_hold_reduces_available_balance_and_capture_is_exactly_once(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session, "holds")
    wallet = await LedgerService.get_or_create_wallet(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        currency="USD",
    )
    await LedgerService.credit(
        db_session,
        wallet,
        Decimal("100.00"),
        reference_type="TEST",
        reference_id="fund",
    )
    hold = await WalletHoldService.reserve(
        db_session,
        wallet=wallet,
        amount=Decimal("80.00"),
        idempotency_key="hold-001",
        reference_type="ORDER_AUTH",
        reference_id="order-1",
    )
    assert await WalletHoldService.available_balance(db_session, wallet=wallet) == Decimal("20.00")

    with pytest.raises(InsufficientFundsError):
        await LedgerService.debit(
            db_session,
            wallet,
            Decimal("20.01"),
            reference_type="OTHER",
            reference_id="too-much",
        )

    first = await WalletHoldService.capture(db_session, hold=hold)
    second = await WalletHoldService.capture(db_session, hold=hold)
    await db_session.refresh(wallet)
    assert first.id == second.id
    assert wallet.balance == Decimal("20.00")


async def test_hold_release_restores_spendable_balance(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session, "release")
    wallet = await LedgerService.get_or_create_wallet(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        currency="USD",
    )
    await LedgerService.credit(db_session, wallet, Decimal("50.00"), reference_type="TEST", reference_id="fund")
    hold = await WalletHoldService.reserve(
        db_session,
        wallet=wallet,
        amount=Decimal("40.00"),
        idempotency_key="hold-release-1",
        reference_type="ORDER_AUTH",
        reference_id="order-2",
    )
    await WalletHoldService.release(db_session, hold=hold)
    assert await WalletHoldService.available_balance(db_session, wallet=wallet) == Decimal("50.00")
