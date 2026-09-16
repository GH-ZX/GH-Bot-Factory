from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from apps.api.main import app
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.saas.billing import ProviderSubscriptionState
from packages.saas.models import (
    BillingInterval,
    PlatformAuditLog,
    SaaSPlan,
    SaaSPlanPrice,
    SubscriptionStatus,
    TenantSubscription,
)
from packages.saas.operations import collect_saas_health
from packages.saas.reconciliation_worker import SaaSBillingReconciliationWorker
from packages.tenants.models import Tenant

pytestmark = pytest.mark.asyncio
PLATFORM_TOKEN = "phase9-4-platform-admin-token-0123456789abcdef-0123456789abcdef"


class FakePullAdapter:
    provider_name = "stripe"

    def __init__(self, state: ProviderSubscriptionState) -> None:
        self.state = state
        self.calls: list[str] = []

    async def fetch_subscription(self, external_subscription_id: str) -> ProviderSubscriptionState:
        self.calls.append(external_subscription_id)
        return self.state


async def seed_provider_subscription(
    session: AsyncSession,
    *,
    slug: str,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    grace_ends_at: datetime | None = None,
    provider_synced_at: datetime | None = None,
    provider_sync_error: str | None = None,
) -> tuple[Tenant, SaaSPlan, SaaSPlanPrice, TenantSubscription]:
    tenant = Tenant(name=slug.replace("-", " ").title(), slug=slug, is_active=True)
    plan = SaaSPlan(
        key=f"plan-{slug}",
        name="Operations Plan",
        is_active=True,
        is_public=True,
        entitlements={
            "max_bots": 5,
            "max_enabled_bots": 3,
            "max_open_provisioning_jobs": 2,
            "features": {"runtime_controls": True},
        },
        metadata_json={},
    )
    session.add_all([tenant, plan])
    await session.flush()
    price = SaaSPlanPrice(
        plan_id=plan.id,
        provider="stripe",
        external_price_id=f"price_{slug}",
        currency="USD",
        unit_amount_minor=1900,
        interval=BillingInterval.MONTH,
        interval_count=1,
        is_active=True,
        metadata_json={},
    )
    subscription = TenantSubscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        status=status,
        billing_provider="stripe",
        external_customer_id=f"cus_{slug}",
        external_subscription_id=f"sub_{slug}",
        grace_ends_at=grace_ends_at,
        provider_synced_at=provider_synced_at,
        provider_sync_source="PULL_RECONCILIATION" if provider_synced_at else None,
        provider_sync_error=provider_sync_error,
        provider_sync_error_at=datetime.now(UTC) if provider_sync_error else None,
        entitlement_overrides={},
        billing_metadata={},
    )
    session.add_all([price, subscription])
    await session.flush()
    return tenant, plan, price, subscription


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


def platform_headers() -> dict[str, str]:
    return {"X-GHBF-Platform-Token": PLATFORM_TOKEN}


async def test_health_snapshot_surfaces_grace_blocked_and_stale_sync(db_session: AsyncSession) -> None:
    now = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    self_hosted = Tenant(name="Self Hosted", slug="ops-self-hosted", is_active=True)
    db_session.add(self_hosted)
    await db_session.flush()
    await seed_provider_subscription(
        db_session,
        slug="ops-grace",
        status=SubscriptionStatus.PAST_DUE,
        grace_ends_at=now + timedelta(days=1),
        provider_synced_at=now - timedelta(minutes=10),
    )
    await seed_provider_subscription(
        db_session,
        slug="ops-blocked",
        status=SubscriptionStatus.PAST_DUE,
        grace_ends_at=now - timedelta(minutes=1),
        provider_synced_at=now - timedelta(days=2),
        provider_sync_error="provider unavailable",
    )
    await db_session.flush()

    snapshot = await collect_saas_health(db_session, now=now, stale_seconds=3600)

    assert snapshot.total_tenants == 3
    assert snapshot.self_hosted_tenants == 1
    assert snapshot.subscribed_tenants == 2
    assert snapshot.grace_access == 1
    assert snapshot.blocked_access == 1
    assert snapshot.provider_managed_subscriptions == 2
    assert snapshot.stale_provider_syncs == 1
    codes = [alert.code for alert in snapshot.alerts]
    assert "SUBSCRIPTION_IN_GRACE" in codes
    assert "COMMERCIAL_ACCESS_BLOCKED" in codes
    assert "PROVIDER_SYNC_ERROR" in codes
    assert "PROVIDER_SYNC_STALE" in codes


