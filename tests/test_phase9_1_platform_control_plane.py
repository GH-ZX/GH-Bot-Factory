from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.saas.models import BillingEvent, PlatformAuditLog, SaaSPlan, TenantSubscription
from packages.saas.service import resolve_tenant_entitlements
from packages.tenants.models import Tenant

pytestmark = pytest.mark.asyncio
PLATFORM_TOKEN = "phase9-1-platform-admin-token-0123456789abcdef-0123456789abcdef"


def platform_headers(token: str = PLATFORM_TOKEN) -> dict[str, str]:
    return {"X-GHBF-Platform-Token": token}


def plan_payload(key: str = "growth") -> dict[str, Any]:
    return {
        "key": key,
        "name": key.replace("-", " ").title(),
        "description": "Platform managed plan",
        "is_active": True,
        "is_public": True,
        "entitlements": {
            "max_bots": 5,
            "max_enabled_bots": 3,
            "max_open_provisioning_jobs": 2,
            "features": {"custom_branding": True},
        },
        "metadata": {"currency": "USD", "price_minor": 1900},
    }


@pytest_asyncio.fixture
async def platform_env(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[dict[str, Any], None]:
    monkeypatch.setattr(settings, "platform_admin_token", PLATFORM_TOKEN)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "session": db_session}
    app.dependency_overrides.clear()


