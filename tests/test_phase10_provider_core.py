from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.providers.catalog import ProviderCapability
from packages.providers.clients.registry import ProviderClientRegistry, provider_registry
from packages.providers.exceptions import ProviderConfigurationError
from packages.providers.models import (
    Provider,
    ProviderCategory,
    ProviderCredential,
    ProviderHealthStatus,
)
from packages.providers.router import ProviderRouter
from packages.providers.service import (
    test_provider_connection as run_provider_connection_test,
)
from packages.providers.service import (
    upsert_provider_credential_value,
)
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Tenant


@pytest.mark.asyncio
async def test_provider_registry_exposes_capability_manifests_without_vendor_secrets() -> None:
    definitions = {definition.key: definition for definition in provider_registry.definitions()}
    mock = definitions["MOCK"]
    assert ProviderCategory.NUMBER in mock.categories
    assert ProviderCategory.ACCOUNT in mock.categories
    assert ProviderCategory.GIFT in mock.categories
    assert ProviderCapability.CREATE_ORDER in mock.capabilities
    assert ProviderCapability.ORDER_STATUS in mock.capabilities
    assert mock.driver_family == "sandbox"
    assert "secret" not in str(mock).lower()


@pytest.mark.asyncio
async def test_managed_provider_credential_persists_only_vault_reference(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Provider Vault", slug=f"provider-vault-{uuid.uuid4().hex[:6]}", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    provider = Provider(
        tenant_id=tenant.id,
        name="Vault Mock",
        slug="vault-mock",
        provider_type="MOCK",
        category=ProviderCategory.DIGITAL_PRODUCT,
        health_status=ProviderHealthStatus.UNKNOWN,
    )
    db_session.add(provider)
    await db_session.flush()

    memory: dict[str, str] = {}
    storage = EnvSecretStorage(memory)
    secret_value = "phase10-super-secret-provider-value"
    credential = await upsert_provider_credential_value(
        db_session,
        provider=provider,
        credential_type="API_KEY",
        secret_value=secret_value,
        secret_storage=storage,
    )
    await db_session.commit()

    assert credential.secret_ref.startswith(f"GHBF_PROVIDER_{tenant.id.hex}_{provider.id.hex}_")
    assert secret_value not in credential.secret_ref
    assert memory[credential.secret_ref] == secret_value

    stored = await db_session.scalar(
        select(ProviderCredential).where(ProviderCredential.provider_id == provider.id)
    )
    assert stored is not None
    assert stored.secret_ref == credential.secret_ref
    assert secret_value not in str(stored.__dict__)


@pytest.mark.asyncio
async def test_provider_adapter_category_mismatch_fails_before_runtime_call(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Category Guard", slug=f"category-{uuid.uuid4().hex[:6]}", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    provider = Provider(
        tenant_id=tenant.id,
        name="Wrong Category",
        slug="wrong-category",
        provider_type="DIGITAL_CODES",
        category=ProviderCategory.NUMBER,
    )
    db_session.add(provider)
    await db_session.flush()
    loaded = await db_session.scalar(
        select(Provider).where(Provider.id == provider.id).options(selectinload(Provider.credentials))
    )
    assert loaded is not None

    with pytest.raises(ProviderConfigurationError):
        await ProviderRouter().build_provider_config(loaded)


