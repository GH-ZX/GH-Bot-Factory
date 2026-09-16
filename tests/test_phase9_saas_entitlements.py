from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
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
from packages.factory.models import BotProvisioningJob, BotProvisioningStatus
from packages.saas.models import SaaSPlan, SubscriptionStatus, TenantSubscription
from packages.saas.service import (
    EntitlementConfigurationError,
    resolve_tenant_entitlements,
    resolve_tenant_usage,
)
from packages.telegram.models import Bot
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase9-saas-jwt-secret-0123456789abcdef-0123456789abcdef"


async def create_identity(session: AsyncSession, tenant: Tenant, *, role: Role) -> tuple[User, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"{role.value.lower()}_{uuid.uuid4().hex[:8]}",
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


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_self_hosted_tenant_uses_existing_factory_defaults(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = Tenant(name="Legacy Tenant", slug="legacy-saas-defaults", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    monkeypatch.setattr(settings, "factory_max_bots_per_tenant", 7)
    monkeypatch.setattr(settings, "factory_max_enabled_bots_per_tenant", 3)
    monkeypatch.setattr(settings, "factory_max_open_provisioning_jobs_per_tenant", 2)

    snapshot = await resolve_tenant_entitlements(db_session, tenant_id=tenant.id)

    assert snapshot.source == "SELF_HOSTED_DEFAULTS"
    assert snapshot.plan_key is None
    assert snapshot.max_bots == 7
    assert snapshot.max_enabled_bots == 3
    assert snapshot.max_open_provisioning_jobs == 2


async def test_subscription_plan_and_overrides_are_authoritative(db_session: AsyncSession) -> None:
    tenant = Tenant(name="SaaS Tenant", slug="saas-plan-tenant", is_active=True)
    plan = SaaSPlan(
        key="growth",
        name="Growth",
        is_active=True,
        is_public=True,
        entitlements={
            "max_bots": 5,
            "max_enabled_bots": 3,
            "max_open_provisioning_jobs": 4,
            "features": {"canary_rollout": False, "custom_branding": True},
        },
        metadata_json={},
    )
    db_session.add_all([tenant, plan])
    await db_session.flush()
    db_session.add(
        TenantSubscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            status=SubscriptionStatus.TRIALING,
            entitlement_overrides={
                "max_bots": 8,
                "features": {"canary_rollout": True},
            },
            billing_metadata={},
        )
    )
    await db_session.flush()

    snapshot = await resolve_tenant_entitlements(db_session, tenant_id=tenant.id)

    assert snapshot.source == "SAAS_PLAN"
    assert snapshot.plan_key == "growth"
    assert snapshot.plan_name == "Growth"
    assert snapshot.subscription_status == "TRIALING"
    assert snapshot.max_bots == 8
    assert snapshot.max_enabled_bots == 3
    assert snapshot.max_open_provisioning_jobs == 4
    assert snapshot.features == {"canary_rollout": True, "custom_branding": True}


@pytest.mark.parametrize(
    "entitlements",
    [
        {},
        {"max_bots": 2, "max_enabled_bots": 3, "max_open_provisioning_jobs": 1},
        {"max_bots": 2, "max_enabled_bots": 1, "max_open_provisioning_jobs": 1, "features": ["bad"]},
    ],
)
async def test_invalid_subscribed_plan_fails_closed(
    db_session: AsyncSession,
    entitlements: dict[str, Any],
) -> None:
    tenant = Tenant(name="Broken Plan Tenant", slug=f"broken-{abs(hash(str(entitlements)))}", is_active=True)
    plan = SaaSPlan(
        key=f"broken-{abs(hash(str(entitlements)))}",
        name="Broken",
        is_active=True,
        is_public=False,
        entitlements=entitlements,
        metadata_json={},
    )
    db_session.add_all([tenant, plan])
    await db_session.flush()
    db_session.add(
        TenantSubscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            entitlement_overrides={},
            billing_metadata={},
        )
    )
    await db_session.flush()

    with pytest.raises(EntitlementConfigurationError):
        await resolve_tenant_entitlements(db_session, tenant_id=tenant.id)


async def test_usage_snapshot_counts_only_current_tenant_open_work(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Usage Tenant", slug="usage-tenant", is_active=True)
    other = Tenant(name="Other Usage", slug="other-usage-tenant", is_active=True)
    db_session.add_all([tenant, other])
    await db_session.flush()

    bots = [
        Bot(
            tenant_id=tenant.id,
            telegram_bot_id=920001,
            username="usage_one",
            display_name="Usage One",
            token_secret_ref="USAGE_ONE_TOKEN",
            is_enabled=True,
            config={},
        ),
        Bot(
            tenant_id=tenant.id,
            telegram_bot_id=920002,
            username="usage_two",
            display_name="Usage Two",
            token_secret_ref="USAGE_TWO_TOKEN",
            is_enabled=False,
            config={},
        ),
        Bot(
            tenant_id=other.id,
            telegram_bot_id=920003,
            username="other_usage",
            display_name="Other Usage",
            token_secret_ref="OTHER_USAGE_TOKEN",
            is_enabled=True,
            config={},
        ),
    ]
    db_session.add_all(bots)
    await db_session.flush()

    db_session.add_all(
        [
            BotProvisioningJob(
                tenant_id=tenant.id,
                idempotency_key="usage-pending",
                request_fingerprint="a" * 64,
                token_secret_ref="USAGE_PENDING_TOKEN",
                desired_enabled=True,
                desired_config={},
                status=BotProvisioningStatus.PENDING,
            ),
            BotProvisioningJob(
                tenant_id=tenant.id,
                idempotency_key="usage-ready",
                request_fingerprint="b" * 64,
                token_secret_ref="USAGE_READY_TOKEN",
                desired_enabled=True,
                desired_config={},
                status=BotProvisioningStatus.READY,
            ),
            BotProvisioningJob(
                tenant_id=other.id,
                idempotency_key="usage-other",
                request_fingerprint="c" * 64,
                token_secret_ref="USAGE_OTHER_TOKEN",
                desired_enabled=True,
                desired_config={},
                status=BotProvisioningStatus.RUNNING,
            ),
        ]
    )
    await db_session.flush()

    usage = await resolve_tenant_usage(db_session, tenant_id=tenant.id)

    assert usage.bots == 2
    assert usage.enabled_bots == 1
    assert usage.open_provisioning_jobs == 1


async def test_saas_overview_exposes_plan_usage_and_limits(admin_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Overview Tenant", slug="saas-overview-tenant", is_active=True)
    plan = SaaSPlan(
        key="overview-growth",
        name="Growth",
        is_active=True,
        is_public=True,
        entitlements={
            "max_bots": 4,
            "max_enabled_bots": 2,
            "max_open_provisioning_jobs": 3,
            "features": {"custom_branding": True},
        },
        metadata_json={},
    )
    session.add_all([tenant, plan])
    await session.flush()
    session.add(
        TenantSubscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            entitlement_overrides={},
            billing_metadata={},
        )
    )
    _, token = await create_identity(session, tenant, role=Role.STAFF)
    session.add(
        Bot(
            tenant_id=tenant.id,
            telegram_bot_id=920010,
            username="overview_bot",
            display_name="Overview Bot",
            token_secret_ref="OVERVIEW_BOT_TOKEN",
            is_enabled=True,
            config={},
        )
    )
    await session.flush()

    response = await client.get("/api/v1/admin/saas/overview", headers=auth(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "SAAS_PLAN"
    assert body["plan_key"] == "overview-growth"
    assert body["subscription_status"] == "ACTIVE"
    assert body["limits"] == {
        "max_bots": 4,
        "max_enabled_bots": 2,
        "max_open_provisioning_jobs": 3,
    }
    assert body["usage"]["bots"] == 1
    assert body["usage"]["enabled_bots"] == 1
    assert body["features"] == {"custom_branding": True}


async def test_bot_factory_capacity_is_enforced_from_subscription_plan(
    admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Plan Capacity", slug="plan-capacity-tenant", is_active=True)
    plan = SaaSPlan(
        key="one-bot",
        name="One Bot",
        is_active=True,
        is_public=True,
        entitlements={
            "max_bots": 1,
            "max_enabled_bots": 1,
            "max_open_provisioning_jobs": 1,
        },
        metadata_json={},
    )
    session.add_all([tenant, plan])
    await session.flush()
    session.add(
        TenantSubscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            entitlement_overrides={},
            billing_metadata={},
        )
    )
    _, token = await create_identity(session, tenant, role=Role.ADMIN)
    session.add(
        Bot(
            tenant_id=tenant.id,
            telegram_bot_id=920011,
            username="only_bot",
            display_name="Only Bot",
            token_secret_ref="ONLY_BOT_TOKEN",
            is_enabled=True,
            config={},
        )
    )
    await session.flush()

    blocked = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(token), "Idempotency-Key": "phase9-plan-capacity"},
        json={
            "token_secret_ref": "SECOND_BOT_TOKEN",
            "display_name": "Second Bot",
            "config": {},
            "is_enabled": False,
        },
    )

    assert blocked.status_code == 409, blocked.text
    assert "bot limit reached (1)" in blocked.json()["detail"].lower()
