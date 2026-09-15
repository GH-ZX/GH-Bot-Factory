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
from packages.payments.service import CANONICAL_REFUND_TYPE, LedgerService
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase7-1-fulfillment-ops-test-jwt-secret-0123456789abcdef"


async def create_identity(
    session: AsyncSession,
    tenant: Tenant,
    *,
    role: Role,
    name: str = "Operator",
) -> tuple[User, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"{role.value.lower()}_{uuid.uuid4().hex[:8]}",
        first_name=name,
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


async def create_order(
    session: AsyncSession,
    tenant: Tenant,
    customer: User,
    *,
    number: str,
    status: OrderStatus = OrderStatus.PROCESSING,
) -> Order:
    order = Order(
        tenant_id=tenant.id,
        user_id=customer.id,
        order_number=number,
        status=status,
        total_amount=Decimal("25.00"),
        currency="USD",
    )
    session.add(order)
    await session.flush()
    return order


@pytest_asyncio.fixture
async def fulfillment_admin_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
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


async def test_staff_can_list_dead_letters_but_only_for_own_tenant(
    fulfillment_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = fulfillment_admin_env["client"]
    session: AsyncSession = fulfillment_admin_env["session"]
    tenant = Tenant(name="Ops A", slug=f"ops-a-{uuid.uuid4().hex[:6]}", is_active=True)
    other = Tenant(name="Ops B", slug=f"ops-b-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, token = await create_identity(session, tenant, role=Role.STAFF)
    customer, _ = await create_identity(session, tenant, role=Role.CUSTOMER)
    hidden_customer, _ = await create_identity(session, other, role=Role.CUSTOMER)
    visible_order = await create_order(session, tenant, customer, number="OPS-VISIBLE")
    hidden_order = await create_order(session, other, hidden_customer, number="OPS-HIDDEN")
    session.add_all(
        [
            FulfillmentJobRecord(
                tenant_id=tenant.id,
                order_id=visible_order.id,
                recipient="visible@example.com",
                status=FulfillmentJobStatus.DEAD_LETTER,
                attempt_number=1,
                failure_classification="RuntimeError",
                last_error="worker crashed",
                payload={},
            ),
            FulfillmentJobRecord(
                tenant_id=other.id,
                order_id=hidden_order.id,
                recipient="hidden@example.com",
                status=FulfillmentJobStatus.DEAD_LETTER,
                attempt_number=1,
                failure_classification="RuntimeError",
                last_error="hidden",
                payload={},
            ),
        ]
    )
    await session.flush()

    response = await client.get(
        "/api/v1/admin/fulfillment/jobs?status=DEAD_LETTER",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert [row["order_number"] for row in body["jobs"]] == ["OPS-VISIBLE"]


async def test_job_detail_exposes_attempt_history_and_blocks_unknown_replay(
    fulfillment_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = fulfillment_admin_env["client"]
    session: AsyncSession = fulfillment_admin_env["session"]
    tenant = Tenant(name="Unknown Ops", slug=f"unknown-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    _, token = await create_identity(session, tenant, role=Role.STAFF)
    customer, _ = await create_identity(session, tenant, role=Role.CUSTOMER)
    order = await create_order(session, tenant, customer, number="OPS-UNKNOWN")
    job = FulfillmentJobRecord(
        tenant_id=tenant.id,
        order_id=order.id,
        recipient="buyer@example.com",
        status=FulfillmentJobStatus.DEAD_LETTER,
        attempt_number=1,
        failure_classification="ProviderTimeoutError",
        last_error="timeout",
        payload={},
    )
    attempt = FulfillmentAttempt(
        tenant_id=tenant.id,
        order_id=order.id,
        attempt_number=1,
        idempotency_key=f"order:{order.id}:attempt:1",
        status=FulfillmentStatus.UNKNOWN,
        error_classification="ProviderTimeoutError",
        response_payload={"error": "timeout", "retryable": True},
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        cost_currency="USD",
    )
    session.add_all([job, attempt])
    await session.flush()

    response = await client.get(f"/api/v1/admin/fulfillment/jobs/{job.id}", headers=auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_requeue"] is False
    assert body["requeue_reason_code"] == "ATTEMPT_STATUS_UNKNOWN"
    assert body["attempts"][0]["status"] == "UNKNOWN"
    assert body["attempts"][0]["error_classification"] == "ProviderTimeoutError"


async def test_only_admin_can_atomically_requeue_safe_dead_letter_and_action_is_audited(
    fulfillment_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = fulfillment_admin_env["client"]
    session: AsyncSession = fulfillment_admin_env["session"]
    tenant = Tenant(name="Requeue Ops", slug=f"requeue-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    admin, admin_token = await create_identity(session, tenant, role=Role.ADMIN)
    _, manager_token = await create_identity(session, tenant, role=Role.MANAGER)
    customer, _ = await create_identity(session, tenant, role=Role.CUSTOMER)
    order = await create_order(session, tenant, customer, number="OPS-REQUEUE")
    job = FulfillmentJobRecord(
        tenant_id=tenant.id,
        order_id=order.id,
        recipient="buyer@example.com",
        status=FulfillmentJobStatus.DEAD_LETTER,
        attempt_number=1,
        failure_classification="RuntimeError",
        last_error="worker infrastructure failure",
        payload={},
    )
    session.add(job)
    await session.flush()

    forbidden = await client.post(
        f"/api/v1/admin/fulfillment/jobs/{job.id}/requeue",
        headers=auth(manager_token),
    )
    assert forbidden.status_code == 403

    response = await client.post(
        f"/api/v1/admin/fulfillment/jobs/{job.id}/requeue",
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["attempt_number"] == 2
    assert body["manual_requeue_count"] == 1

    duplicate = await client.post(
        f"/api/v1/admin/fulfillment/jobs/{job.id}/requeue",
        headers=auth(admin_token),
    )
    assert duplicate.status_code == 409

    audit = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant.id,
                AuditLog.user_id == admin.id,
                AuditLog.action == "FULFILLMENT_JOB_REQUEUED",
                AuditLog.resource_id == str(job.id),
            )
        )
    ).scalar_one()
    assert audit.details["attempt_number"] == 2


async def test_manual_requeue_is_blocked_after_canonical_refund(
    fulfillment_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = fulfillment_admin_env["client"]
    session: AsyncSession = fulfillment_admin_env["session"]
    tenant = Tenant(name="Refund Ops", slug=f"refund-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    _, token = await create_identity(session, tenant, role=Role.ADMIN)
    customer, _ = await create_identity(session, tenant, role=Role.CUSTOMER)
    order = await create_order(session, tenant, customer, number="OPS-REFUNDED")
    job = FulfillmentJobRecord(
        tenant_id=tenant.id,
        order_id=order.id,
        recipient="buyer@example.com",
        status=FulfillmentJobStatus.DEAD_LETTER,
        attempt_number=1,
        payload={},
    )
    session.add(job)
    wallet = await LedgerService.get_or_create_wallet(
        session,
        tenant_id=tenant.id,
        user_id=customer.id,
        currency="USD",
    )
    await LedgerService.refund(
        session,
        wallet,
        Decimal("25.00"),
        reference_id=str(order.id),
        reference_type=CANONICAL_REFUND_TYPE,
        description="test compensation",
    )
    await session.flush()

    response = await client.post(
        f"/api/v1/admin/fulfillment/jobs/{job.id}/requeue",
        headers=auth(token),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ORDER_ALREADY_REFUNDED"


async def test_admin_can_run_targeted_reconciliation_without_touching_other_tenant(
    fulfillment_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = fulfillment_admin_env["client"]
    session: AsyncSession = fulfillment_admin_env["session"]
    tenant = Tenant(name="Recon Ops", slug=f"recon-{uuid.uuid4().hex[:6]}", is_active=True)
    other = Tenant(name="Other Recon Ops", slug=f"other-recon-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    admin, token = await create_identity(session, tenant, role=Role.ADMIN)
    customer, _ = await create_identity(session, tenant, role=Role.CUSTOMER)
    other_customer, _ = await create_identity(session, other, role=Role.CUSTOMER)
    order = await create_order(session, tenant, customer, number="OPS-RECON")
    other_order = await create_order(session, other, other_customer, number="OPS-RECON-HIDDEN")
    session.add_all(
        [
            FulfillmentAttempt(
                tenant_id=tenant.id,
                order_id=order.id,
                attempt_number=1,
                idempotency_key=f"order:{order.id}:attempt:1",
                status=FulfillmentStatus.UNKNOWN,
                error_classification="ProviderTimeoutError",
                response_payload={"retryable": True},
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                cost_currency="USD",
            ),
            FulfillmentAttempt(
                tenant_id=other.id,
                order_id=other_order.id,
                attempt_number=1,
                idempotency_key=f"order:{other_order.id}:attempt:1",
                status=FulfillmentStatus.UNKNOWN,
                error_classification="ProviderTimeoutError",
                response_payload={"retryable": True},
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                cost_currency="USD",
            ),
        ]
    )
    await session.flush()

    response = await client.post(
        f"/api/v1/admin/fulfillment/orders/{order.id}/reconcile",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["order_number"] for row in body["results"]] == ["OPS-RECON"]
    assert body["results"][0]["issue_type"] == "STUCK_PROCESSING_WITHOUT_EXTERNAL_ID"

    hidden = await client.post(
        f"/api/v1/admin/fulfillment/orders/{other_order.id}/reconcile",
        headers=auth(token),
    )
    assert hidden.status_code == 404

    audit = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant.id,
                AuditLog.user_id == admin.id,
                AuditLog.action == "FULFILLMENT_RECONCILIATION_RUN",
                AuditLog.resource_id == str(order.id),
            )
        )
    ).scalar_one()
    assert audit.details["result_count"] == 1