async def test_platform_boundary_rejects_missing_and_wrong_token(platform_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = platform_env["client"]

    missing = await client.get("/api/v1/platform/overview")
    wrong = await client.get(
        "/api/v1/platform/overview",
        headers=platform_headers("x" * 64),
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401


async def test_plan_and_subscription_lifecycle_is_platform_owned(platform_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = platform_env["client"]
    session: AsyncSession = platform_env["session"]
    tenant = Tenant(name="Laptop Shop", slug="laptop-shop", is_active=True)
    session.add(tenant)
    await session.commit()

    created = await client.post(
        "/api/v1/platform/plans",
        headers=platform_headers(),
        json=plan_payload("local-pro"),
    )
    assert created.status_code == 201, created.text
    assert created.json()["subscriber_count"] == 0

    assigned = await client.put(
        f"/api/v1/platform/tenants/{tenant.id}/subscription",
        headers=platform_headers(),
        json={"plan_key": "local-pro", "status": "ACTIVE"},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["plan_key"] == "local-pro"

    snapshot = await resolve_tenant_entitlements(session, tenant_id=tenant.id)
    assert snapshot.source == "SAAS_PLAN"
    assert snapshot.plan_key == "local-pro"
    assert snapshot.max_bots == 5

    overview = await client.get("/api/v1/platform/overview", headers=platform_headers())
    assert overview.status_code == 200
    assert overview.json()["subscriptions"] == 1
    assert overview.json()["plans"] == 1

    cleared = await client.delete(
        f"/api/v1/platform/tenants/{tenant.id}/subscription",
        headers=platform_headers(),
    )
    assert cleared.status_code == 204, cleared.text
    assert await session.scalar(select(func.count()).select_from(TenantSubscription)) == 0


async def test_retired_plan_keeps_existing_subscriber_but_blocks_new_assignment(
    platform_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = platform_env["client"]
    session: AsyncSession = platform_env["session"]
    first = Tenant(name="Existing", slug="existing-retired", is_active=True)
    second = Tenant(name="New", slug="new-retired", is_active=True)
    session.add_all([first, second])
    await session.commit()

    created = await client.post(
        "/api/v1/platform/plans",
        headers=platform_headers(),
        json=plan_payload("retirable"),
    )
    plan_id = created.json()["id"]
    assigned = await client.put(
        f"/api/v1/platform/tenants/{first.id}/subscription",
        headers=platform_headers(),
        json={"plan_key": "retirable", "status": "ACTIVE"},
    )
    assert assigned.status_code == 200

    retired = await client.patch(
        f"/api/v1/platform/plans/{plan_id}",
        headers=platform_headers(),
        json={"is_active": False, "is_public": False},
    )
    assert retired.status_code == 200, retired.text

    existing = await resolve_tenant_entitlements(session, tenant_id=first.id)
    assert existing.plan_key == "retirable"

    blocked = await client.put(
        f"/api/v1/platform/tenants/{second.id}/subscription",
        headers=platform_headers(),
        json={"plan_key": "retirable", "status": "ACTIVE"},
    )
    assert blocked.status_code == 422, blocked.text


async def test_control_plane_rejects_secret_like_metadata(platform_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = platform_env["client"]
    payload = plan_payload("unsafe-meta")
    payload["metadata"] = {"stripe_api_key": "should-never-persist"}

    response = await client.post(
        "/api/v1/platform/plans",
        headers=platform_headers(),
        json=payload,
    )

    assert response.status_code == 422, response.text
    assert "secret-like" in response.json()["detail"]["message"].lower()


async def test_normalized_billing_event_converges_once_and_conflicts_on_id_reuse(
    platform_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = platform_env["client"]
    session: AsyncSession = platform_env["session"]
    tenant = Tenant(name="Billing Tenant", slug="billing-tenant", is_active=True)
    session.add(tenant)
    await session.commit()

    for key in ("billing-pro", "billing-max"):
        created = await client.post(
            "/api/v1/platform/plans",
            headers=platform_headers(),
            json=plan_payload(key),
        )
        assert created.status_code == 201, created.text

    event = {
        "provider": "examplepay",
        "external_event_id": "evt_001",
        "event_type": "subscription.updated",
        "plan_key": "billing-pro",
        "status": "ACTIVE",
        "external_customer_id": "cus_001",
        "external_subscription_id": "sub_001",
        "event_metadata": {"source": "signed-webhook-adapter"},
    }
    first = await client.post(
        f"/api/v1/platform/tenants/{tenant.id}/billing-events/converge",
        headers=platform_headers(),
        json=event,
    )
    second = await client.post(
        f"/api/v1/platform/tenants/{tenant.id}/billing-events/converge",
        headers=platform_headers(),
        json=event,
    )

    assert first.status_code == 200, first.text
    assert first.json()["duplicate"] is False
    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is True
    assert await session.scalar(select(func.count()).select_from(BillingEvent)) == 1

    changed = dict(event)
    changed["plan_key"] = "billing-max"
    conflict = await client.post(
        f"/api/v1/platform/tenants/{tenant.id}/billing-events/converge",
        headers=platform_headers(),
        json=changed,
    )
    assert conflict.status_code == 409, conflict.text
    assert await session.scalar(select(func.count()).select_from(BillingEvent)) == 1


async def test_platform_mutations_emit_separate_global_audit(platform_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = platform_env["client"]
    session: AsyncSession = platform_env["session"]
    tenant = Tenant(name="Audit Tenant", slug="platform-audit-tenant", is_active=True)
    session.add(tenant)
    await session.commit()

    created = await client.post(
        "/api/v1/platform/plans",
        headers=platform_headers(),
        json=plan_payload("audited"),
    )
    assert created.status_code == 201
    assigned = await client.put(
        f"/api/v1/platform/tenants/{tenant.id}/subscription",
        headers=platform_headers(),
        json={"plan_key": "audited", "status": "ACTIVE"},
    )
    assert assigned.status_code == 200

    rows = (await session.execute(select(PlatformAuditLog).order_by(PlatformAuditLog.created_at))).scalars().all()
    assert [row.action for row in rows] == ["SAAS_PLAN_CREATED", "TENANT_SUBSCRIPTION_SET"]
    assert rows[1].tenant_id == tenant.id

    response = await client.get("/api/v1/platform/audit", headers=platform_headers())
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_plan_update_cannot_break_existing_subscription_override(
    platform_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = platform_env["client"]
    session: AsyncSession = platform_env["session"]
    tenant = Tenant(name="Override Tenant", slug="override-plan-tenant", is_active=True)
    session.add(tenant)
    await session.commit()

    created = await client.post(
        "/api/v1/platform/plans",
        headers=platform_headers(),
        json=plan_payload("override-safe"),
    )
    plan_id = created.json()["id"]
    assigned = await client.put(
        f"/api/v1/platform/tenants/{tenant.id}/subscription",
        headers=platform_headers(),
        json={
            "plan_key": "override-safe",
            "status": "ACTIVE",
            "entitlement_overrides": {"max_enabled_bots": 5},
        },
    )
    assert assigned.status_code == 200, assigned.text

    broken_update = await client.patch(
        f"/api/v1/platform/plans/{plan_id}",
        headers=platform_headers(),
        json={
            "entitlements": {
                "max_bots": 4,
                "max_enabled_bots": 3,
                "max_open_provisioning_jobs": 2,
            }
        },
    )
    assert broken_update.status_code == 422, broken_update.text

    plan = await session.get(SaaSPlan, uuid.UUID(plan_id))
    assert plan is not None
    assert plan.entitlements["max_bots"] == 5
