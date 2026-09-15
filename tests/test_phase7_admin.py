import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.commerce.models import Category, Order, OrderItem, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.fulfillment.models import FulfillmentJobRecord, FulfillmentJobStatus
from packages.payments.models import (
    PaymentReconciliationEvent,
    PaymentReconciliationEventStatus,
)
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase7-admin-test-jwt-secret-0123456789abcdef-0123456789abcdef"


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


async def create_catalog(session: AsyncSession, tenant: Tenant, title: str) -> tuple[Product, ProductVariant]:
    category = Category(
        tenant_id=tenant.id,
        name="Digital",
        slug=f"digital-{uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    session.add(category)
    await session.flush()
    product = Product(
        tenant_id=tenant.id,
        category_id=category.id,
        title=title,
        description="Admin test product",
        metadata_json={},
        is_active=True,
    )
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku=f"SKU-{uuid.uuid4().hex[:8]}",
        title="Standard",
        price=Decimal("15.00"),
        currency="USD",
        stock_quantity=10,
        is_active=True,
        attributes={},
    )
    session.add(variant)
    await session.flush()
    return product, variant


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


async def test_admin_static_shell_is_served(admin_env: dict[str, Any]) -> None:
    response = await admin_env["client"].get("/admin/")
    assert response.status_code == 200
    assert "GH Bot Factory Admin" in response.text
    assert "telegram-web-app.js" in response.text


