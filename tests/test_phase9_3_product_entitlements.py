from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.factory.templates import build_template_config
from packages.saas.models import SaaSPlan, SubscriptionStatus, TenantSubscription
from packages.saas.product_entitlements import (
    FEATURE_CANARY_ROLLOUT,
    FEATURE_CUSTOM_BRANDING,
    FEATURE_RUNTIME_CONTROLS,
    CommercialAccessState,
    resolve_product_entitlements,
)
from packages.telegram.models import Bot
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase9-3-product-jwt-secret-0123456789abcdef-0123456789abcdef"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def create_identity(session: AsyncSession, tenant: Tenant, *, role: Role = Role.ADMIN) -> tuple[User, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"phase93_{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role, permissions=[], is_active=True))
    await session.flush()
    token = AuthTokenService(secret_key=TEST_JWT_SECRET).issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[role],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )
    return user, token


@pytest_asyncio.fixture
async def admin_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "session": db_session}
    app.dependency_overrides.clear()


async def make_subscription(
    session: AsyncSession,
    *,
    tenant: Tenant,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    features: dict[str, bool] | None = None,
    grace_ends_at: datetime | None = None,
) -> tuple[SaaSPlan, TenantSubscription]:
    plan = SaaSPlan(
        key=f"phase93-{uuid.uuid4().hex[:8]}",
        name="Phase 9.3",
        is_active=True,
        is_public=True,
        entitlements={
            "max_bots": 5,
            "max_enabled_bots": 3,
            "max_open_provisioning_jobs": 2,
            "features": features or {},
        },
        metadata_json={},
    )
    session.add(plan)
    await session.flush()
    subscription = TenantSubscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        status=status,
        grace_ends_at=grace_ends_at,
        entitlement_overrides={},
        billing_metadata={},
    )
    session.add(subscription)
    await session.flush()
    return plan, subscription


