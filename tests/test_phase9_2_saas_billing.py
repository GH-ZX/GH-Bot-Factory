from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from apps.api.v1 import platform as platform_api
from apps.api.v1 import saas_billing as billing_api
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.saas.billing import (
    BillingProviderResponseError,
    BillingWebhookVerificationError,
    CheckoutSessionResult,
    PortalSessionResult,
    ProviderSubscriptionState,
    StripeBillingAdapter,
    VerifiedWebhookEvent,
)
from packages.saas.models import (
    BillingEvent,
    BillingInterval,
    SaaSPlan,
    SaaSPlanPrice,
    SubscriptionStatus,
    TenantSubscription,
)
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase9-2-billing-jwt-secret-0123456789abcdef-0123456789abcdef"
PLATFORM_TOKEN = "phase9-2-platform-admin-token-0123456789abcdef-0123456789abcdef"


class FakeBillingAdapter:
    provider_name = "stripe"

    def __init__(self) -> None:
        self.checkout_calls: list[dict[str, Any]] = []
        self.portal_calls: list[dict[str, Any]] = []
        self.webhook_event: VerifiedWebhookEvent | None = None
        self.subscription_states: dict[str, ProviderSubscriptionState] = {}

    async def create_checkout_session(self, **kwargs: Any) -> CheckoutSessionResult:
        self.checkout_calls.append(kwargs)
        return CheckoutSessionResult(
            provider="stripe",
            external_session_id="cs_test_001",
            url="https://checkout.stripe.test/session/cs_test_001",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )

    async def create_portal_session(self, **kwargs: Any) -> PortalSessionResult:
        self.portal_calls.append(kwargs)
        return PortalSessionResult(
            provider="stripe",
            external_session_id="bps_test_001",
            url="https://billing.stripe.test/session/bps_test_001",
        )

    def verify_webhook(self, payload: bytes, signature_header: str) -> VerifiedWebhookEvent:
        assert payload
        assert signature_header
        if self.webhook_event is None:
            raise AssertionError("test did not configure webhook_event")
        return self.webhook_event

    async def fetch_subscription(self, external_subscription_id: str) -> ProviderSubscriptionState:
        try:
            return self.subscription_states[external_subscription_id]
        except KeyError as exc:
            raise BillingProviderResponseError("missing fake subscription") from exc


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


async def seed_plan_price(
    session: AsyncSession,
    *,
    key: str = "pro",
    external_price_id: str = "price_pro_monthly",
) -> tuple[SaaSPlan, SaaSPlanPrice]:
    plan = SaaSPlan(
        key=key,
        name=key.title(),
        description="Hosted SaaS plan",
        is_active=True,
        is_public=True,
        entitlements={
            "max_bots": 10,
            "max_enabled_bots": 5,
            "max_open_provisioning_jobs": 3,
            "features": {"custom_branding": True},
        },
        metadata_json={},
    )
    session.add(plan)
    await session.flush()
    price = SaaSPlanPrice(
        plan_id=plan.id,
        provider="stripe",
        external_price_id=external_price_id,
        currency="USD",
        unit_amount_minor=2900,
        interval=BillingInterval.MONTH,
        interval_count=1,
        is_active=True,
        metadata_json={},
    )
    session.add(price)
    await session.flush()
    return plan, price


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def platform_headers() -> dict[str, str]:
    return {"X-GHBF-Platform-Token": PLATFORM_TOKEN}


@pytest_asyncio.fixture
async def billing_env(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[dict[str, Any], None]:
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    fake = FakeBillingAdapter()
    monkeypatch.setattr(settings, "billing_provider", "stripe")
    monkeypatch.setattr(settings, "billing_success_url", "https://app.example.test/admin/?billing=success")
    monkeypatch.setattr(settings, "billing_cancel_url", "https://app.example.test/admin/?billing=cancel")
    monkeypatch.setattr(settings, "billing_portal_return_url", "https://app.example.test/admin/?billing=return")
    monkeypatch.setattr(settings, "billing_past_due_grace_days", 7)
    monkeypatch.setattr(settings, "platform_admin_token", PLATFORM_TOKEN)
    monkeypatch.setattr(billing_api, "get_billing_provider", lambda provider=None: fake)
    monkeypatch.setattr(platform_api, "get_billing_provider", lambda provider=None: fake)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "session": db_session, "adapter": fake}
    app.dependency_overrides.clear()


