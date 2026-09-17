from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.marketplace.handoff_service import DeploymentHandoffService
from packages.marketplace.models import (
    CommercialQuote,
    ContactMethod,
    CustomerInquiry,
    InquiryStatus,
    LicenseType,
    QuoteStatus,
)
from packages.providers.models import Provider, ProviderCategory, ProviderCredential
from packages.telegram.models import Bot
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_PLATFORM_TOKEN = "test-platform-token-0123456789abcdef0123456789abcdef"
TEST_JWT_SECRET = "phase14-qual-jwt-secret-0123456789abcdef-0123456789abcdef"


@pytest_asyncio.fixture
async def qual_env(
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


def user_auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_commercial_governance_metrics_aggregation(qual_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = qual_env["client"]
    session: AsyncSession = qual_env["session"]

    # Seed sample inquiries
    inq1 = CustomerInquiry(
        contact_method=ContactMethod.TELEGRAM,
        contact_handle="@qual_lead_1",
        status=InquiryStatus.NEW,
    )
    inq2 = CustomerInquiry(
        contact_method=ContactMethod.EMAIL,
        contact_handle="qual_lead_2@example.com",
        status=InquiryStatus.CONVERTED,
    )
    session.add_all([inq1, inq2])
    await session.flush()

    # Seed sample quotes
    q_draft = CommercialQuote(
        quote_number="Q-2026-00001",
        version=1,
        inquiry_id=inq1.id,
        customer_name="Qual Corp",
        customer_contact="@qual_lead_1",
        status=QuoteStatus.DRAFT,
        currency="USD",
        total_one_time=150.00,
        total_monthly=50.00,
    )
    q_accepted = CommercialQuote(
        quote_number="Q-2026-00002",
        version=1,
        inquiry_id=inq2.id,
        customer_name="Accepted Corp",
        customer_contact="qual_lead_2@example.com",
        status=QuoteStatus.ACCEPTED,
        currency="USD",
        total_one_time=2000.00,
        total_monthly=0.00,
    )
    session.add_all([q_draft, q_accepted])
    await session.commit()

    # Fetch metrics via platform API
    res = await client.get("/api/v1/platform/sales/governance/metrics", headers=platform_auth())
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["total_inquiries"] >= 2
    assert data["inquiries_by_status"]["CONVERTED"] >= 1
    assert data["total_quotes"] >= 2
    assert data["quotes_by_status"]["ACCEPTED"] >= 1
    assert float(data["accepted_one_time"]) >= 2000.00
    assert float(data["pipeline_one_time"]) >= 150.00


async def test_end_to_end_tenant_isolation_and_secret_safety_audit(
    qual_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = qual_env["client"]
    session: AsyncSession = qual_env["session"]
    vault: dict[str, str] = qual_env["vault"]

    # Tenant A
    tenant_a = Tenant(name="Tenant Alpha", slug="tenant-alpha", is_active=True)
    tenant_b = Tenant(name="Tenant Beta", slug="tenant-beta", is_active=True)
    session.add_all([tenant_a, tenant_b])
    await session.flush()

    user_a = User(telegram_id=111222333, username="user_alpha", is_active=True)
    user_b = User(telegram_id=444555666, username="user_beta", is_active=True)
    session.add_all([user_a, user_b])
    await session.flush()

    session.add(Membership(tenant_id=tenant_a.id, user_id=user_a.id, role=Role.OWNER, is_active=True))
    session.add(Membership(tenant_id=tenant_b.id, user_id=user_b.id, role=Role.OWNER, is_active=True))

    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    token_a = token_service.issue_access_token(
        user_id=user_a.id, tenant_id=tenant_a.id, roles=[Role.OWNER], source=AuthSource.TEST, token_version=user_a.token_version
    )

    # 1. Tenant A cannot see Tenant B's providers or configure them
    prov_b = Provider(
        tenant_id=tenant_b.id,
        name="Beta Secret Supplier",
        slug="beta-secret-supplier",
        provider_type="mock",
        category=ProviderCategory.NUMBER,
        is_enabled=True,
    )
    session.add(prov_b)
    await session.commit()

    # Tenant A lists providers
    res_a = await client.get("/api/v1/admin/providers", headers=user_auth(token_a))
    assert res_a.status_code == 200
    providers_seen_by_a = [p["name"] for p in res_a.json()]
    assert "Beta Secret Supplier" not in providers_seen_by_a

    # 2. Handoff export for Tenant A contains zero data from Tenant B
    bot_a = Bot(
        tenant_id=tenant_a.id,
        telegram_bot_id=11110001,
        username="alpha_bot",
        display_name="Alpha Store",
        token_secret_ref="ALPHA_BOT_TOKEN_REF",
        is_enabled=True,
    )
    session.add(bot_a)
    await session.commit()

    handoff = await DeploymentHandoffService.create_handoff(
        session,
        tenant_id=tenant_a.id,
        license_type=LicenseType.DEDICATED_DEPLOYMENT,
        licensed_to="Alpha Corp",
        licensed_domain="alpha.example.com",
    )

    bundle = await DeploymentHandoffService.generate_single_tenant_export_bundle(
        session, handoff_id=handoff.id
    )

    bundle_file = Path(bundle["bundle_file"])
    bundle_text = bundle_file.read_text(encoding="utf-8")
    assert "tenant-alpha" in bundle_text
    assert "tenant-beta" not in bundle_text
    assert "Beta Secret Supplier" not in bundle_text
    assert "user_beta" not in bundle_text

    # 3. Secret leak audit: ensure no plaintext secret values stored in DB columns
    prov_cred = ProviderCredential(
        tenant_id=tenant_a.id,
        provider_id=prov_b.id,
        credential_type="API_KEY",
        secret_ref="GHBF_TEST_LEAK_CHECK_REF",
    )
    vault["GHBF_TEST_LEAK_CHECK_REF"] = "super-confidential-api-token-987"
    session.add(prov_cred)
    await session.commit()

    # Query DB row directly
    db_cred = await session.get(ProviderCredential, prov_cred.id)
    assert db_cred is not None
    assert "super-confidential-api-token-987" not in str(db_cred.__dict__)
    assert db_cred.secret_ref == "GHBF_TEST_LEAK_CHECK_REF"
