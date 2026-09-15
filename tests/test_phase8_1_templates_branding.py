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
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.factory.models import BotProvisioningJob
from packages.telegram.models import Bot
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase8-template-jwt-secret-0123456789abcdef-0123456789abcdef"


async def create_identity(
    session: AsyncSession,
    tenant: Tenant,
    *,
    role: Role,
    bot_id: uuid.UUID | None = None,
) -> tuple[User, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"{role.value.lower()}_{uuid.uuid4().hex[:8]}",
        first_name="Template",
        last_name="Tester",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    session.add(
        Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            permissions=[],
            is_active=True,
        )
    )
    await session.flush()
    token = AuthTokenService(secret_key=TEST_JWT_SECRET).issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[role],
        source=AuthSource.TEST,
        token_version=user.token_version,
        extra_claims={"bot_id": str(bot_id)} if bot_id else None,
    )
    return user, token


@pytest_asyncio.fixture
async def client_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "session": db_session}
    app.dependency_overrides.clear()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_template_catalog_and_provisioning_resolve_server_side_config(
    client_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = client_env["client"]
    session: AsyncSession = client_env["session"]
    tenant = Tenant(name="Template Tenant", slug="template-tenant", is_active=True)
    session.add(tenant)
    await session.flush()
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)

    templates = await client.get("/api/v1/admin/bots/templates", headers=auth(admin_token))
    assert templates.status_code == 200
    rows = templates.json()["templates"]
    assert {row["key"] for row in rows} >= {"general-commerce", "digital-goods", "gaming-store", "services"}
    assert all(row["version"] == 1 for row in rows)
    assert "secret" not in str(rows).lower()

    payload = {
        "token_secret_ref": "TEMPLATE_BOT_TOKEN",
        "expected_username": "template_shop_bot",
        "display_name": "Neon Shop",
        "template_key": "gaming-store",
        "template_version": 1,
        "currency": "XTR",
        "locale": "de",
        "branding": {
            "brand_accent": "#1122AA",
            "brand_logo_url": "https://cdn.example.com/neon.png",
            "store_tagline": "Gaming delivered fast.",
            "support_contact": "@neon_support",
            "menu_text": "Shop now",
        },
        "enabled_modules": ["catalog", "orders"],
    }
    response = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "template-provision-001"},
        json=payload,
    )
    assert response.status_code == 202, response.text
    job_id = uuid.UUID(response.json()["id"])
    job = await session.get(BotProvisioningJob, job_id)
    assert job is not None
    assert job.desired_config["_factory"] == {"template_key": "gaming-store", "template_version": 1}
    assert job.desired_config["currency"] == "XTR"
    assert job.desired_config["locale"] == "de"
    assert job.desired_config["branding"]["brand_accent"] == "#1122AA"
    assert job.desired_config["enabled_modules"] == ["catalog", "orders"]

    invalid = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "template-provision-002"},
        json={**payload, "branding": {"brand_logo_url": "http://unsafe.example/logo.png"}},
    )
    assert invalid.status_code == 422


async def test_bot_configuration_is_tenant_scoped_audited_and_credential_preserving(
    client_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = client_env["client"]
    session: AsyncSession = client_env["session"]
    tenant = Tenant(name="Brand Tenant", slug="brand-tenant", is_active=True)
    other = Tenant(name="Other", slug="brand-other", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)
    _, staff_token = await create_identity(session, tenant, role=Role.STAFF)
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=8110001,
        username="brand_bot",
        display_name="Old Brand",
        token_secret_ref="IMMUTABLE_TOKEN_REF",
        is_enabled=True,
        config={"locale": "en"},
    )
    foreign = Bot(
        tenant_id=other.id,
        telegram_bot_id=8110002,
        username="foreign_brand_bot",
        display_name="Foreign",
        token_secret_ref="FOREIGN_REF",
        is_enabled=True,
        config={},
    )
    session.add_all([bot, foreign])
    await session.flush()

    body = {
        "display_name": "Updated Brand",
        "template_key": "digital-goods",
        "template_version": 1,
        "currency": "USD",
        "locale": "en",
        "branding": {
            "brand_accent": "#00AA88",
            "store_tagline": "Instant access.",
            "support_url": "https://example.com/support",
            "menu_text": "Open shop",
        },
        "enabled_modules": ["catalog", "orders", "account"],
    }
    denied = await client.patch(
        f"/api/v1/admin/bots/{bot.id}/configuration", headers=auth(staff_token), json=body
    )
    assert denied.status_code == 403
    foreign_response = await client.patch(
        f"/api/v1/admin/bots/{foreign.id}/configuration", headers=auth(admin_token), json=body
    )
    assert foreign_response.status_code == 404

    updated = await client.patch(
        f"/api/v1/admin/bots/{bot.id}/configuration", headers=auth(admin_token), json=body
    )
    assert updated.status_code == 200, updated.text
    data = updated.json()
    assert data["display_name"] == "Updated Brand"
    assert data["template_key"] == "digital-goods"
    assert data["template_version"] == 1
    assert "token_secret_ref" not in data

    await session.refresh(bot)
    assert bot.token_secret_ref == "IMMUTABLE_TOKEN_REF"
    assert bot.config["_factory"]["template_key"] == "digital-goods"
    audit = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant.id,
                AuditLog.action == "BOT_CONFIGURATION_UPDATED",
            )
        )
    ).scalar_one()
    assert audit.details["template"] == "digital-goods"
    assert "IMMUTABLE_TOKEN_REF" not in str(audit.details)


async def test_storefront_bootstrap_uses_verified_bot_branding_not_tenant_default(
    client_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = client_env["client"]
    session: AsyncSession = client_env["session"]
    tenant = Tenant(
        name="Tenant Default",
        slug="bot-branded-store",
        is_active=True,
        settings={
            "brand_accent": "#111111",
            "store_tagline": "Tenant fallback",
            "internal_setting": "hidden",
        },
    )
    session.add(tenant)
    await session.flush()
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=8110003,
        username="branded_bot",
        display_name="Bot Specific Store",
        token_secret_ref="BOT_REF",
        is_enabled=True,
        config={
            "branding": {
                "brand_accent": "#ABCDEF",
                "brand_logo_url": "https://cdn.example.com/logo.png",
                "store_tagline": "Bot-specific tagline",
            },
            "_factory": {"template_key": "general-commerce", "template_version": 1},
        },
    )
    session.add(bot)
    await session.flush()
    _, token = await create_identity(session, tenant, role=Role.CUSTOMER, bot_id=bot.id)

    response = await client.get("/api/v1/storefront/bootstrap", headers=auth(token))
    assert response.status_code == 200, response.text
    store = response.json()["store"]
    assert store["name"] == "Bot Specific Store"
    assert store["settings"]["brand_accent"] == "#ABCDEF"
    assert store["settings"]["brand_logo_url"] == "https://cdn.example.com/logo.png"
    assert store["settings"]["store_tagline"] == "Bot-specific tagline"
    assert "internal_setting" not in store["settings"]
