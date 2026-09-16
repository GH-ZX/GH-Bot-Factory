from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.commerce.models import Product
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.providers.models import Provider, ProviderProductMapping
from packages.tenants.models import Membership, Role, Tenant, User

_SECRET = "phase10-4-admin-offer-api-test-secret-0123456789abcdef"


async def _admin_token(session: AsyncSession, tenant: Tenant) -> str:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"phase104_{uuid.uuid4().hex[:8]}",
        first_name="Offer Admin",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    session.add(
        Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=Role.ADMIN,
            permissions=[],
            is_active=True,
        )
    )
    await session.flush()
    return AuthTokenService(secret_key=_SECRET).issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.ADMIN],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )


@pytest.mark.asyncio
async def test_offer_refresh_api_is_read_side_only_and_tenant_scoped(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(name="Offer API", slug=f"offer-api-{uuid.uuid4().hex[:6]}")
    other = Tenant(name="Other Offer API", slug=f"offer-other-{uuid.uuid4().hex[:6]}")
    db_session.add_all([tenant, other])
    await db_session.flush()
    token = await _admin_token(db_session, tenant)
    product = Product(tenant_id=tenant.id, title="Stars")
    other_product = Product(tenant_id=other.id, title="Other")
    provider = Provider(
        tenant_id=tenant.id,
        name="Mock Catalog",
        slug=f"mock-catalog-{uuid.uuid4().hex[:6]}",
        provider_type="MOCK",
    )
    db_session.add_all([product, other_product, provider])
    await db_session.flush()
    db_session.add(
        ProviderProductMapping(
            tenant_id=tenant.id,
            provider_id=provider.id,
            product_id=product.id,
            external_product_id="mock-prod-stars-50",
            cost_price=Decimal("9.99"),
            cost_currency="USD",
        )
    )
    await db_session.commit()

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: AuthTokenService(secret_key=_SECRET)
    headers = {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            refreshed = await client.post(
                "/api/v1/admin/provider-offers/refresh",
                headers=headers,
                json={"product_id": str(product.id)},
            )
            assert refreshed.status_code == 200, refreshed.text
            body = refreshed.json()
            assert body["refreshed"] == 1
            assert body["failed"] == 0
            assert body["offers"][0]["cost_amount"] == "1.250000"
            assert body["offers"][0]["is_fresh"] is True

            listed = await client.get(
                "/api/v1/admin/provider-offers",
                params={"product_id": str(product.id)},
                headers=headers,
            )
            assert listed.status_code == 200, listed.text
            assert len(listed.json()) == 1

            cross_tenant = await client.post(
                "/api/v1/admin/provider-offers/refresh",
                headers=headers,
                json={"product_id": str(other_product.id)},
            )
            assert cross_tenant.status_code == 404
            assert str(other_product.id) not in cross_tenant.text
    finally:
        app.dependency_overrides.clear()
