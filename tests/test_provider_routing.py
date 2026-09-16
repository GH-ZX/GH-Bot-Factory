from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Product
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.exceptions import (
    ProviderAuthenticationError,
)
from packages.providers.models import (
    Provider,
    ProviderHealthStatus,
    ProviderProductMapping,
)
from packages.providers.router import ProviderRouter
from packages.tenants.models import Tenant


@pytest.mark.asyncio
async def test_provider_router_priority_selection(db_session: AsyncSession):
    tenant = Tenant(name="Router Store", slug="router-store")
    db_session.add(tenant)
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Test Item")
    db_session.add(product)
    await db_session.flush()

    # Create Provider 1 (Priority 1) and Provider 2 (Priority 2)
    p1 = Provider(tenant_id=tenant.id, name="Prov1", slug="p1", provider_type="MOCK", priority=1)
    p2 = Provider(tenant_id=tenant.id, name="Prov2", slug="p2", provider_type="MOCK", priority=2)
    db_session.add_all([p1, p2])
    await db_session.flush()

    m1 = ProviderProductMapping(
        tenant_id=tenant.id,
        provider_id=p1.id,
        product_id=product.id,
        external_product_id="ext-p1",
        cost_price=Decimal("10.00"),
    )
    m2 = ProviderProductMapping(
        tenant_id=tenant.id,
        provider_id=p2.id,
        product_id=product.id,
        external_product_id="ext-p2",
        cost_price=Decimal("12.00"),
    )
    db_session.add_all([m1, m2])
    await db_session.commit()

    # Custom registry injecting MockProviders
    registry = ProviderClientRegistry()
    client1 = MockProvider(provider_name="Prov1")
    client2 = MockProvider(provider_name="Prov2")
    registry.register_singleton(str(p1.id), client1)
    registry.register_singleton(str(p2.id), client2)

    router = ProviderRouter(registry=registry)
    prov, _mapping, resp = await router.route_and_execute_order(
        session=db_session,
        tenant_id=tenant.id,
        product_id=product.id,
        quantity=1,
        recipient="@buyer",
        idempotency_key="key-prio-1",
    )

    # Priority 1 should be selected
    assert prov.id == p1.id
    assert resp.is_success is True


@pytest.mark.asyncio
async def test_provider_router_fallback_on_retryable_error(db_session: AsyncSession):
    tenant = Tenant(name="Fallback Store", slug="fb-store")
    db_session.add(tenant)
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Fallback Item")
    db_session.add(product)
    await db_session.flush()

    p1 = Provider(tenant_id=tenant.id, name="FailingProv", slug="p1", provider_type="MOCK", priority=1)
    p2 = Provider(tenant_id=tenant.id, name="BackupProv", slug="p2", provider_type="MOCK", priority=2)
    db_session.add_all([p1, p2])
    await db_session.flush()

    m1 = ProviderProductMapping(tenant_id=tenant.id, provider_id=p1.id, product_id=product.id, external_product_id="ext-p1")
    m2 = ProviderProductMapping(tenant_id=tenant.id, provider_id=p2.id, product_id=product.id, external_product_id="ext-p2")
    db_session.add_all([m1, m2])
    await db_session.commit()

    registry = ProviderClientRegistry()
    client1 = MockProvider(provider_name="FailingProv")
    client1.fail_with_rate_limit = True  # Retryable and safe before upstream work is accepted.

    client2 = MockProvider(provider_name="BackupProv")

    registry.register_singleton(str(p1.id), client1)
    registry.register_singleton(str(p2.id), client2)

    router = ProviderRouter(registry=registry)
    prov, _mapping, resp = await router.route_and_execute_order(
        session=db_session,
        tenant_id=tenant.id,
        product_id=product.id,
        quantity=1,
        recipient="@buyer",
        idempotency_key="key-fb-1",
    )

    # Failed over to BackupProv (Priority 2)
    assert prov.id == p2.id
    assert resp.is_success is True

    # Check that p1 failure count was incremented
    await db_session.refresh(p1)
    assert p1.consecutive_failures == 1


@pytest.mark.asyncio
async def test_provider_router_halts_on_non_retryable_error(db_session: AsyncSession):
    tenant = Tenant(name="Halt Store", slug="halt-store")
    db_session.add(tenant)
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Halt Item")
    db_session.add(product)
    await db_session.flush()

    p1 = Provider(tenant_id=tenant.id, name="AuthFailProv", slug="p1", provider_type="MOCK", priority=1)
    p2 = Provider(tenant_id=tenant.id, name="BackupProv", slug="p2", provider_type="MOCK", priority=2)
    db_session.add_all([p1, p2])
    await db_session.flush()

    m1 = ProviderProductMapping(tenant_id=tenant.id, provider_id=p1.id, product_id=product.id, external_product_id="ext-p1")
    m2 = ProviderProductMapping(tenant_id=tenant.id, provider_id=p2.id, product_id=product.id, external_product_id="ext-p2")
    db_session.add_all([m1, m2])
    await db_session.commit()

    registry = ProviderClientRegistry()
    client1 = MockProvider(provider_name="AuthFailProv")
    client1.fail_with_auth_error = True  # Non-retryable!

    client2 = MockProvider(provider_name="BackupProv")
    registry.register_singleton(str(p1.id), client1)
    registry.register_singleton(str(p2.id), client2)

    router = ProviderRouter(registry=registry)

    # Should raise ProviderAuthenticationError immediately without attempting client2
    with pytest.raises(ProviderAuthenticationError):
        await router.route_and_execute_order(
            session=db_session,
            tenant_id=tenant.id,
            product_id=product.id,
            quantity=1,
            recipient="@buyer",
            idempotency_key="key-halt-1",
        )


@pytest.mark.asyncio
async def test_provider_router_skips_unavailable_provider(db_session: AsyncSession):
    tenant = Tenant(name="Unavail Store", slug="unavail-store")
    db_session.add(tenant)
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Unavail Item")
    db_session.add(product)
    await db_session.flush()

    # p1 is UNAVAILABLE
    p1 = Provider(
        tenant_id=tenant.id,
        name="DeadProv",
        slug="p1",
        provider_type="MOCK",
        priority=1,
        health_status=ProviderHealthStatus.UNAVAILABLE,
    )
    p2 = Provider(tenant_id=tenant.id, name="LiveProv", slug="p2", provider_type="MOCK", priority=2)
    db_session.add_all([p1, p2])
    await db_session.flush()

    m1 = ProviderProductMapping(tenant_id=tenant.id, provider_id=p1.id, product_id=product.id, external_product_id="ext-p1")
    m2 = ProviderProductMapping(tenant_id=tenant.id, provider_id=p2.id, product_id=product.id, external_product_id="ext-p2")
    db_session.add_all([m1, m2])
    await db_session.commit()

    router = ProviderRouter()
    eligible = await router.get_eligible_mappings(db_session, tenant.id, product.id)
    assert len(eligible) == 1
    assert eligible[0].provider_id == p2.id