@pytest.mark.asyncio
async def test_provider_connection_test_updates_durable_health_evidence(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Connection Test", slug=f"connection-{uuid.uuid4().hex[:6]}", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    provider = Provider(
        tenant_id=tenant.id,
        name="Connection Mock",
        slug="connection-mock",
        provider_type="MOCK",
        category=ProviderCategory.NUMBER,
        health_status=ProviderHealthStatus.UNKNOWN,
        consecutive_failures=2,
    )
    db_session.add(provider)
    await db_session.flush()
    loaded = await db_session.scalar(
        select(Provider).where(Provider.id == provider.id).options(selectinload(Provider.credentials))
    )
    assert loaded is not None

    result = await run_provider_connection_test(
        db_session,
        provider=loaded,
        registry=ProviderClientRegistry(),
        secret_storage=EnvSecretStorage({}),
    )
    await db_session.commit()

    assert result.status == ProviderHealthStatus.HEALTHY
    assert result.balance is not None
    assert loaded.health_status == ProviderHealthStatus.HEALTHY
    assert loaded.consecutive_failures == 0
    assert loaded.last_health_check_at is not None
    assert loaded.last_health_latency_ms == pytest.approx(12.5)
    assert loaded.last_health_message == "Mock Provider operational"


async def _phase10_admin_identity(session: AsyncSession, tenant: Tenant) -> tuple[object, str]:
    from packages.core.auth import AuthSource, AuthTokenService
    from packages.tenants.models import Membership, Role, User

    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"phase10_{uuid.uuid4().hex[:8]}",
        first_name="Phase Ten",
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
    token = AuthTokenService(
        secret_key="phase10-provider-core-test-jwt-secret-0123456789abcdef"
    ).issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.ADMIN],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )
    return user, token


@pytest.mark.asyncio
async def test_phase10_admin_catalog_and_inline_vault_credential(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import AsyncGenerator

    import httpx

    from apps.api.deps import get_auth_token_service
    from apps.api.main import app
    from packages.core.auth import AuthTokenService
    from packages.core.database import get_db_session

    tenant = Tenant(name="Provider Admin", slug=f"provider-admin-{uuid.uuid4().hex[:6]}", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    _, token = await _phase10_admin_identity(db_session, tenant)
    await db_session.commit()

    vault: dict[str, str] = {}
    storage = EnvSecretStorage(vault)
    monkeypatch.setattr("packages.providers.service.get_default_secret_storage", lambda: storage)
    token_service = AuthTokenService(secret_key="phase10-provider-core-test-jwt-secret-0123456789abcdef")

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    headers = {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            catalog = await client.get("/api/v1/admin/provider-adapters", headers=headers)
            assert catalog.status_code == 200, catalog.text
            mock = next(item for item in catalog.json() if item["key"] == "MOCK")
            assert "NUMBER" in mock["categories"]
            assert "CREATE_ORDER" in mock["capabilities"]

            rejected = await client.post(
                "/api/v1/admin/providers",
                headers=headers,
                json={
                    "name": "Wrong Digital Codes",
                    "slug": "wrong-digital-codes",
                    "provider_type": "DIGITAL_CODES",
                    "category": "NUMBER",
                },
            )
            assert rejected.status_code == 422

            created = await client.post(
                "/api/v1/admin/providers",
                headers=headers,
                json={
                    "name": "Number Sandbox",
                    "slug": "number-sandbox",
                    "provider_type": "MOCK",
                    "category": "NUMBER",
                    "metadata": {"timeout_seconds": 10},
                },
            )
            assert created.status_code == 201, created.text
            provider_id = created.json()["id"]
            assert created.json()["category"] == "NUMBER"

            secret_value = "phase10-inline-secret-never-return"
            credential = await client.put(
                f"/api/v1/admin/providers/{provider_id}/credentials",
                headers=headers,
                json={"credential_type": "API_KEY", "secret_value": secret_value},
            )
            assert credential.status_code == 200, credential.text
            assert secret_value not in credential.text
            assert secret_value in vault.values()

            listing = await client.get("/api/v1/admin/providers", headers=headers)
            assert listing.status_code == 200
            assert secret_value not in listing.text
            row = next(item for item in listing.json() if item["id"] == provider_id)
            assert row["category"] == "NUMBER"
            assert "CREATE_ORDER" in row["capabilities"]
            assert row["credentials"] == [{"credential_type": "API_KEY", "configured": True}]
    finally:
        app.dependency_overrides.clear()
