from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.providers.models import Provider, ProviderCategory
from packages.tenants.models import Membership, Role, Tenant, User


async def _admin_token(session: AsyncSession, tenant: Tenant) -> str:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"phase102_{uuid.uuid4().hex[:8]}",
        first_name="Provider Admin",
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
    service = AuthTokenService(
        secret_key="phase10-2-admin-provider-api-test-secret-0123456789abcdef"
    )
    return service.issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.ADMIN],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )


@pytest.mark.asyncio
async def test_admin_openapi_inspection_and_number_discovery_are_tenant_safe(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(name="Provider API", slug=f"provider-api-{uuid.uuid4().hex[:6]}")
    other = Tenant(name="Other Provider API", slug=f"provider-other-{uuid.uuid4().hex[:6]}")
    db_session.add_all([tenant, other])
    await db_session.flush()
    token = await _admin_token(db_session, tenant)
    other_provider = Provider(
        tenant_id=other.id,
        name="Other Numbers",
        slug="other-numbers",
        provider_type="MOCK",
        category=ProviderCategory.NUMBER,
    )
    provider = Provider(
        tenant_id=tenant.id,
        name="Own Numbers",
        slug="own-numbers",
        provider_type="MOCK",
        category=ProviderCategory.NUMBER,
    )
    db_session.add_all([provider, other_provider])
    await db_session.commit()

    token_service = AuthTokenService(
        secret_key="phase10-2-admin-provider-api-test-secret-0123456789abcdef"
    )

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inspected = await client.post(
                "/api/v1/admin/provider-openapi/inspect",
                headers=headers,
                json={
                    "document": {
                        "swagger": "2.0",
                        "info": {"title": "Legacy Reseller", "version": "1"},
                        "paths": {
                            "/balance": {"get": {"operationId": "balance"}},
                            "/order": {"post": {"operationId": "order"}},
                        },
                    }
                },
            )
            assert inspected.status_code == 200, inspected.text
            assert {item["operation_id"] for item in inspected.json()["operations"]} == {
                "balance",
                "order",
            }

            services = await client.get(
                f"/api/v1/admin/providers/{provider.id}/number/services",
                headers=headers,
            )
            assert services.status_code == 200, services.text
            assert "telegram" in {item["code"] for item in services.json()}

            countries = await client.get(
                f"/api/v1/admin/providers/{provider.id}/number/countries",
                params={"service": "telegram"},
                headers=headers,
            )
            assert countries.status_code == 200
            assert "US" in {item["code"] for item in countries.json()}

            offers = await client.get(
                f"/api/v1/admin/providers/{provider.id}/number/offers",
                params={"service": "telegram", "country": "US"},
                headers=headers,
            )
            assert offers.status_code == 200
            assert offers.json()[0]["cost"] == "0.75"

            cross_tenant = await client.get(
                f"/api/v1/admin/providers/{other_provider.id}/number/services",
                headers=headers,
            )
            assert cross_tenant.status_code == 422
            assert str(other_provider.id) not in cross_tenant.text
    finally:
        app.dependency_overrides.clear()
