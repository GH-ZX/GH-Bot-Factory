from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.commerce.models import Category, Product
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.marketplace.models import (
    CommercialQuote,
    ContactMethod,
    CustomerInquiry,
    InquiryStatus,
    QuoteStatus,
)
from packages.payments.models import PaymentMethodConfig, PaymentMethodType, PaymentVerificationMode
from packages.saas.models import PlatformAuditLog
from packages.telegram.models import Bot
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_PLATFORM_TOKEN = "test-platform-token-0123456789abcdef0123456789abcdef"
TEST_JWT_SECRET = "phase14-onboard-jwt-secret-0123456789abcdef-0123456789abcdef"


@pytest_asyncio.fixture
async def client_env(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[dict[str, Any], None]:
    monkeypatch.setattr(settings, "platform_admin_token", TEST_PLATFORM_TOKEN)
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    app.dependency_overrides[get_db_session] = lambda: db_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "session": db_session}

    app.dependency_overrides.clear()


def platform_auth(token: str = TEST_PLATFORM_TOKEN) -> dict[str, str]:
    return {"X-GHBF-Platform-Token": token}


def user_auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_customer_onboarding_from_accepted_quote(client_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = client_env["client"]
    session: AsyncSession = client_env["session"]

    inquiry = CustomerInquiry(
        contact_method=ContactMethod.TELEGRAM,
        contact_handle="@acme_founder",
        project_notes="Acme digital software store",
        configuration={"format": "combo", "template_key": "digital-goods"},
        estimated_quote={"total_one_time": "89.00", "total_monthly": "49.00"},
        status=InquiryStatus.QUOTED,
    )
    session.add(inquiry)
    await session.commit()
    await session.refresh(inquiry)

    quote = CommercialQuote(
        quote_number="Q-2026-99991",
        version=1,
        inquiry_id=inquiry.id,
        customer_name="Acme Digital",
        customer_contact="@acme_founder",
        status=QuoteStatus.ACCEPTED,
        currency="USD",
        total_one_time=89.00,
        total_monthly=49.00,
    )
    session.add(quote)
    await session.commit()
    await session.refresh(quote)

    # Onboard tenant from quote
    payload = {
        "tenant_slug": "acme-digital-store",
        "tenant_name": "Acme Digital Store",
        "owner_username": "acme_founder",
    }
    res = await client.post(
        f"/api/v1/platform/sales/quotes/{quote.id}/onboard",
        headers=platform_auth(),
        json=payload,
    )
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["tenant_slug"] == "acme-digital-store"
    assert data["tenant_name"] == "Acme Digital Store"
    assert data["owner_username"] == "acme_founder"
    assert data["already_existed"] is False
    assert "/admin/?code=" in data["admin_launch_url"]

    tenant_id = uuid.UUID(data["tenant_id"])
    owner_id = uuid.UUID(data["owner_id"])
    bot_id = uuid.UUID(data["bot_id"])

    # Verify Database records
    tenant = await session.get(Tenant, tenant_id)
    assert tenant is not None
    assert tenant.slug == "acme-digital-store"
    assert tenant.is_active is True

    owner = await session.get(User, owner_id)
    assert owner is not None
    assert owner.username == "acme_founder"

    membership = (
        await session.execute(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == owner_id,
            )
        )
    ).scalar_one_or_none()
    assert membership is not None
    assert membership.role == Role.OWNER

    bot = await session.get(Bot, bot_id)
    assert bot is not None
    assert bot.tenant_id == tenant_id
    assert bot.display_name == "Acme Digital Store"

    # Verify quote linkage and inquiry conversion
    await session.refresh(quote)
    assert quote.tenant_id == tenant_id

    await session.refresh(inquiry)
    assert inquiry.status == InquiryStatus.CONVERTED

    # Verify platform audit log
    audit = (
        await session.execute(
            select(PlatformAuditLog).where(
                PlatformAuditLog.action == "tenant.onboarded_from_quote",
                PlatformAuditLog.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    assert audit is not None
    assert audit.details["quote_number"] == "Q-2026-99991"

    # Idempotent second call returns already_existed = True
    res_idempotent = await client.post(
        f"/api/v1/platform/sales/quotes/{quote.id}/onboard",
        headers=platform_auth(),
        json=payload,
    )
    assert res_idempotent.status_code == 201
    assert res_idempotent.json()["already_existed"] is True


async def test_tenant_admin_onboarding_checklist(client_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = client_env["client"]
    session: AsyncSession = client_env["session"]

    tenant = Tenant(name="Fresh Store", slug="fresh-store", is_active=True)
    session.add(tenant)
    await session.flush()

    user = User(telegram_id=123456789, username="store_owner", is_active=True)
    session.add(user)
    await session.flush()

    membership = Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER, is_active=True)
    session.add(membership)
    await session.flush()

    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    token = token_service.issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.OWNER],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )

    # 1. Initial checklist on fresh tenant
    res = await client.get("/api/v1/admin/onboarding/checklist", headers=user_auth(token))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["launch_ready"] is False
    assert data["progress_percent"] < 100
    assert len(data["items"]) == 5

    # 2. Add product & payment method & bot identity
    category = Category(tenant_id=tenant.id, name="Software", slug="software", is_active=True)
    session.add(category)
    await session.flush()

    product = Product(
        tenant_id=tenant.id,
        category_id=category.id,
        title="Antivirus Pro",
        is_active=True,
    )
    session.add(product)

    payment = PaymentMethodConfig(
        tenant_id=tenant.id,
        code="USDT_TRON",
        display_name="TRON USDT",
        method_type=PaymentMethodType.CRYPTO_GATEWAY,
        verification_mode=PaymentVerificationMode.MANUAL,
        is_enabled=True,
    )
    session.add(payment)

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=987654321,
        username="fresh_store_bot",
        display_name="Fresh Store Brand",
        token_secret_ref="FRESH_BOT_TOKEN",
        credential_status="VERIFIED",
        is_enabled=True,
    )
    session.add(bot)
    await session.commit()

    # 3. Check completed checklist
    res_ready = await client.get("/api/v1/admin/onboarding/checklist", headers=user_auth(token))
    assert res_ready.status_code == 200, res_ready.text
    data_ready = res_ready.json()
    assert data_ready["launch_ready"] is True
    assert data_ready["progress_percent"] == 100
    assert "launch-ready" in data_ready["next_step"].lower()
