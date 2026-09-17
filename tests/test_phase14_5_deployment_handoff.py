from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.commerce.models import Category, Product, ProductVariant
from packages.core.auth import AuthTokenService
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.marketplace.models import CommercialQuote, QuoteStatus
from packages.providers.models import Provider, ProviderCategory, ProviderCredential
from packages.saas.models import PlatformAuditLog
from packages.telegram.models import Bot
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_PLATFORM_TOKEN = "test-platform-token-0123456789abcdef0123456789abcdef"
TEST_JWT_SECRET = "phase14-handoff-jwt-secret-0123456789abcdef-0123456789abcdef"


@pytest_asyncio.fixture
async def client_env(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[dict[str, Any], None]:
    monkeypatch.setattr(settings, "platform_admin_token", TEST_PLATFORM_TOKEN)
    vault: dict[str, str] = {}
    storage = EnvSecretStorage(vault)
    monkeypatch.setattr("packages.providers.service.get_default_secret_storage", lambda: storage)
    monkeypatch.setattr("packages.marketplace.handoff_service.get_default_secret_storage", lambda: storage)
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    app.dependency_overrides[get_db_session] = lambda: db_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "session": db_session, "vault": vault}

    app.dependency_overrides.clear()


def platform_auth(token: str = TEST_PLATFORM_TOKEN) -> dict[str, str]:
    return {"X-GHBF-Platform-Token": token}


async def test_dedicated_deployment_handoff_and_runtime_deactivation(
    client_env: dict[str, Any],
    tmp_path: Path,
) -> None:
    client: httpx.AsyncClient = client_env["client"]
    session: AsyncSession = client_env["session"]
    vault: dict[str, str] = client_env["vault"]

    # 1. Seed tenant with catalog, bot, and provider credentials
    tenant = Tenant(name="Dedicated Client Corp", slug="dedicated-client", is_active=True)
    other_tenant = Tenant(name="Other Client Corp", slug="other-client", is_active=True)
    session.add_all([tenant, other_tenant])
    await session.flush()

    user = User(telegram_id=888777666, username="dedicated_owner", is_active=True)
    session.add(user)
    await session.flush()

    membership = Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER, is_active=True)
    session.add(membership)

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=777666555,
        username="dedicated_bot",
        display_name="Dedicated Store",
        token_secret_ref="DEDICATED_BOT_TOKEN_REF",
        is_enabled=True,
        runtime_revision=1,
    )
    session.add(bot)

    category = Category(tenant_id=tenant.id, name="Digital Codes", slug="digital-codes", is_active=True)
    session.add(category)
    await session.flush()

    product = Product(tenant_id=tenant.id, category_id=category.id, title="Pro License", is_active=True)
    session.add(product)
    await session.flush()

    variant = ProductVariant(
        product_id=product.id,
        sku="PRO-LIC-001",
        title="1-Year License",
        price=99.00,
        currency="USD",
        stock_quantity=50,
        is_active=True,
    )
    session.add(variant)

    provider = Provider(
        tenant_id=tenant.id,
        name="Dedicated 5sim",
        slug="dedicated-5sim",
        provider_type="mock",
        category=ProviderCategory.NUMBER,
        priority=10,
        is_enabled=True,
    )
    session.add(provider)
    await session.flush()

    # Store credential in secret storage
    secret_ref = f"GHBF_PROVIDER_{tenant.id.hex}_{provider.id.hex}_API_KEY"
    vault[secret_ref] = "super-secret-provider-key-999"
    cred = ProviderCredential(
        tenant_id=tenant.id,
        provider_id=provider.id,
        credential_type="API_KEY",
        secret_ref=secret_ref,
    )
    session.add(cred)

    quote = CommercialQuote(
        quote_number="Q-2026-88888",
        version=1,
        tenant_id=tenant.id,
        customer_name="Dedicated Client Corp",
        customer_contact="@dedicated_owner",
        status=QuoteStatus.ACCEPTED,
        currency="USD",
        total_one_time=2000.00,
        total_monthly=0.00,
    )
    session.add(quote)
    await session.commit()

    # 2. Create Deployment Handoff record
    handoff_payload = {
        "license_type": "SOURCE_LICENSE",
        "licensed_to": "Dedicated Client Corp",
        "licensed_domain": "store.dedicatedclient.com",
        "support_plan": "Enterprise 24/7 SLA",
        "quote_id": str(quote.id),
        "handoff_notes": "Perpetual source code buyout with standalone docker compose package.",
    }
    res_create = await client.post(
        f"/api/v1/platform/sales/tenants/{tenant.id}/handoffs",
        headers=platform_auth(),
        json=handoff_payload,
    )
    assert res_create.status_code == 201, res_create.text
    h_data = res_create.json()

    handoff_id = uuid.UUID(h_data["id"])
    license_key = h_data["license_key"]
    assert license_key.startswith("LIC-GHBF-")
    assert h_data["license_type"] == "SOURCE_LICENSE"
    assert h_data["status"] == "PREPARING"
    assert h_data["runtime_deactivated"] is False

    # Verify audit log
    audit_create = (
        await session.execute(
            select(PlatformAuditLog).where(
                PlatformAuditLog.action == "handoff.created",
                PlatformAuditLog.resource_id == license_key,
            )
        )
    ).scalar_one_or_none()
    assert audit_create is not None

    # 3. Generate Single-Tenant Sanitized Bundle
    res_bundle = await client.post(
        f"/api/v1/platform/sales/handoffs/{handoff_id}/generate-bundle",
        headers=platform_auth(),
    )
    assert res_bundle.status_code == 200, res_bundle.text
    bundle_meta = res_bundle.json()

    bundle_file = Path(bundle_meta["bundle_file"])
    assert bundle_file.exists()
    bundle_json = json.loads(bundle_file.read_text(encoding="utf-8"))

    # Assert strict single-tenant isolation
    assert bundle_json["tenant"]["slug"] == "dedicated-client"
    assert "other-client" not in str(bundle_json)
    assert bundle_json["license"]["key"] == license_key
    assert bundle_json["license"]["type"] == "SOURCE_LICENSE"
    assert len(bundle_json["catalog"]["products"]) == 1
    assert bundle_json["catalog"]["products"][0]["title"] == "Pro License"
    assert bundle_json["provider_credentials"]["API_KEY"] == "super-secret-provider-key-999"

    # Verify manifest and standalone compose file
    target_dir = Path(bundle_meta["artifact_dir"])
    manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["license_key"] == license_key
    assert manifest["checksum_sha256"] == bundle_meta["checksum_sha256"]
    assert (target_dir / "docker-compose.standalone.yml").exists()

    # 4. Deactivate Managed Runtime before customer cutover
    res_deact = await client.post(
        f"/api/v1/platform/sales/handoffs/{handoff_id}/deactivate-managed",
        headers=platform_auth(),
    )
    assert res_deact.status_code == 200, res_deact.text
    deact_data = res_deact.json()
    assert deact_data["runtime_deactivated"] is True
    assert deact_data["status"] == "HANDED_OFF"
    assert deact_data["handed_off_at"] is not None

    # Verify bot was disabled on managed factory
    await session.refresh(bot)
    assert bot.is_enabled is False
    assert bot.runtime_revision == 2

    # Verify audit log
    audit_deact = (
        await session.execute(
            select(PlatformAuditLog).where(
                PlatformAuditLog.action == "handoff.runtime_deactivated",
                PlatformAuditLog.resource_id == license_key,
            )
        )
    ).scalar_one_or_none()
    assert audit_deact is not None
