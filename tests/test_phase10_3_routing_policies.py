from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Product
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.exceptions import ProviderTimeoutError
from packages.providers.models import (
    Provider,
    ProviderHealthStatus,
    ProviderProductMapping,
    ProviderRoutingPolicy,
    ProviderRoutingStrategy,
)
from packages.providers.router import ProviderRouter
from packages.tenants.models import Tenant


async def _routing_fixture(
    session: AsyncSession,
) -> tuple[Tenant, Product, list[Provider], list[ProviderProductMapping]]:
    tenant = Tenant(name="Routing Policy", slug=f"routing-{uuid.uuid4().hex[:8]}")
    session.add(tenant)
    await session.flush()
    product = Product(tenant_id=tenant.id, title="Routing Product")
    session.add(product)
    await session.flush()
    providers = [
        Provider(
            tenant_id=tenant.id,
            name="Primary",
            slug=f"primary-{uuid.uuid4().hex[:4]}",
            provider_type="MOCK",
            priority=1,
            health_status=ProviderHealthStatus.HEALTHY,
            last_health_latency_ms=80,
        ),
        Provider(
            tenant_id=tenant.id,
            name="Cheap",
            slug=f"cheap-{uuid.uuid4().hex[:4]}",
            provider_type="MOCK",
            priority=2,
            health_status=ProviderHealthStatus.HEALTHY,
            last_health_latency_ms=120,
        ),
        Provider(
            tenant_id=tenant.id,
            name="Fast",
            slug=f"fast-{uuid.uuid4().hex[:4]}",
            provider_type="MOCK",
            priority=3,
            health_status=ProviderHealthStatus.HEALTHY,
            last_health_latency_ms=10,
        ),
    ]
    session.add_all(providers)
    await session.flush()
    mappings = [
        ProviderProductMapping(
            tenant_id=tenant.id,
            provider_id=providers[0].id,
            product_id=product.id,
            external_product_id="primary-product",
            cost_price=Decimal("5.00"),
        ),
        ProviderProductMapping(
            tenant_id=tenant.id,
            provider_id=providers[1].id,
            product_id=product.id,
            external_product_id="cheap-product",
            cost_price=Decimal("2.00"),
        ),
        ProviderProductMapping(
            tenant_id=tenant.id,
            provider_id=providers[2].id,
            product_id=product.id,
            external_product_id="fast-product",
            cost_price=Decimal("4.00"),
        ),
    ]
    session.add_all(mappings)
    await session.flush()
    return tenant, product, providers, mappings


@pytest.mark.asyncio
async def test_routing_strategies_order_eligible_mappings_deterministically(
    db_session: AsyncSession,
) -> None:
    tenant, product, providers, _ = await _routing_fixture(db_session)
    router = ProviderRouter()

    legacy = await router.get_eligible_mappings(db_session, tenant.id, product.id)
    assert [item.provider_id for item in legacy] == [provider.id for provider in providers]

    policy = ProviderRoutingPolicy(
        tenant_id=tenant.id,
        product_id=product.id,
        strategy=ProviderRoutingStrategy.LOWEST_COST,
    )
    db_session.add(policy)
    await db_session.flush()
    cheapest = await router.get_eligible_mappings(db_session, tenant.id, product.id)
    assert cheapest[0].provider_id == providers[1].id

    policy.strategy = ProviderRoutingStrategy.HEALTHIEST
    await db_session.flush()
    healthiest = await router.get_eligible_mappings(db_session, tenant.id, product.id)
    assert healthiest[0].provider_id == providers[2].id

    policy.strategy = ProviderRoutingStrategy.MANUAL
    policy.preferred_provider_id = providers[1].id
    policy.failover_enabled = False
    await db_session.flush()
    manual = await router.get_eligible_mappings(db_session, tenant.id, product.id)
    assert [item.provider_id for item in manual] == [providers[1].id]


@pytest.mark.asyncio
async def test_weighted_routing_is_stable_for_same_idempotency_key(
    db_session: AsyncSession,
) -> None:
    tenant, product, providers, _ = await _routing_fixture(db_session)
    db_session.add(
        ProviderRoutingPolicy(
            tenant_id=tenant.id,
            product_id=product.id,
            strategy=ProviderRoutingStrategy.WEIGHTED,
            weights_json={
                str(providers[0].id): 1,
                str(providers[1].id): 10,
                str(providers[2].id): 2,
            },
        )
    )
    await db_session.flush()
    router = ProviderRouter()
    first = await router.get_eligible_mappings(
        db_session,
        tenant.id,
        product.id,
        routing_key="order:stable:attempt:1",
    )
    replay = await router.get_eligible_mappings(
        db_session,
        tenant.id,
        product.id,
        routing_key="order:stable:attempt:1",
    )
    assert [item.provider_id for item in first] == [item.provider_id for item in replay]
    assert {item.provider_id for item in first} == {provider.id for provider in providers}


@pytest.mark.asyncio
async def test_timeout_ambiguity_never_fails_over_to_second_provider(
    db_session: AsyncSession,
) -> None:
    tenant, product, providers, _ = await _routing_fixture(db_session)
    registry = ProviderClientRegistry()
    ambiguous = MockProvider(provider_name="Primary")
    ambiguous.fail_with_timeout = True
    backup = MockProvider(provider_name="Cheap")
    registry.register_singleton(str(providers[0].id), ambiguous)
    registry.register_singleton(str(providers[1].id), backup)
    router = ProviderRouter(registry=registry)

    with pytest.raises(ProviderTimeoutError):
        await router.route_and_execute_order(
            session=db_session,
            tenant_id=tenant.id,
            product_id=product.id,
            quantity=1,
            recipient="buyer",
            idempotency_key="ambiguous-timeout-1",
        )
    assert backup.orders == {}


@pytest.mark.asyncio
async def test_safe_preorder_failure_still_uses_deterministic_fallback(
    db_session: AsyncSession,
) -> None:
    tenant, product, providers, _ = await _routing_fixture(db_session)
    registry = ProviderClientRegistry()
    throttled = MockProvider(provider_name="Primary")
    throttled.fail_with_rate_limit = True
    backup = MockProvider(provider_name="Cheap")
    registry.register_singleton(str(providers[0].id), throttled)
    registry.register_singleton(str(providers[1].id), backup)
    router = ProviderRouter(registry=registry)

    selected, _, response = await router.route_and_execute_order(
        session=db_session,
        tenant_id=tenant.id,
        product_id=product.id,
        quantity=1,
        recipient="buyer",
        idempotency_key="safe-fallback-1",
    )
    assert selected.id == providers[1].id
    assert response.is_success is True
