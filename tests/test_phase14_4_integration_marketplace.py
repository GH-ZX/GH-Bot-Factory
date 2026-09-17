from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.marketplace.models import TenantIntegrationEntitlement
from packages.providers.models import Provider, ProviderCategory, ProviderCredential
from packages.saas.models import PlatformAuditLog
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_PLATFORM_TOKEN = "test-platform-token-0123456789abcdef0123456789abcdef"
TEST_JWT_SECRET = "phase14-integration-jwt-secret-0123456789abcdef-0123456789abcdef"


@pytest_asyncio.fixture
async def client_env(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[dict[str, Any], None]:
    monkeypatch.setattr(settings, "platform_admin_token", TEST_PLATFORM_TOKEN)
    vault: dict[str, str] = {}
    storage = EnvSecretStorage(vault)
    monkeypatch.setattr("packages.providers.service.get_default_secret_storage", lambda: storage)
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    app.dependency_overrides[get_db_session] = lambda: db_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "session": db_session, "vault": vault}

    app.dependency_overrides.clear()


def platform_auth(token: str = TEST_PLATFORM_TOKEN) -> dict[str, str]:
    return {"X-GHBF-Platform-Token": token}


def user_auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_integration_marketplace_lifecycle_and_fail_closed_entitlement(
    client_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = client_env["client"]
    session: AsyncSession = client_env["session"]
    vault: dict[str, str] = client_env["vault"]

    tenant = Tenant(name="Marketplace Tenant", slug="mkt-tenant", is_active=True)
    session.add(tenant)
    await session.flush()

    user = User(telegram_id=987123654, username="mkt_owner", is_active=True)
    session.add(user)
    await session.flush()

    membership = Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER, is_active=True)
    session.add(membership)
    await session.commit()

    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    token = token_service.issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.OWNER],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )

    # 1. Platform Operator lists integrations catalog
    res_catalog = await client.get("/api/v1/platform/sales/integrations", headers=platform_auth())
    assert res_catalog.status_code == 200, res_catalog.text
    catalog = res_catalog.json()
    assert len(catalog) >= 6
    cat_keys = {it["key"] for it in catalog}
    assert "numbers-sms" in cat_keys
    assert "crypto-payments" in cat_keys
    assert "binance-pay" in cat_keys

    # 2. Tenant browses integrations (initially all LOCKED)
    res_tenant_list = await client.get("/api/v1/admin/integrations", headers=user_auth(token))
    assert res_tenant_list.status_code == 200, res_tenant_list.text
    tenant_items = res_tenant_list.json()
    sms_item = next(it for it in tenant_items if it["key"] == "numbers-sms")
    assert sms_item["is_entitled"] is False
    assert sms_item["status"] == "LOCKED"

    # 3. Unentitled configuration attempt fails closed with 403 Forbidden
    res_forbidden = await client.post(
        "/api/v1/admin/integrations/numbers-sms/configure",
        headers=user_auth(token),
        json={"api_key": "unentitled-key-attempt"},
    )
    assert res_forbidden.status_code == 403
    assert "not entitled" in res_forbidden.text.lower()

    # 4. Platform Operator grants entitlement to tenant
    res_grant = await client.post(
        f"/api/v1/platform/sales/tenants/{tenant.id}/integrations/numbers-sms/grant",
        headers=platform_auth(),
        json={"granted_by": "OPERATOR"},
    )
    assert res_grant.status_code == 200, res_grant.text
    grant_data = res_grant.json()
    assert grant_data["integration_key"] == "numbers-sms"
    assert grant_data["is_enabled"] is True

    # Verify platform audit log
    audit = (
        await session.execute(
            select(PlatformAuditLog).where(
                PlatformAuditLog.action == "integration_entitlement.granted",
                PlatformAuditLog.tenant_id == tenant.id,
            )
        )
    ).scalar_one_or_none()
    assert audit is not None

    # 5. Tenant now sees ENTITLED status
    res_tenant_entitled = await client.get("/api/v1/admin/integrations", headers=user_auth(token))
    assert res_tenant_entitled.status_code == 200
    sms_entitled = next(it for it in res_tenant_entitled.json() if it["key"] == "numbers-sms")
    assert sms_entitled["is_entitled"] is True
    assert sms_entitled["status"] == "ENTITLED"

    # 6. Tenant configures write-only API key
    res_config = await client.post(
        "/api/v1/admin/integrations/numbers-sms/configure",
        headers=user_auth(token),
        json={
            "api_key": "live-5sim-secret-token-abcdef",
            "display_name": "Primary 5sim Supplier",
        },
    )
    assert res_config.status_code == 200, res_config.text
    assert res_config.json()["status"] == "CONFIGURED"

    # Verify provider created with write-only SecretStorage credential
    provider = (
        await session.execute(
            select(Provider).where(
                Provider.tenant_id == tenant.id,
                Provider.category == ProviderCategory.NUMBER,
            )
        )
    ).scalar_one_or_none()
    assert provider is not None
    assert provider.name == "Primary 5sim Supplier"
    assert provider.is_enabled is True

    cred = (
        await session.execute(
            select(ProviderCredential).where(
                ProviderCredential.tenant_id == tenant.id,
                ProviderCredential.provider_id == provider.id,
            )
        )
    ).scalar_one_or_none()
    assert cred is not None
    assert cred.credential_type == "API_KEY"
    # Secret reference exists, but plaintext value is NOT stored in the database column
    assert cred.secret_ref.startswith("GHBF_PROVIDER_")
    assert "live-5sim-secret-token-abcdef" not in str(cred.__dict__)
    # Verify secret is recoverable from storage
    assert cred.secret_ref in vault
    assert vault[cred.secret_ref] == "live-5sim-secret-token-abcdef"

    # 7. Tenant now sees CONFIGURED status
    res_tenant_configured = await client.get("/api/v1/admin/integrations", headers=user_auth(token))
    sms_configured = next(it for it in res_tenant_configured.json() if it["key"] == "numbers-sms")
    assert sms_configured["is_configured"] is True
    assert sms_configured["status"] == "CONFIGURED"

    # 8. Platform Operator revokes entitlement
    res_revoke = await client.delete(
        f"/api/v1/platform/sales/tenants/{tenant.id}/integrations/numbers-sms",
        headers=platform_auth(),
    )
    assert res_revoke.status_code == 200
    assert res_revoke.json()["ok"] is True

    # Verify entitlement is revoked
    ent = (
        await session.execute(
            select(TenantIntegrationEntitlement).where(
                TenantIntegrationEntitlement.tenant_id == tenant.id,
                TenantIntegrationEntitlement.integration_key == "numbers-sms",
            )
        )
    ).scalar_one_or_none()
    assert ent is not None
    assert ent.is_enabled is False
