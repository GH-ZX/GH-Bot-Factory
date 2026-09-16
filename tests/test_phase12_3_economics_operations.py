from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_principal
from apps.api.main import app
from packages.commerce.economics_models import PricingTier
from packages.commerce.models import Product, ProductVariant
from packages.core.auth import AuthenticatedPrincipal, AuthSource
from packages.core.database import get_db_session
from packages.providers.balance_monitor import ProviderBalanceMonitor
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.models import (
    Provider,
    ProviderBalanceSnapshot,
    ProviderCategory,
)
from packages.tenants.models import Role, Tenant, User

pytestmark = pytest.mark.asyncio


async def _override_api(
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
) -> tuple[httpx.AsyncClient, dict[str, AuthenticatedPrincipal]]:
    holder = {"principal": principal}

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield session

    async def override_principal() -> AuthenticatedPrincipal:
        return holder["principal"]

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_principal] = override_principal
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ), holder


async def test_admin_economics_is_tenant_scoped_and_default_tier_is_unique(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(name="Economics A", slug=f"econ-a-{uuid.uuid4().hex[:8]}", is_active=True)
    other = Tenant(name="Economics B", slug=f"econ-b-{uuid.uuid4().hex[:8]}", is_active=True)
    admin = User(username=f"econ_admin_{uuid.uuid4().hex[:8]}", is_active=True)
    db_session.add_all([tenant, other, admin])
    await db_session.flush()

    foreign_product = Product(tenant_id=other.id, title="Foreign Product", is_active=True)
    db_session.add(foreign_product)
    await db_session.flush()
    foreign_variant = ProductVariant(
        product_id=foreign_product.id,
        sku=f"FOREIGN-{uuid.uuid4().hex[:6]}",
        title="Foreign Variant",
        price=Decimal("9.00"),
        currency="USD",
        is_active=True,
    )
    db_session.add(foreign_variant)
    await db_session.flush()

    db_session.add_all(
        [
            Provider(
                tenant_id=tenant.id,
                name="Tenant Provider",
                slug=f"tenant-provider-{uuid.uuid4().hex[:6]}",
                provider_type="MOCK",
                category=ProviderCategory.GIFT,
                is_enabled=True,
                metadata_json={},
            ),
            Provider(
                tenant_id=other.id,
                name="Foreign Provider",
                slug=f"foreign-provider-{uuid.uuid4().hex[:6]}",
                provider_type="MOCK",
                category=ProviderCategory.GIFT,
                is_enabled=True,
                metadata_json={},
            ),
        ]
    )
    await db_session.flush()
    providers = list((await db_session.execute(select(Provider))).scalars().all())
    own_provider = next(item for item in providers if item.tenant_id == tenant.id)
    foreign_provider = next(item for item in providers if item.tenant_id == other.id)
    db_session.add_all(
        [
            ProviderBalanceSnapshot(
                tenant_id=tenant.id,
                provider_id=own_provider.id,
                balance=Decimal("4.00"),
                currency="USD",
                low_balance_threshold=Decimal("5.00"),
                is_low_balance=True,
            ),
            ProviderBalanceSnapshot(
                tenant_id=other.id,
                provider_id=foreign_provider.id,
                balance=Decimal("999.00"),
                currency="USD",
                low_balance_threshold=Decimal("1.00"),
                is_low_balance=False,
            ),
        ]
    )
    await db_session.flush()

    principal = AuthenticatedPrincipal(
        user_id=admin.id,
        tenant_id=tenant.id,
        source=AuthSource.TEST,
        roles=frozenset({Role.ADMIN}),
    )
    client, _holder = await _override_api(db_session, principal)
    try:
        async with client:
            first = await client.post(
                "/api/v1/admin/economics/pricing-tiers",
                json={"code": "default", "display_name": "Default", "is_default": True},
            )
            assert first.status_code == 201, first.text

            duplicate_default = await client.post(
                "/api/v1/admin/economics/pricing-tiers",
                json={"code": "default-2", "display_name": "Default 2", "is_default": True},
            )
            assert duplicate_default.status_code == 409, duplicate_default.text

            foreign_scope = await client.post(
                "/api/v1/admin/economics/pricing-rules",
                json={
                    "name": "Foreign Variant Rule",
                    "scope": "VARIANT",
                    "product_variant_id": str(foreign_variant.id),
                    "markup_mode": "PERCENT",
                    "markup_percent": "10",
                },
            )
            assert foreign_scope.status_code == 404, foreign_scope.text

            balances = await client.get("/api/v1/admin/economics/provider-balances")
            assert balances.status_code == 200, balances.text
            body = balances.json()
            assert len(body) == 1
            assert body[0]["provider_id"] == str(own_provider.id)
            assert body[0]["is_low_balance"] is True
    finally:
        app.dependency_overrides.clear()

    # The uniqueness invariant is durable in persistence, not merely a UI convention.
    defaults = [
        item
        for item in (await db_session.execute(select(PricingTier))).scalars().all()
        if item.tenant_id == tenant.id and item.is_default and item.is_active
    ]
    assert len(defaults) == 1


async def test_provider_balance_monitor_is_read_only_and_respects_thresholds(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(name="Balance Tenant", slug=f"balance-{uuid.uuid4().hex[:8]}", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    provider = Provider(
        tenant_id=tenant.id,
        name="Balance Supplier",
        slug=f"balance-supplier-{uuid.uuid4().hex[:8]}",
        provider_type="MOCK",
        category=ProviderCategory.GIFT,
        is_enabled=True,
        metadata_json={"low_balance_threshold": "25.00", "low_balance_currency": "USD"},
    )
    db_session.add(provider)
    await db_session.flush()

    registry = ProviderClientRegistry()
    registry.register_singleton(
        str(provider.id),
        MockProvider(provider_name=provider.name, balance=Decimal("20.00")),
    )
    monitor = ProviderBalanceMonitor(registry=registry)

    snapshot = await monitor.refresh_provider(db_session, provider=provider)
    assert snapshot is not None
    assert snapshot.balance == Decimal("20.00")
    assert snapshot.currency == "USD"
    assert snapshot.low_balance_threshold == Decimal("25.00")
    assert snapshot.is_low_balance is True
    assert snapshot.last_error is None

    # Monitoring is observability only: it must never disable or mutate routing policy.
    await db_session.refresh(provider)
    assert provider.is_enabled is True

    provider.metadata_json = {"low_balance_threshold": "10.00", "low_balance_currency": "USD"}
    await db_session.flush()
    snapshot = await monitor.refresh_provider(db_session, provider=provider)
    assert snapshot is not None
    assert snapshot.is_low_balance is False
    assert provider.is_enabled is True
