from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Product
from packages.providers.aggregation import ProviderOfferAggregator
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.exceptions import ProviderConfigurationError
from packages.providers.interface import ProviderProductDTO
from packages.providers.models import (
    Provider,
    ProviderOfferSnapshot,
    ProviderProductMapping,
    ProviderRoutingPolicy,
    ProviderRoutingStrategy,
)
from packages.providers.router import ProviderRouter
from packages.tenants.models import Tenant


class CatalogMock(MockProvider):
    def __init__(
        self,
        provider_name: str,
        *,
        external_id: str,
        cost: str,
        currency: str = "USD",
        available: bool = True,
        stock: int | None = 10,
    ) -> None:
        super().__init__(provider_name=provider_name)
        self.product = ProviderProductDTO(
            external_id=external_id,
            name=f"Offer {external_id}",
            cost=Decimal(cost),
            currency=currency,
            is_available=available,
            stock=stock,
        )

    async def list_products(self) -> list[ProviderProductDTO]:
        return [self.product]

    async def get_product(self, external_id: str) -> ProviderProductDTO:
        if external_id != self.product.external_id:
            return await super().get_product(external_id)
        return self.product


async def _fixture(
    session: AsyncSession,
) -> tuple[Tenant, Product, list[Provider], list[ProviderProductMapping]]:
    tenant = Tenant(name="Aggregation", slug=f"aggregation-{uuid.uuid4().hex[:8]}")
    session.add(tenant)
    await session.flush()
    product = Product(tenant_id=tenant.id, title="Aggregated Product")
    session.add(product)
    await session.flush()
    providers = [
        Provider(
            tenant_id=tenant.id,
            name="Supplier A",
            slug=f"supplier-a-{uuid.uuid4().hex[:5]}",
            provider_type="MOCK",
            priority=1,
        ),
        Provider(
            tenant_id=tenant.id,
            name="Supplier B",
            slug=f"supplier-b-{uuid.uuid4().hex[:5]}",
            provider_type="MOCK",
            priority=2,
        ),
    ]
    session.add_all(providers)
    await session.flush()
    mappings = [
        ProviderProductMapping(
            tenant_id=tenant.id,
            provider_id=providers[0].id,
            product_id=product.id,
            external_product_id="same-a",
            cost_price=Decimal("99.00"),
            cost_currency="USD",
        ),
        ProviderProductMapping(
            tenant_id=tenant.id,
            provider_id=providers[1].id,
            product_id=product.id,
            external_product_id="same-b",
            cost_price=Decimal("1.00"),
            cost_currency="USD",
        ),
    ]
    session.add_all(mappings)
    await session.flush()
    return tenant, product, providers, mappings


@pytest.mark.asyncio
async def test_offer_refresh_normalizes_live_cost_and_routing_uses_fresh_snapshot(
    db_session: AsyncSession,
) -> None:
    tenant, product, providers, mappings = await _fixture(db_session)
    registry = ProviderClientRegistry()
    registry.register_singleton(
        str(providers[0].id),
        CatalogMock("A", external_id="same-a", cost="0.80"),
    )
    registry.register_singleton(
        str(providers[1].id),
        CatalogMock("B", external_id="same-b", cost="1.20"),
    )
    db_session.add(
        ProviderRoutingPolicy(
            tenant_id=tenant.id,
            product_id=product.id,
            strategy=ProviderRoutingStrategy.LOWEST_COST,
        )
    )
    await db_session.flush()

    aggregator = ProviderOfferAggregator(registry=registry, freshness_seconds=300)
    result = await aggregator.refresh_product(
        db_session,
        tenant_id=tenant.id,
        product_id=product.id,
    )
    assert result.refreshed == 2
    assert result.failed == 0

    offers = await aggregator.list_offers(
        db_session,
        tenant_id=tenant.id,
        product_id=product.id,
    )
    assert [offer.cost_amount for offer in offers] == [Decimal("0.800000"), Decimal("1.200000")]

    router = ProviderRouter(registry=registry)
    eligible = await router.get_eligible_mappings(db_session, tenant.id, product.id)
    assert eligible[0].id == mappings[0].id


@pytest.mark.asyncio
async def test_fresh_unavailable_offer_is_filtered_but_stale_observation_is_advisory(
    db_session: AsyncSession,
) -> None:
    tenant, product, providers, mappings = await _fixture(db_session)
    registry = ProviderClientRegistry()
    registry.register_singleton(
        str(providers[0].id),
        CatalogMock("A", external_id="same-a", cost="0.80", available=False, stock=0),
    )
    registry.register_singleton(
        str(providers[1].id),
        CatalogMock("B", external_id="same-b", cost="1.20"),
    )
    aggregator = ProviderOfferAggregator(registry=registry, freshness_seconds=300)
    await aggregator.refresh_product(db_session, tenant_id=tenant.id, product_id=product.id)

    router = ProviderRouter(registry=registry)
    eligible = await router.get_eligible_mappings(db_session, tenant.id, product.id)
    assert [item.id for item in eligible] == [mappings[1].id]

    snapshot = await db_session.get(
        ProviderOfferSnapshot,
        next(
            offer.id
            for offer in await aggregator.list_offers(
                db_session, tenant_id=tenant.id, product_id=product.id
            )
            if offer.mapping_id == mappings[0].id
        ),
    )
    assert snapshot is not None
    snapshot.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    eligible_after_stale = await router.get_eligible_mappings(db_session, tenant.id, product.id)
    assert {item.id for item in eligible_after_stale} == {item.id for item in mappings}


@pytest.mark.asyncio
async def test_lowest_cost_never_compares_mixed_currencies_without_fx_normalization(
    db_session: AsyncSession,
) -> None:
    tenant, product, providers, _ = await _fixture(db_session)
    registry = ProviderClientRegistry()
    registry.register_singleton(
        str(providers[0].id),
        CatalogMock("A", external_id="same-a", cost="0.80", currency="USD"),
    )
    registry.register_singleton(
        str(providers[1].id),
        CatalogMock("B", external_id="same-b", cost="0.70", currency="EUR"),
    )
    db_session.add(
        ProviderRoutingPolicy(
            tenant_id=tenant.id,
            product_id=product.id,
            strategy=ProviderRoutingStrategy.LOWEST_COST,
        )
    )
    await db_session.flush()
    await ProviderOfferAggregator(registry=registry).refresh_product(
        db_session, tenant_id=tenant.id, product_id=product.id
    )

    with pytest.raises(ProviderConfigurationError, match="cost currency"):
        await ProviderRouter(registry=registry).get_eligible_mappings(
            db_session, tenant.id, product.id
        )