async def test_platform_price_catalog_is_operator_owned(billing_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = billing_env["client"]
    created_plan = await client.post(
        "/api/v1/platform/plans",
        headers=platform_headers(),
        json={
            "key": "growth-hosted",
            "name": "Growth Hosted",
            "is_active": True,
            "is_public": True,
            "entitlements": {
                "max_bots": 8,
                "max_enabled_bots": 4,
                "max_open_provisioning_jobs": 2,
                "features": {},
            },
            "metadata": {},
        },
    )
    assert created_plan.status_code == 201, created_plan.text
    plan_id = created_plan.json()["id"]

    created_price = await client.post(
        f"/api/v1/platform/plans/{plan_id}/prices",
        headers=platform_headers(),
        json={
            "provider": "stripe",
            "external_price_id": "price_growth_monthly",
            "currency": "usd",
            "unit_amount_minor": 1900,
            "interval": "MONTH",
            "interval_count": 1,
            "is_active": True,
            "metadata": {"label": "Monthly"},
        },
    )
    assert created_price.status_code == 201, created_price.text
    assert created_price.json()["currency"] == "USD"

    listed = await client.get(
        f"/api/v1/platform/plans/{plan_id}/prices",
        headers=platform_headers(),
    )
    assert listed.status_code == 200
    assert [row["external_price_id"] for row in listed.json()] == ["price_growth_monthly"]


async def test_owner_can_open_checkout_and_portal_without_mutating_subscription_directly(
    billing_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = billing_env["client"]
    session: AsyncSession = billing_env["session"]
    fake: FakeBillingAdapter = billing_env["adapter"]
    tenant = Tenant(name="Hosted Shop", slug="hosted-shop", is_active=True)
    session.add(tenant)
    await session.flush()
    owner, token = await create_identity(session, tenant, role=Role.OWNER)
    plan, price = await seed_plan_price(session)
    await session.commit()

    overview = await client.get("/api/v1/admin/saas/billing", headers=auth(token))
    assert overview.status_code == 200, overview.text
    payload = overview.json()
    assert payload["provider_configured"] is True
    assert payload["checkout_available"] is True
    assert payload["portal_available"] is False
    assert payload["catalog"][0]["id"] == str(price.id)

    checkout = await client.post(
        "/api/v1/admin/saas/billing/checkout",
        headers={**auth(token), "Idempotency-Key": "checkout-request-001"},
        json={"price_id": str(price.id)},
    )
    assert checkout.status_code == 200, checkout.text
    assert checkout.json()["url"].startswith("https://checkout.stripe.test/")
    assert len(fake.checkout_calls) == 1
    assert await session.scalar(select(func.count()).select_from(TenantSubscription)) == 0

    subscription = TenantSubscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        billing_provider="stripe",
        external_customer_id="cus_hosted_001",
        external_subscription_id="sub_hosted_001",
        entitlement_overrides={},
        billing_metadata={},
    )
    session.add(subscription)
    await session.commit()

    portal = await client.post("/api/v1/admin/saas/billing/portal", headers=auth(token))
    assert portal.status_code == 200, portal.text
    assert portal.json()["url"].startswith("https://billing.stripe.test/")
    assert fake.portal_calls == [
        {
            "external_customer_id": "cus_hosted_001",
            "return_url": "https://app.example.test/admin/?billing=return",
        }
    ]
    audit_actions = (
        await session.execute(select(AuditLog.action).where(AuditLog.user_id == owner.id))
    ).scalars().all()
    assert "SAAS_BILLING_CHECKOUT_CREATED" in audit_actions
    assert "SAAS_BILLING_PORTAL_CREATED" in audit_actions


async def test_signed_webhook_converges_subscription_and_sets_deterministic_grace(
    billing_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = billing_env["client"]
    session: AsyncSession = billing_env["session"]
    fake: FakeBillingAdapter = billing_env["adapter"]
    tenant = Tenant(name="Grace Shop", slug="grace-shop", is_active=True)
    session.add(tenant)
    await session.flush()
    _, price = await seed_plan_price(session, key="grace", external_price_id="price_grace")
    await session.commit()

    event_time = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    state = ProviderSubscriptionState(
        provider="stripe",
        event_type="customer.subscription.updated",
        external_event_id="evt_grace_001",
        tenant_id=tenant.id,
        external_price_id=price.external_price_id,
        status=SubscriptionStatus.PAST_DUE,
        external_customer_id="cus_grace",
        external_subscription_id="sub_grace",
        current_period_start=event_time - timedelta(days=30),
        current_period_end=event_time + timedelta(days=1),
        trial_ends_at=None,
        cancel_at_period_end=False,
        provider_created_at=event_time,
        revision="rev-grace-1",
        event_metadata={"source": "stripe"},
    )
    fake.webhook_event = VerifiedWebhookEvent(
        provider="stripe",
        external_event_id="evt_grace_001",
        event_type="customer.subscription.updated",
        provider_created_at=event_time,
        subscription_state=state,
        external_subscription_id="sub_grace",
        tenant_id=tenant.id,
    )

    first = await client.post(
        "/api/v1/billing/webhooks/stripe",
        headers={"Stripe-Signature": "fake-signed-header"},
        content=b'{"signed":"provider-payload"}',
    )
    second = await client.post(
        "/api/v1/billing/webhooks/stripe",
        headers={"Stripe-Signature": "fake-signed-header"},
        content=b'{"signed":"provider-payload"}',
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "APPLIED"
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "DUPLICATE"

    subscription = await session.scalar(
        select(TenantSubscription).where(TenantSubscription.tenant_id == tenant.id)
    )
    assert subscription is not None
    assert subscription.status == SubscriptionStatus.PAST_DUE
    stored_grace = subscription.grace_ends_at
    assert stored_grace is not None
    if stored_grace.tzinfo is None:
        stored_grace = stored_grace.replace(tzinfo=UTC)
    assert stored_grace == event_time + timedelta(days=7)
    assert await session.scalar(select(func.count()).select_from(BillingEvent)) == 1


async def test_pull_reconciliation_works_without_public_webhook_ingress(
    billing_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = billing_env["client"]
    session: AsyncSession = billing_env["session"]
    fake: FakeBillingAdapter = billing_env["adapter"]
    tenant = Tenant(name="Laptop Shop", slug="laptop-pull-shop", is_active=True)
    session.add(tenant)
    await session.flush()
    plan, price = await seed_plan_price(session, key="laptop", external_price_id="price_laptop")
    subscription = TenantSubscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        billing_provider="stripe",
        external_customer_id="cus_laptop",
        external_subscription_id="sub_laptop",
        entitlement_overrides={},
        billing_metadata={},
    )
    session.add(subscription)
    await session.commit()

    now = datetime(2026, 9, 15, 19, 0, tzinfo=UTC)
    fake.subscription_states["sub_laptop"] = ProviderSubscriptionState(
        provider="stripe",
        event_type="subscription.reconciled",
        external_event_id=None,
        tenant_id=tenant.id,
        external_price_id=price.external_price_id,
        status=SubscriptionStatus.PAUSED,
        external_customer_id="cus_laptop",
        external_subscription_id="sub_laptop",
        current_period_start=now - timedelta(days=5),
        current_period_end=now + timedelta(days=25),
        trial_ends_at=None,
        cancel_at_period_end=False,
        provider_created_at=now,
        revision="pull-revision-001",
        event_metadata={"source": "stripe"},
    )

    first = await client.post(
        "/api/v1/platform/billing/reconcile?provider=stripe",
        headers=platform_headers(),
    )
    second = await client.post(
        "/api/v1/platform/billing/reconcile?provider=stripe",
        headers=platform_headers(),
    )
    assert first.status_code == 200, first.text
    assert first.json()["applied"] == 1
    assert first.json()["failed"] == 0
    assert second.status_code == 200, second.text
    assert second.json()["duplicates"] == 1
    await session.refresh(subscription)
    assert subscription.status == SubscriptionStatus.PAUSED


def _stripe_payload(tenant_id: uuid.UUID) -> bytes:
    event = {
        "id": "evt_signed_001",
        "type": "customer.subscription.updated",
        "created": int(time.time()),
        "data": {
            "object": {
                "id": "sub_signed_001",
                "customer": "cus_signed_001",
                "status": "active",
                "current_period_start": int(time.time()) - 100,
                "current_period_end": int(time.time()) + 1000,
                "trial_end": None,
                "cancel_at_period_end": False,
                "metadata": {"ghbf_tenant_id": str(tenant_id)},
                "items": {"data": [{"price": {"id": "price_signed_001"}}]},
            }
        },
    }
    return json.dumps(event, separators=(",", ":")).encode("utf-8")


async def test_stripe_adapter_verifies_signature_and_rejects_tampering() -> None:
    webhook_secret = "whsec_phase9_2_test_secret"
    tenant_id = uuid.uuid4()
    payload = _stripe_payload(tenant_id)
    timestamp = int(time.time())
    digest = hmac.new(
        webhook_secret.encode("utf-8"),
        str(timestamp).encode("ascii") + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    adapter = StripeBillingAdapter(
        secret_key="sk_test_phase9_2",
        webhook_secret=webhook_secret,
        webhook_tolerance_seconds=300,
    )

    verified = adapter.verify_webhook(payload, f"t={timestamp},v1={digest}")
    assert verified.external_event_id == "evt_signed_001"
    assert verified.subscription_state is not None
    assert verified.subscription_state.tenant_id == tenant_id
    assert verified.subscription_state.external_price_id == "price_signed_001"
    assert verified.subscription_state.status == SubscriptionStatus.ACTIVE

    with pytest.raises(BillingWebhookVerificationError):
        adapter.verify_webhook(payload + b" ", f"t={timestamp},v1={digest}")