async def test_self_hosted_product_features_remain_available(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Laptop Local", slug=f"laptop-{uuid.uuid4().hex[:8]}", is_active=True)
    db_session.add(tenant)
    await db_session.flush()

    product = await resolve_product_entitlements(db_session, tenant_id=tenant.id)

    assert product.access.state == CommercialAccessState.SELF_HOSTED
    assert product.access.allowed is True
    assert product.effective_features[FEATURE_CUSTOM_BRANDING] is True
    assert product.effective_features[FEATURE_CANARY_ROLLOUT] is True
    assert product.effective_features[FEATURE_RUNTIME_CONTROLS] is True


async def test_past_due_grace_is_deterministic_and_fail_closed_after_deadline(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Grace Tenant", slug=f"grace-{uuid.uuid4().hex[:8]}", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    _, subscription = await make_subscription(
        db_session,
        tenant=tenant,
        status=SubscriptionStatus.PAST_DUE,
        features={FEATURE_CUSTOM_BRANDING: True},
        grace_ends_at=now + timedelta(days=2),
    )

    inside = await resolve_product_entitlements(db_session, tenant_id=tenant.id, now=now)
    assert inside.access.state == CommercialAccessState.GRACE
    assert inside.access.allowed is True
    assert inside.effective_features[FEATURE_CUSTOM_BRANDING] is True

    after = await resolve_product_entitlements(db_session, tenant_id=tenant.id, now=now + timedelta(days=3))
    assert after.access.state == CommercialAccessState.BLOCKED
    assert after.access.allowed is False
    assert after.effective_features[FEATURE_CUSTOM_BRANDING] is False

    subscription.grace_ends_at = None
    await db_session.flush()
    missing_deadline = await resolve_product_entitlements(db_session, tenant_id=tenant.id, now=now)
    assert missing_deadline.access.allowed is False


async def test_plan_feature_flags_gate_premium_bot_operations(admin_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Feature Gates", slug=f"feature-{uuid.uuid4().hex[:8]}", is_active=True)
    session.add(tenant)
    await session.flush()
    plan, _ = await make_subscription(
        session,
        tenant=tenant,
        features={
            FEATURE_CUSTOM_BRANDING: False,
            FEATURE_CANARY_ROLLOUT: False,
            FEATURE_RUNTIME_CONTROLS: False,
        },
    )
    _, token = await create_identity(session, tenant)
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=930001,
        username="phase93_bot",
        display_name="Phase 9.3 Bot",
        token_secret_ref="PHASE93_BOT_TOKEN",
        is_enabled=True,
        config=build_template_config(template_key="general-commerce"),
    )
    session.add(bot)
    await session.commit()

    branded = await client.patch(
        f"/api/v1/admin/bots/{bot.id}/configuration",
        headers=auth(token),
        json={
            "template_key": "general-commerce",
            "branding": {"brand_accent": "#112233"},
        },
    )
    assert branded.status_code == 403, branded.text
    assert branded.json()["detail"]["feature"] == FEATURE_CUSTOM_BRANDING

    canary = await client.patch(
        f"/api/v1/admin/bots/{bot.id}/release-channel",
        headers=auth(token),
        json={"release_channel": "CANARY"},
    )
    assert canary.status_code == 403, canary.text

    restart = await client.post(f"/api/v1/admin/bots/{bot.id}/runtime/restart", headers=auth(token))
    assert restart.status_code == 403, restart.text

    plan.entitlements = {
        **plan.entitlements,
        "features": {
            FEATURE_CUSTOM_BRANDING: True,
            FEATURE_CANARY_ROLLOUT: True,
            FEATURE_RUNTIME_CONTROLS: True,
        },
    }
    await session.commit()

    branded = await client.patch(
        f"/api/v1/admin/bots/{bot.id}/configuration",
        headers=auth(token),
        json={
            "template_key": "general-commerce",
            "branding": {"brand_accent": "#112233"},
        },
    )
    assert branded.status_code == 200, branded.text
    canary = await client.patch(
        f"/api/v1/admin/bots/{bot.id}/release-channel",
        headers=auth(token),
        json={"release_channel": "CANARY"},
    )
    assert canary.status_code == 200, canary.text
    restart = await client.post(f"/api/v1/admin/bots/{bot.id}/runtime/restart", headers=auth(token))
    assert restart.status_code == 200, restart.text


async def test_blocked_subscription_prevents_growth_but_allows_safe_shutdown(admin_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Blocked Tenant", slug=f"blocked-{uuid.uuid4().hex[:8]}", is_active=True)
    session.add(tenant)
    await session.flush()
    _, subscription = await make_subscription(
        session,
        tenant=tenant,
        status=SubscriptionStatus.PAST_DUE,
        features={FEATURE_RUNTIME_CONTROLS: True},
        grace_ends_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    _, token = await create_identity(session, tenant)
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=930002,
        username="blocked_bot",
        display_name="Blocked Bot",
        token_secret_ref="BLOCKED_BOT_TOKEN",
        is_enabled=True,
        config=build_template_config(template_key="general-commerce"),
    )
    session.add(bot)
    await session.commit()

    restart = await client.post(f"/api/v1/admin/bots/{bot.id}/runtime/restart", headers=auth(token))
    assert restart.status_code == 402, restart.text
    assert restart.json()["detail"]["code"] == "SAAS_COMMERCIAL_ACCESS_BLOCKED"

    disable = await client.patch(
        f"/api/v1/admin/bots/{bot.id}/state",
        headers=auth(token),
        json={"is_enabled": False},
    )
    assert disable.status_code == 200, disable.text

    enable = await client.patch(
        f"/api/v1/admin/bots/{bot.id}/state",
        headers=auth(token),
        json={"is_enabled": True},
    )
    assert enable.status_code == 402, enable.text

    subscription.status = SubscriptionStatus.ACTIVE
    subscription.grace_ends_at = None
    await session.commit()
    enable = await client.patch(
        f"/api/v1/admin/bots/{bot.id}/state",
        headers=auth(token),
        json={"is_enabled": True},
    )
    assert enable.status_code == 200, enable.text


async def test_saas_overview_exposes_effective_features_and_commercial_access(admin_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Overview Phase93", slug=f"overview93-{uuid.uuid4().hex[:8]}", is_active=True)
    session.add(tenant)
    await session.flush()
    await make_subscription(
        session,
        tenant=tenant,
        status=SubscriptionStatus.PAUSED,
        features={FEATURE_CUSTOM_BRANDING: True},
    )
    _, token = await create_identity(session, tenant, role=Role.STAFF)
    await session.commit()

    response = await client.get("/api/v1/admin/saas/overview", headers=auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["commercial_access"]["state"] == "BLOCKED"
    assert body["commercial_access"]["allowed"] is False
    assert body["features"][FEATURE_CUSTOM_BRANDING] is True
    assert body["effective_features"][FEATURE_CUSTOM_BRANDING] is False