async def test_admin_bootstrap_forbids_customer_and_is_tenant_scoped(
    admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Admin Tenant", slug="admin-tenant", is_active=True)
    other = Tenant(name="Other Tenant", slug="other-admin-tenant", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, customer_token = await create_identity(session, tenant, role=Role.CUSTOMER)
    _, staff_token = await create_identity(session, tenant, role=Role.STAFF)
    await create_catalog(session, tenant, "Visible Product")
    await create_catalog(session, other, "Hidden Product")

    rejected = await client.get("/api/v1/admin/bootstrap", headers=auth(customer_token))
    assert rejected.status_code == 403

    response = await client.get("/api/v1/admin/bootstrap", headers=auth(staff_token))
    assert response.status_code == 200
    body = response.json()
    assert body["store"]["id"] == str(tenant.id)
    assert body["actor"]["role"] == "STAFF"
    assert body["counts"]["products"] == 1
    assert body["counts"]["active_products"] == 1


async def test_staff_can_read_catalog_but_cannot_mutate(admin_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Read Tenant", slug="read-tenant", is_active=True)
    session.add(tenant)
    await session.flush()
    _, token = await create_identity(session, tenant, role=Role.STAFF)
    await create_catalog(session, tenant, "Read Me")

    listing = await client.get("/api/v1/admin/catalog/products", headers=auth(token))
    assert listing.status_code == 200
    assert [row["title"] for row in listing.json()["products"]] == ["Read Me"]

    create = await client.post(
        "/api/v1/admin/catalog/products",
        headers=auth(token),
        json={"title": "Forbidden write", "variants": []},
    )
    assert create.status_code == 403


async def test_manager_can_create_product_with_variant_and_audit_log(
    admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Manager Tenant", slug="manager-tenant", is_active=True)
    session.add(tenant)
    await session.flush()
    manager, token = await create_identity(session, tenant, role=Role.MANAGER)
    category = Category(
        tenant_id=tenant.id,
        name="Licenses",
        slug="licenses",
        is_active=True,
    )
    session.add(category)
    await session.flush()

    response = await client.post(
        "/api/v1/admin/catalog/products",
        headers=auth(token),
        json={
            "title": "Premium License",
            "description": "Instant delivery",
            "category_id": str(category.id),
            "is_active": True,
            "metadata": {"badge": "HOT"},
            "variants": [
                {
                    "sku": "PREMIUM-30",
                    "title": "30 days",
                    "price": "25.50",
                    "currency": "usd",
                    "stock_quantity": 20,
                    "is_active": True,
                    "attributes": {"duration": "30d"},
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Premium License"
    assert body["category_name"] == "Licenses"
    assert body["variants"][0]["currency"] == "USD"
    assert body["variants"][0]["stock_quantity"] == 20

    logs = list(
        (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant.id,
                    AuditLog.user_id == manager.id,
                    AuditLog.action == "PRODUCT_CREATED",
                )
            )
        ).scalars().all()
    )
    assert len(logs) == 1
    assert logs[0].resource_id == body["id"]


async def test_manager_cannot_attach_foreign_tenant_category(admin_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Tenant A", slug="admin-a", is_active=True)
    other = Tenant(name="Tenant B", slug="admin-b", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, token = await create_identity(session, tenant, role=Role.MANAGER)
    foreign = Category(tenant_id=other.id, name="Foreign", slug="foreign", is_active=True)
    session.add(foreign)
    await session.flush()

    response = await client.post(
        "/api/v1/admin/catalog/products",
        headers=auth(token),
        json={"title": "Cross tenant", "category_id": str(foreign.id), "variants": []},
    )
    assert response.status_code == 404


async def test_admin_orders_are_tenant_scoped_and_include_fulfillment_state(
    admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Orders Tenant", slug="orders-tenant", is_active=True)
    other = Tenant(name="Other Orders", slug="other-orders", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, staff_token = await create_identity(session, tenant, role=Role.STAFF)
    customer, _ = await create_identity(session, tenant, role=Role.CUSTOMER, name="Buyer")
    other_customer, _ = await create_identity(session, other, role=Role.CUSTOMER, name="Hidden")
    _, variant = await create_catalog(session, tenant, "Order Product")
    _, other_variant = await create_catalog(session, other, "Hidden Product")

    order = Order(
        tenant_id=tenant.id,
        user_id=customer.id,
        order_number="ORD-ADMIN-001",
        status=OrderStatus.PROCESSING,
        total_amount=Decimal("30.00"),
        currency="USD",
    )
    hidden = Order(
        tenant_id=other.id,
        user_id=other_customer.id,
        order_number="ORD-HIDDEN-001",
        status=OrderStatus.FAILED,
        total_amount=Decimal("99.00"),
        currency="USD",
    )
    session.add_all([order, hidden])
    await session.flush()
    session.add_all(
        [
            OrderItem(
                order_id=order.id,
                product_variant_id=variant.id,
                quantity=2,
                unit_price=Decimal("15.00"),
                total_price=Decimal("30.00"),
            ),
            OrderItem(
                order_id=hidden.id,
                product_variant_id=other_variant.id,
                quantity=1,
                unit_price=Decimal("99.00"),
                total_price=Decimal("99.00"),
            ),
            FulfillmentJobRecord(
                tenant_id=tenant.id,
                order_id=order.id,
                recipient="buyer@example.com",
                status=FulfillmentJobStatus.RUNNING,
                payload={},
            ),
        ]
    )
    await session.flush()

    response = await client.get("/api/v1/admin/orders", headers=auth(staff_token))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [row["order_number"] for row in body["orders"]] == ["ORD-ADMIN-001"]
    assert body["orders"][0]["customer_name"] == "Buyer"
    assert body["orders"][0]["fulfillment_status"] == "RUNNING"
    assert body["orders"][0]["items"][0]["quantity"] == 2


async def test_reconciliation_view_is_tenant_scoped(admin_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Recon Tenant", slug="recon-tenant", is_active=True)
    other = Tenant(name="Other Recon", slug="other-recon", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, token = await create_identity(session, tenant, role=Role.ADMIN)
    session.add_all(
        [
            PaymentReconciliationEvent(
                tenant_id=tenant.id,
                provider="telegram_stars",
                provider_event_id="evt-visible",
                event_type="EXTERNAL_REVERSAL",
                status=PaymentReconciliationEventStatus.MANUAL_REVIEW,
                amount=Decimal("100.00"),
                currency="XTR",
                classification="EXTERNAL_REVERSAL_INSUFFICIENT_FUNDS",
                requires_review=True,
                metadata_json={},
            ),
            PaymentReconciliationEvent(
                tenant_id=other.id,
                provider="telegram_stars",
                provider_event_id="evt-hidden",
                event_type="EXTERNAL_REVERSAL",
                status=PaymentReconciliationEventStatus.MANUAL_REVIEW,
                amount=Decimal("50.00"),
                currency="XTR",
                classification="HIDDEN",
                requires_review=True,
                metadata_json={},
            ),
        ]
    )
    await session.flush()

    response = await client.get(
        "/api/v1/admin/reconciliation-events?requires_review=true",
        headers=auth(token),
    )
    assert response.status_code == 200
    assert [row["provider_event_id"] for row in response.json()] == ["evt-visible"]
