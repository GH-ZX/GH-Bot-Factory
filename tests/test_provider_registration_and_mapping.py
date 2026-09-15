from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Product, ProductVariant
from packages.providers.models import (
    Provider,
    ProviderCredential,
    ProviderHealthStatus,
    ProviderProductMapping,
)
from packages.tenants.models import Tenant


@pytest.mark.asyncio
async def test_provider_registration_and_tenant_isolation(db_session: AsyncSession):
    tenant_a = Tenant(name="Tenant Alpha", slug="t-alpha")
    tenant_b = Tenant(name="Tenant Beta", slug="t-beta")
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()

    # 1. Create Provider for Tenant A
    prov_a = Provider(
        tenant_id=tenant_a.id,
        name="FastSMM",
        slug="fast-smm",
        provider_type="SMM",
        is_enabled=True,
        priority=1,
        health_status=ProviderHealthStatus.HEALTHY,
    )
    db_session.add(prov_a)
    await db_session.flush()

    # Add secret credential reference
    cred_a = ProviderCredential(
        tenant_id=tenant_a.id,
        provider_id=prov_a.id,
        credential_type="API_KEY",
        secret_ref="ENV_FASTSMM_API_KEY",
    )
    db_session.add(cred_a)
    await db_session.commit()

    # Query for Tenant B must never see Tenant A's provider
    stmt = select(Provider).where(Provider.tenant_id == tenant_b.id)
    res = await db_session.execute(stmt)
    assert res.scalar_one_or_none() is None

    # Query for Tenant A retrieves it
    stmt_a = select(Provider).where(Provider.tenant_id == tenant_a.id)
    res_a = await db_session.execute(stmt_a)
    retrieved = res_a.scalar_one_or_none()
    assert retrieved is not None
    assert retrieved.name == "FastSMM"


@pytest.mark.asyncio
async def test_duplicate_provider_slug_per_tenant_prevented(db_session: AsyncSession):
    tenant = Tenant(name="Single Tenant", slug="s-tenant")
    db_session.add(tenant)
    await db_session.flush()

    prov1 = Provider(
        tenant_id=tenant.id,
        name="Provider 1",
        slug="dup-slug",
        provider_type="MOCK",
    )
    db_session.add(prov1)
    await db_session.commit()

    prov2 = Provider(
        tenant_id=tenant.id,
        name="Provider 2",
        slug="dup-slug",  # Duplicate slug within same tenant
        provider_type="MOCK",
    )
    db_session.add(prov2)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_provider_product_mapping_multiple_providers(db_session: AsyncSession):
    tenant = Tenant(name="Commerce Tenant", slug="comm-tenant")
    db_session.add(tenant)
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Telegram Stars 50")
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(
        product_id=product.id,
        sku="STARS-50",
        title="50 Stars",
        price=Decimal("1.50"),
    )
    db_session.add(variant)
    await db_session.flush()

    # Provider 1 (Primary)
    p1 = Provider(tenant_id=tenant.id, name="Primary Vendor", slug="p1", provider_type="MOCK", priority=1)
    # Provider 2 (Fallback)
    p2 = Provider(tenant_id=tenant.id, name="Secondary Vendor", slug="p2", provider_type="MOCK", priority=2)
    db_session.add_all([p1, p2])
    await db_session.flush()

    # Map product to both providers with distinct costs
    m1 = ProviderProductMapping(
        tenant_id=tenant.id,
        provider_id=p1.id,
        product_id=product.id,
        product_variant_id=variant.id,
        external_product_id="vendor1-stars-50",
        cost_price=Decimal("1.10"),
        is_enabled=True,
    )
    m2 = ProviderProductMapping(
        tenant_id=tenant.id,
        provider_id=p2.id,
        product_id=product.id,
        product_variant_id=variant.id,
        external_product_id="vendor2-stars-50",
        cost_price=Decimal("1.25"),
        is_enabled=True,
    )
    db_session.add_all([m1, m2])
    await db_session.commit()

    stmt = select(ProviderProductMapping).where(
        ProviderProductMapping.tenant_id == tenant.id,
        ProviderProductMapping.product_id == product.id,
    )
    res = await db_session.execute(stmt)
    mappings = res.scalars().all()
    assert len(mappings) == 2
    ext_ids = {m.external_product_id for m in mappings}
    assert ext_ids == {"vendor1-stars-50", "vendor2-stars-50"}
