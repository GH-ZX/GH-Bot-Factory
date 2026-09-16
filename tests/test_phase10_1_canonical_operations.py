from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.providers.contracts import (
    NumberActivationState,
    NumberReservationRequest,
    ProviderOrderState,
    normalize_provider_order_state,
)
from packages.providers.models import Provider, ProviderCategory
from packages.providers.operations import ProviderOperationsService
from packages.tenants.models import Tenant


def test_order_state_normalization_is_vendor_neutral() -> None:
    assert normalize_provider_order_state("success") == ProviderOrderState.COMPLETED
    assert normalize_provider_order_state("in-progress") == ProviderOrderState.PROCESSING
    assert normalize_provider_order_state("canceled") == ProviderOrderState.CANCELLED
    assert normalize_provider_order_state("provider-specific-weird-state") == ProviderOrderState.UNKNOWN


@pytest.mark.asyncio
async def test_number_sandbox_canonical_lifecycle_is_idempotent(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Number Canonical", slug=f"number-{uuid.uuid4().hex[:8]}")
    db_session.add(tenant)
    await db_session.flush()
    provider = Provider(
        tenant_id=tenant.id,
        name="Number Sandbox",
        slug="number-sandbox",
        provider_type="MOCK",
        category=ProviderCategory.NUMBER,
    )
    db_session.add(provider)
    await db_session.flush()

    service = ProviderOperationsService()
    services = await service.list_number_services(
        db_session, tenant_id=tenant.id, provider_id=provider.id
    )
    countries = await service.list_number_countries(
        db_session,
        tenant_id=tenant.id,
        provider_id=provider.id,
        service="telegram",
    )
    offers = await service.list_number_offers(
        db_session,
        tenant_id=tenant.id,
        provider_id=provider.id,
        service="telegram",
        country="US",
    )
    assert {item.code for item in services} >= {"telegram", "whatsapp"}
    assert "US" in {item.code for item in countries}
    assert offers and offers[0].cost == Decimal("0.75")

    request = NumberReservationRequest(
        service="telegram",
        country="US",
        max_price=Decimal("1.00"),
        idempotency_key="number-idempotency-1",
    )
    first = await service.reserve_number(
        db_session,
        tenant_id=tenant.id,
        provider_id=provider.id,
        request=request,
    )
    replay = await service.reserve_number(
        db_session,
        tenant_id=tenant.id,
        provider_id=provider.id,
        request=request,
    )
    assert first.external_order_id == replay.external_order_id
    assert first.state == NumberActivationState.WAITING_SMS
    assert first.phone_number

    finished = await service.finish_number_activation(
        db_session,
        tenant_id=tenant.id,
        provider_id=provider.id,
        external_order_id=first.external_order_id,
    )
    assert finished.state == NumberActivationState.COMPLETED
    assert finished.messages and finished.messages[0].code == "123456"


@pytest.mark.asyncio
async def test_number_operations_are_tenant_and_category_scoped(db_session: AsyncSession) -> None:
    tenant_a = Tenant(name="Numbers A", slug=f"numbers-a-{uuid.uuid4().hex[:6]}")
    tenant_b = Tenant(name="Numbers B", slug=f"numbers-b-{uuid.uuid4().hex[:6]}")
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()
    number_provider = Provider(
        tenant_id=tenant_a.id,
        name="Tenant A Numbers",
        slug="tenant-a-numbers",
        provider_type="MOCK",
        category=ProviderCategory.NUMBER,
    )
    gift_provider = Provider(
        tenant_id=tenant_a.id,
        name="Tenant A Gifts",
        slug="tenant-a-gifts",
        provider_type="MOCK",
        category=ProviderCategory.GIFT,
    )
    db_session.add_all([number_provider, gift_provider])
    await db_session.flush()
    service = ProviderOperationsService()

    from packages.providers.exceptions import ProviderConfigurationError

    with pytest.raises(ProviderConfigurationError):
        await service.list_number_services(
            db_session,
            tenant_id=tenant_b.id,
            provider_id=number_provider.id,
        )
    with pytest.raises(ProviderConfigurationError):
        await service.list_number_services(
            db_session,
            tenant_id=tenant_a.id,
            provider_id=gift_provider.id,
        )
