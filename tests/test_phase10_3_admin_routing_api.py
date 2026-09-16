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

_SECRET = "phase10-3-admin-routing-api-test-secret-0123456789abcdef"


async def _admin_token(session: AsyncSession, tenant: Tenant) -> str:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"phase103_{uuid.uuid4().hex[:8]}",
        first_name="Routing Admin",
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
async def test_admin_routing_policy_crud_and_tenant_isolation(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(name="Routing API", slug=f"routing-api-{uuid.uuid4().hex[:6]}")
    other = Tenant(name="Other Routing API", slug=f"routing-other-{uuid.uuid4().hex[:6]}")
    db_session.add_all([tenant, other])
    await db_session.flush()
    token = await _admin_token(db_session, tenant)

    product = Product(tenant_id=tenant.id, title="Own Product")
    other_product = Product(tenant_id=other.id, title="Other Product")
    provider_a = Provider(
        tenant_id=tenant.id,
        name="Provider A",
        slug=f"provider-a-{uuid.uuid4().hex[:5]}",
        provider_type="MOCK",
    )
    provider_b = Provider(
        tenant_id=tenant.id,
        name="Provider B",
        slug=f"provider-b-{uuid.uuid4().hex[:5]}",
        provider_type="MOCK",
    )
    other_provider = Provider(
        tenant_id=other.id,
        name="Other Provider",
        slug=f"other-provider-{uuid.uuid4().hex[:5]}",
        provider_type="MOCK",
    )
    db_session.add_all([product, other_product, provider_a, provider_b, other_provider])
    await db_session.flush()
    db_session.add_all(
        [
            ProviderProductMapping(
                tenant_id=tenant.id,
                provider_id=provider_a.id,
                product_id=product.id,
                external_product_id="a-product",
                cost_price=Decimal("1.00"),
            ),
            ProviderProductMapping(
                tenant_id=tenant.id,
                provider_id=provider_b.id,
                product_id=product.id,
                external_product_id="b-product",
                cost_price=Decimal("2.00"),
            ),
            ProviderProductMapping(
                tenant_id=other.id,
                provider_id=other_provider.id,
                product_id=other_product.id,
                external_product_id="other-product",
                cost_price=Decimal("3.00"),
            ),
        ]
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
            created = await client.put(
                "/api/v1/admin/provider-routing-policies",
                headers=headers,
                json={
                    "product_id": str(product.id),
                    "strategy": "WEIGHTED",
                    "weights": {
                        str(provider_a.id): 3,
                        str(provider_b.id): 7,
                    },
                    "failover_enabled": True,
                },
            )
            assert created.status_code == 200, created.text
            body = created.json()
            policy_id = body["id"]
            assert body["strategy"] == "WEIGHTED"
            assert body["weights"] == {
                str(provider_a.id): 3,
                str(provider_b.id): 7,
            }

            listed = await client.get(
                "/api/v1/admin/provider-routing-policies",
                headers=headers,
            )
            assert listed.status_code == 200, listed.text
            assert [item["id"] for item in listed.json()] == [policy_id]

            updated = await client.put(
                "/api/v1/admin/provider-routing-policies",
                headers=headers,
                json={
                    "product_id": str(product.id),
                    "strategy": "MANUAL",
                    "preferred_provider_id": str(provider_b.id),
                    "failover_enabled": False,
                },
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["id"] == policy_id
            assert updated.json()["preferred_provider_id"] == str(provider_b.id)
            assert updated.json()["failover_enabled"] is False

            cross_product = await client.put(
                "/api/v1/admin/provider-routing-policies",
                headers=headers,
                json={
                    "product_id": str(other_product.id),
                    "strategy": "PRIORITY",
                },
            )
            assert cross_product.status_code == 404
            assert str(other_product.id) not in cross_product.text

            cross_provider = await client.put(
                "/api/v1/admin/provider-routing-policies",
                headers=headers,
                json={
                    "product_id": str(product.id),
                    "strategy": "MANUAL",
                    "preferred_provider_id": str(other_provider.id),
                },
            )
            assert cross_provider.status_code == 422
            assert str(other_provider.id) not in cross_provider.text

            deleted = await client.delete(
                f"/api/v1/admin/provider-routing-policies/{policy_id}",
                headers=headers,
            )
            assert deleted.status_code == 204, deleted.text

            missing = await client.delete(
                f"/api/v1/admin/provider-routing-policies/{policy_id}",
                headers=headers,
            )
            assert missing.status_code == 404
    finally:
        app.dependency_overrides.clear()
