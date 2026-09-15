import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStatus
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.fulfillment.models import (
    FulfillmentAttempt,
    FulfillmentJobRecord,
    FulfillmentJobStatus,
    FulfillmentStatus,
)
from packages.payments.models import (
    FinancialResolutionCase,
    FinancialResolutionCaseStatus,
    PaymentIntent,
    PaymentIntentPurpose,
    PaymentReconciliationEvent,
    PaymentReconciliationEventStatus,
    Wallet,
    WalletTopUpReversal,
    WalletTopUpReversalStatus,
)
from packages.payments.state_machine import PaymentIntentStatus
from packages.providers.models import Provider
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase7-analytics-test-jwt-secret-0123456789abcdef-0123456789abcdef"


async def identity(
    session: AsyncSession,
    tenant: Tenant,
    role: Role,
) -> tuple[User, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"analytics_{role.value.lower()}_{uuid.uuid4().hex[:6]}",
        first_name="Analytics",
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


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def analytics_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "session": db_session}
    app.dependency_overrides.clear()


async def test_analytics_is_tenant_scoped_currency_safe_and_staff_readable(
    analytics_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = analytics_env["client"]
    session: AsyncSession = analytics_env["session"]
    tenant = Tenant(name="Analytics Tenant", slug="analytics-tenant", is_active=True)
    other = Tenant(name="Other Analytics", slug="other-analytics", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    staff, staff_token = await identity(session, tenant, Role.STAFF)
    _, customer_token = await identity(session, tenant, Role.CUSTOMER)
    other_user, _ = await identity(session, other, Role.STAFF)

    session.add_all(
        [
            Order(
                tenant_id=tenant.id,
                user_id=staff.id,
                order_number=f"A-{uuid.uuid4().hex[:8]}",
                status=OrderStatus.FULFILLED,
                total_amount=Decimal("20.00"),
                currency="USD",
            ),
            Order(
                tenant_id=tenant.id,
                user_id=staff.id,
                order_number=f"A-{uuid.uuid4().hex[:8]}",
                status=OrderStatus.REFUNDED,
                total_amount=Decimal("10.00"),
                currency="USD",
            ),
            Order(
                tenant_id=tenant.id,
                user_id=staff.id,
                order_number=f"A-{uuid.uuid4().hex[:8]}",
                status=OrderStatus.FULFILLED,
                total_amount=Decimal("7.00"),
                currency="XTR",
            ),
            Order(
                tenant_id=other.id,
                user_id=other_user.id,
                order_number=f"B-{uuid.uuid4().hex[:8]}",
                status=OrderStatus.FULFILLED,
                total_amount=Decimal("999.00"),
                currency="USD",
            ),
        ]
    )
    await session.flush()

    topup = PaymentIntent(
        tenant_id=tenant.id,
        order_id=None,
        purpose=PaymentIntentPurpose.WALLET_TOPUP,
        user_id=staff.id,
        provider="mock",
        provider_payment_id="topup-a",
        currency="USD",
        amount=Decimal("50.00"),
        status=PaymentIntentStatus.SUCCEEDED,
        idempotency_key=f"topup-{uuid.uuid4()}",
        metadata_json={},
    )
    session.add(topup)
    await session.flush()
    wallet = Wallet(
        tenant_id=tenant.id,
        user_id=staff.id,
        currency="USD",
        balance=Decimal("40.00"),
        is_active=True,
    )
    session.add(wallet)
    await session.flush()
    session.add(
        WalletTopUpReversal(
            tenant_id=tenant.id,
            payment_intent_id=topup.id,
            user_id=staff.id,
            wallet_id=wallet.id,
            provider="mock",
            original_provider_payment_id="topup-a",
            provider_refund_id="refund-a",
            amount=Decimal("5.00"),
            currency="USD",
            idempotency_key=f"reversal-{uuid.uuid4()}",
            status=WalletTopUpReversalStatus.COMPLETED,
            completed_at=datetime.now(UTC),
            metadata_json={},
        )
    )
    session.add(
        Wallet(
            tenant_id=tenant.id,
            user_id=staff.id,
            currency="XTR",
            balance=Decimal("12.00"),
            is_active=False,
        )
    )

    provider = Provider(
        tenant_id=tenant.id,
        name="Supplier A",
        slug="supplier-a",
        provider_type="mock",
        is_enabled=True,
        priority=1,
        metadata_json={},
    )
    session.add(provider)
    await session.flush()
    tenant_order = (
        await session.execute(
            select(Order).where(
                Order.tenant_id == tenant.id,
                Order.currency == "USD",
                Order.status == OrderStatus.FULFILLED,
            )
        )
    ).scalars().first()
    assert tenant_order is not None
    session.add_all(
        [
            FulfillmentAttempt(
                tenant_id=tenant.id,
                order_id=tenant_order.id,
                provider_id=provider.id,
                attempt_number=1,
                idempotency_key=f"attempt-{uuid.uuid4()}",
                status=FulfillmentStatus.SUCCEEDED,
                cost_amount=Decimal("3.00"),
                cost_currency="USD",
                request_payload={},
                response_payload={},
            ),
            FulfillmentAttempt(
                tenant_id=tenant.id,
                order_id=tenant_order.id,
                provider_id=provider.id,
                attempt_number=2,
                idempotency_key=f"attempt-{uuid.uuid4()}",
                status=FulfillmentStatus.FAILED,
                cost_amount=Decimal("2.00"),
                cost_currency="USD",
                request_payload={},
                response_payload={},
            ),
            FulfillmentJobRecord(
                tenant_id=tenant.id,
                order_id=tenant_order.id,
                recipient="customer",
                attempt_number=3,
                status=FulfillmentJobStatus.DEAD_LETTER,
                payload={},
            ),
            FinancialResolutionCase(
                tenant_id=tenant.id,
                source_key=f"analytics-case:{uuid.uuid4()}",
                case_type="EXTERNAL_REVERSAL",
                severity="HIGH",
                status=FinancialResolutionCaseStatus.OPEN,
                metadata_json={},
            ),
            PaymentReconciliationEvent(
                tenant_id=tenant.id,
                provider="mock",
                provider_event_id=f"evt-{uuid.uuid4()}",
                event_type="REVERSAL",
                payment_intent_id=topup.id,
                status=PaymentReconciliationEventStatus.MANUAL_REVIEW,
                amount=Decimal("5.00"),
                currency="USD",
                classification="EXTERNAL_REVERSAL",
                requires_review=True,
                metadata_json={},
            ),
            AuditLog(
                tenant_id=tenant.id,
                user_id=staff.id,
                action="ANALYTICS_TEST",
                resource_type="test",
                resource_id="local",
                details={},
            ),
        ]
    )
    await session.commit()

    forbidden = await client.get("/api/v1/admin/analytics/overview", headers=auth(customer_token))
    assert forbidden.status_code == 403

    response = await client.get(
        "/api/v1/admin/analytics/overview?days=30",
        headers=auth(staff_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["orders"]["total"] == 3
    assert body["orders"]["fulfilled"] == 2
    assert body["orders"]["refunded"] == 1
    assert body["fulfillment"]["attempts"] == 2
    assert body["fulfillment"]["succeeded"] == 1
    assert body["fulfillment"]["success_rate"] == 50.0
    assert body["operations"]["dead_letter_jobs"] == 1
    assert body["operations"]["open_financial_cases"] == 1
    assert body["operations"]["frozen_wallets"] == 1
    assert body["operations"]["reconciliation_reviews"] == 1

    financials = {row["currency"]: row for row in body["financials"]}
    assert set(financials) == {"USD", "XTR"}
    assert Decimal(financials["USD"]["gross_order_value"]) == Decimal("30.00")
    assert Decimal(financials["USD"]["refunded_order_value"]) == Decimal("10.00")
    assert Decimal(financials["USD"]["net_order_value"]) == Decimal("20.00")
    assert Decimal(financials["USD"]["topup_value"]) == Decimal("50.00")
    assert Decimal(financials["USD"]["reversal_value"]) == Decimal("5.00")
    assert Decimal(financials["USD"]["wallet_liability"]) == Decimal("40.00")
    assert Decimal(financials["XTR"]["gross_order_value"]) == Decimal("7.00")
    assert Decimal(financials["XTR"]["wallet_liability"]) == Decimal("12.00")
    assert len(body["providers"]) == 1
    assert body["providers"][0]["provider_name"] == "Supplier A"
    assert body["providers"][0]["success_rate"] == 50.0


async def test_audit_explorer_is_tenant_scoped_filterable_and_redacts_secrets(
    analytics_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = analytics_env["client"]
    session: AsyncSession = analytics_env["session"]
    tenant = Tenant(name="Audit Tenant", slug="audit-tenant", is_active=True)
    other = Tenant(name="Other Audit", slug="other-audit", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    staff, token = await identity(session, tenant, Role.STAFF)
    other_user, _ = await identity(session, other, Role.STAFF)

    session.add_all(
        [
            AuditLog(
                tenant_id=tenant.id,
                user_id=staff.id,
                action="PROVIDER_UPDATED",
                resource_type="provider",
                resource_id="visible-provider",
                details={
                    "fields": ["priority"],
                    "api_token": "must-not-leak",
                    "nested": {"password": "must-not-leak-either", "safe": "visible"},
                },
            ),
            AuditLog(
                tenant_id=tenant.id,
                user_id=staff.id,
                action="PRODUCT_UPDATED",
                resource_type="product",
                resource_id="visible-product",
                details={"fields": ["title"]},
            ),
            AuditLog(
                tenant_id=other.id,
                user_id=other_user.id,
                action="PROVIDER_UPDATED",
                resource_type="provider",
                resource_id="foreign-provider",
                details={"safe": "foreign"},
            ),
        ]
    )
    await session.commit()

    response = await client.get(
        "/api/v1/admin/audit-logs?action=PROVIDER_UPDATED",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    log = body["logs"][0]
    assert log["resource_id"] == "visible-provider"
    assert log["actor"]["id"] == str(staff.id)
    assert log["details"]["api_token"] == "[REDACTED]"
    assert log["details"]["nested"]["password"] == "[REDACTED]"
    assert log["details"]["nested"]["safe"] == "visible"

    searched = await client.get(
        "/api/v1/admin/audit-logs?q=visible-product",
        headers=auth(token),
    )
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    assert searched.json()["logs"][0]["action"] == "PRODUCT_UPDATED"


async def test_analytics_rejects_unsupported_window(analytics_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = analytics_env["client"]
    session: AsyncSession = analytics_env["session"]
    tenant = Tenant(name="Window Tenant", slug="window-tenant", is_active=True)
    session.add(tenant)
    await session.flush()
    _, token = await identity(session, tenant, Role.STAFF)

    response = await client.get(
        "/api/v1/admin/analytics/overview?days=3650",
        headers=auth(token),
    )
    assert response.status_code == 422