async def test_platform_health_and_support_safe_entitlement_inspection(platform_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = platform_env["client"]
    session: AsyncSession = platform_env["session"]
    tenant, _, _, subscription = await seed_provider_subscription(
        session,
        slug="ops-inspection",
        status=SubscriptionStatus.ACTIVE,
        provider_synced_at=datetime.now(UTC),
    )
    await session.commit()

    health = await client.get("/api/v1/platform/operations/saas-health", headers=platform_headers())
    assert health.status_code == 200, health.text
    assert health.json()["subscribed_tenants"] == 1

    inspected = await client.get(
        f"/api/v1/platform/tenants/{tenant.id}/entitlements",
        headers=platform_headers(),
    )
    assert inspected.status_code == 200, inspected.text
    body = inspected.json()
    assert body["tenant_id"] == str(tenant.id)
    assert body["commercial_access_state"] == "ACTIVE"
    assert body["commercial_access_allowed"] is True
    assert body["provider_synced_at"] is not None
    assert body["effective_features"]["runtime_controls"] is True
    assert "token" not in str(body).lower()

    audit = await session.scalar(
        select(PlatformAuditLog).where(
            PlatformAuditLog.tenant_id == tenant.id,
            PlatformAuditLog.action == "TENANT_ENTITLEMENTS_INSPECTED",
        )
    )
    assert audit is not None
    assert audit.details["support_mode"] == "NO_IMPERSONATION"
    assert subscription.external_customer_id not in str(audit.details)


async def test_periodic_pull_reconciler_updates_sync_evidence_and_audit(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with db_session_factory() as session:
        tenant, plan, price, subscription = await seed_provider_subscription(
            session,
            slug="ops-worker",
            status=SubscriptionStatus.ACTIVE,
        )
        await session.commit()
        tenant_id = tenant.id
        plan_key = plan.key
        external_price_id = price.external_price_id
        external_subscription_id = subscription.external_subscription_id

    state = ProviderSubscriptionState(
        provider="stripe",
        event_type="customer.subscription.updated",
        external_event_id=None,
        tenant_id=tenant_id,
        external_price_id=external_price_id,
        status=SubscriptionStatus.ACTIVE,
        external_customer_id="cus_ops-worker",
        external_subscription_id=external_subscription_id,
        current_period_start=datetime.now(UTC),
        current_period_end=datetime.now(UTC) + timedelta(days=30),
        trial_ends_at=None,
        cancel_at_period_end=False,
        provider_created_at=datetime.now(UTC),
        revision="ops-worker-r1",
        event_metadata={},
    )
    adapter = FakePullAdapter(state)
    monkeypatch.setattr(settings, "billing_provider", "stripe")
    monkeypatch.setattr(settings, "billing_reconcile_enabled", True)
    monkeypatch.setattr("packages.saas.reconciliation_worker.get_billing_provider", lambda: adapter)

    worker = SaaSBillingReconciliationWorker(
        session_factory=db_session_factory,
        poll_interval_seconds=60,
        batch_size=50,
    )
    summary = await worker.poll_once()
    assert summary is not None
    assert summary.scanned == 1
    assert summary.applied == 1
    assert adapter.calls == [external_subscription_id]

    async with db_session_factory() as session:
        refreshed = await session.scalar(
            select(TenantSubscription)
            .where(TenantSubscription.tenant_id == tenant_id)
            .options(selectinload(TenantSubscription.plan))
        )
        assert refreshed is not None
        assert refreshed.plan.key == plan_key
        assert refreshed.provider_synced_at is not None
        assert refreshed.provider_sync_source == "PULL_RECONCILIATION"
        assert refreshed.provider_sync_error is None
        audit = await session.scalar(
            select(PlatformAuditLog).where(
                PlatformAuditLog.action == "BILLING_RECONCILIATION_RUN",
                PlatformAuditLog.actor == "SYSTEM_BILLING_RECONCILER",
            )
        )
        assert audit is not None
        assert audit.details["mode"] == "PERIODIC_PULL"
