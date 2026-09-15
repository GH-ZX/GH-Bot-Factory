import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from apps.api.v1.storefront import get_checkout_service
from packages.commerce.checkout import CheckoutLine, CheckoutService
from packages.commerce.models import Category, Order, Product, ProductVariant
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.fulfillment.models import FulfillmentJobRecord, FulfillmentJobStatus
from packages.fulfillment.worker import FulfillmentWorker
from packages.payments.models import Wallet
from packages.payments.service import LedgerService
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio

TEST_JWT_SECRET = "phase6-test-jwt-secret-0123456789abcdef-0123456789abcdef"


class NoopFulfillmentService:
    async def execute_order_fulfillment(self, **_: Any) -> None:
        return None


async def create_identity(
    session: AsyncSession,
    tenant: Tenant,
    *,
    role: Role = Role.CUSTOMER,
    telegram_id: int | None = None,
) -> tuple[User, str]:
    user = User(
        telegram_id=telegram_id or int(uuid.uuid4().int % 2_000_000_000),
        username=f"customer_{uuid.uuid4().hex[:8]}",
        first_name="Mini",
        last_name="App",
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


async def create_catalog(
    session: AsyncSession,
    tenant: Tenant,
    *,
    price: Decimal = Decimal("12.50"),
    currency: str = "USD",
    title: str = "Digital Access",
) -> ProductVariant:
    category = Category(
        tenant_id=tenant.id,
        name="Subscriptions",
        slug=f"subscriptions-{uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    session.add(category)
    await session.flush()
    product = Product(
        tenant_id=tenant.id,
        category_id=category.id,
        title=title,
        description="Instant digital delivery.",
        metadata_json={
            "image_url": "https://example.com/product.png",
            "featured": True,
            "internal_supplier_note": "must never reach the storefront",
        },
        is_active=True,
    )
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku=f"SKU-{uuid.uuid4().hex[:8]}",
        title="Standard",
        price=price,
        currency=currency,
        stock_quantity=100,
        is_active=True,
        attributes={"duration": "30d"},
    )
    session.add(variant)
    await session.flush()
    return variant


@pytest_asyncio.fixture
async def storefront_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    checkout_service = CheckoutService(fulfillment_service=NoopFulfillmentService())

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    app.dependency_overrides[get_checkout_service] = lambda: checkout_service

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "session": db_session}

    app.dependency_overrides.clear()


async def test_miniapp_static_shell_is_served(storefront_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = storefront_env["client"]

    response = await client.get("/miniapp/")

    assert response.status_code == 200
    assert "telegram-web-app.js" in response.text
    assert "Your cart" in response.text


async def test_catalog_is_tenant_scoped_and_metadata_is_whitelisted(
    storefront_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = storefront_env["client"]
    session: AsyncSession = storefront_env["session"]
    tenant_a = Tenant(name="Store A", slug="store-a", is_active=True)
    tenant_b = Tenant(name="Store B", slug="store-b", is_active=True)
    session.add_all([tenant_a, tenant_b])
    await session.flush()
    _, token_a = await create_identity(session, tenant_a)
    await create_catalog(session, tenant_a, title="Visible Product")
    await create_catalog(session, tenant_b, title="Hidden Product")

    response = await client.get(
        "/api/v1/storefront/catalog",
        headers={"Authorization": f"Bearer {token_a}"},
    )

    assert response.status_code == 200
    products = response.json()["products"]
    assert [product["title"] for product in products] == ["Visible Product"]
    assert products[0]["metadata"]["featured"] is True
    assert products[0]["metadata"]["image_url"] == "https://example.com/product.png"
    assert "internal_supplier_note" not in products[0]["metadata"]


async def test_catalog_search_pagination_and_availability_are_tenant_scoped(
    storefront_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = storefront_env["client"]
    session: AsyncSession = storefront_env["session"]
    tenant = Tenant(name="Search Store", slug="search-store", is_active=True)
    other = Tenant(name="Other Search Store", slug="other-search-store", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, token = await create_identity(session, tenant)

    alpha = await create_catalog(session, tenant, title="Alpha Access")
    beta = await create_catalog(session, tenant, title="Beta Bundle")
    gamma = await create_catalog(session, tenant, title="Gamma Pack")
    await create_catalog(session, other, title="Beta Hidden")
    gamma.stock_quantity = 0
    await session.flush()

    first_page = await client.get(
        "/api/v1/storefront/catalog?limit=2&offset=0",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first_page.status_code == 200
    payload = first_page.json()
    assert [product["title"] for product in payload["products"]] == [
        "Alpha Access",
        "Beta Bundle",
    ]
    assert payload["total"] == 3
    assert payload["has_more"] is True
    assert payload["next_offset"] == 2

    second_page = await client.get(
        f"/api/v1/storefront/catalog?limit=2&offset={payload['next_offset']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    second_payload = second_page.json()
    assert [product["title"] for product in second_payload["products"]] == ["Gamma Pack"]
    assert second_payload["has_more"] is False
    assert second_payload["next_offset"] is None

    search = await client.get(
        "/api/v1/storefront/catalog?q=beta",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert search.status_code == 200
    assert search.json()["query"] == "beta"
    assert [product["title"] for product in search.json()["products"]] == ["Beta Bundle"]

    in_stock = await client.get(
        "/api/v1/storefront/catalog?available=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert {product["title"] for product in in_stock.json()["products"]} == {
        "Alpha Access",
        "Beta Bundle",
    }

    sold_out = await client.get(
        "/api/v1/storefront/catalog?available=false",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert [product["title"] for product in sold_out.json()["products"]] == ["Gamma Pack"]
    assert alpha.id != beta.id


async def test_catalog_search_matches_active_variant_title_and_sku(
    storefront_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = storefront_env["client"]
    session: AsyncSession = storefront_env["session"]
    tenant = Tenant(name="Variant Search", slug="variant-search", is_active=True)
    session.add(tenant)
    await session.flush()
    _, token = await create_identity(session, tenant)
    variant = await create_catalog(session, tenant, title="Neutral Product")
    variant.title = "Enterprise Annual"
    variant.sku = "ENTERPRISE-365"
    await session.flush()

    by_title = await client.get(
        "/api/v1/storefront/catalog?q=annual",
        headers={"Authorization": f"Bearer {token}"},
    )
    by_sku = await client.get(
        "/api/v1/storefront/catalog?q=enterprise-365",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert [product["title"] for product in by_title.json()["products"]] == ["Neutral Product"]
    assert [product["title"] for product in by_sku.json()["products"]] == ["Neutral Product"]


async def test_bootstrap_exposes_only_current_tenant_user_wallets_and_public_settings(
    storefront_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = storefront_env["client"]
    session: AsyncSession = storefront_env["session"]
    tenant = Tenant(
        name="Neon Store",
        slug="neon-store",
        is_active=True,
        settings={
            "brand_accent": "#7667ff",
            "store_tagline": "Premium digital delivery",
            "provider_api_key": "private-value",
        },
    )
    session.add(tenant)
    await session.flush()
    user, token = await create_identity(session, tenant)
    wallet = await LedgerService.get_or_create_wallet(session, tenant.id, user.id, "USD")
    await LedgerService.credit(session, wallet, Decimal("80.00"), reference_type="TEST")
    await session.flush()

    response = await client.get(
        "/api/v1/storefront/bootstrap",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["store"]["id"] == str(tenant.id)
    assert body["store"]["settings"] == {
        "brand_accent": "#7667ff",
        "store_tagline": "Premium digital delivery",
    }
    assert body["user"]["id"] == str(user.id)
    assert Decimal(body["wallets"][0]["balance"]) == Decimal("80.00")


async def test_checkout_uses_authoritative_price_and_is_idempotent(
    storefront_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = storefront_env["client"]
    session: AsyncSession = storefront_env["session"]
    tenant = Tenant(name="Checkout Store", slug="checkout-store", is_active=True)
    session.add(tenant)
    await session.flush()
    user, token = await create_identity(session, tenant)
    variant = await create_catalog(session, tenant, price=Decimal("12.50"))
    wallet = await LedgerService.get_or_create_wallet(session, tenant.id, user.id, "USD")
    await LedgerService.credit(session, wallet, Decimal("100.00"), reference_type="TEST")
    await session.commit()
    tenant_id = tenant.id
    user_id = user.id

    payload = {
        "items": [{"variant_id": str(variant.id), "quantity": 2}],
        "recipient": "account-12345",
        "idempotency_key": "miniapp-checkout-0001",
    }
    headers = {"Authorization": f"Bearer {token}"}

    injected_price = await client.post(
        "/api/v1/storefront/checkout",
        json={**payload, "price": "0.01"},
        headers=headers,
    )
    assert injected_price.status_code == 422

    first = await client.post("/api/v1/storefront/checkout", json=payload, headers=headers)
    second = await client.post("/api/v1/storefront/checkout", json=payload, headers=headers)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]
    assert Decimal(first.json()["total_amount"]) == Decimal("25.00")

    await session.refresh(wallet)
    assert wallet.balance == Decimal("75.00")
    orders = list(
        (
            await session.execute(
                select(Order).where(
                    Order.tenant_id == tenant_id,
                    Order.user_id == user_id,
                )
            )
        ).scalars().all()
    )
    assert len(orders) == 1
    jobs = list(
        (
            await session.execute(
                select(FulfillmentJobRecord).where(
                    FulfillmentJobRecord.order_id == orders[0].id
                )
            )
        ).scalars().all()
    )
    assert len(jobs) == 1
    assert jobs[0].status == FulfillmentJobStatus.QUEUED
    assert jobs[0].recipient == payload["recipient"]

    changed_payload = {
        **payload,
        "items": [{"variant_id": str(variant.id), "quantity": 3}],
    }
    conflict = await client.post(
        "/api/v1/storefront/checkout",
        json=changed_payload,
        headers=headers,
    )
    assert conflict.status_code == 400
    assert "different checkout request" in conflict.json()["detail"]


async def test_customer_cannot_access_another_users_order(
    storefront_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = storefront_env["client"]
    session: AsyncSession = storefront_env["session"]
    tenant = Tenant(name="Order Store", slug="order-store", is_active=True)
    session.add(tenant)
    await session.flush()
    user_a, token_a = await create_identity(session, tenant)
    user_b, _ = await create_identity(session, tenant)
    order_b = Order(
        tenant_id=tenant.id,
        user_id=user_b.id,
        order_number=f"ORD-{uuid.uuid4().hex[:8].upper()}",
        total_amount=Decimal("5.00"),
        currency="USD",
    )
    session.add(order_b)
    await session.commit()

    detail_response = await client.get(
        f"/api/v1/storefront/orders/{order_b.id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    list_response = await client.get(
        "/api/v1/storefront/orders",
        headers={"Authorization": f"Bearer {token_a}"},
    )

    assert user_a.id != user_b.id
    assert detail_response.status_code == 403
    assert list_response.status_code == 200
    assert list_response.json() == []

async def test_checkout_rejects_cross_tenant_variant(
    storefront_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = storefront_env["client"]
    session: AsyncSession = storefront_env["session"]
    tenant_a = Tenant(name="Buyer Store", slug="buyer-store", is_active=True)
    tenant_b = Tenant(name="Foreign Store", slug="foreign-store", is_active=True)
    session.add_all([tenant_a, tenant_b])
    await session.flush()
    user_a, token_a = await create_identity(session, tenant_a)
    foreign_variant = await create_catalog(session, tenant_b, price=Decimal("20.00"))
    wallet = await LedgerService.get_or_create_wallet(session, tenant_a.id, user_a.id, "USD")
    await LedgerService.credit(session, wallet, Decimal("100.00"), reference_type="TEST")
    await session.commit()

    response = await client.post(
        "/api/v1/storefront/checkout",
        json={
            "items": [{"variant_id": str(foreign_variant.id), "quantity": 1}],
            "recipient": "customer-destination",
            "idempotency_key": "cross-tenant-checkout-0001",
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )

    assert response.status_code == 400
    assert "unavailable" in response.json()["detail"]
    await session.refresh(wallet)
    assert wallet.balance == Decimal("100.00")


async def test_inactive_membership_cannot_use_storefront(
    storefront_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = storefront_env["client"]
    session: AsyncSession = storefront_env["session"]
    tenant = Tenant(name="Membership Store", slug="membership-store", is_active=True)
    session.add(tenant)
    await session.flush()
    user, token = await create_identity(session, tenant)
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.tenant_id == tenant.id,
                Membership.user_id == user.id,
            )
        )
    ).scalar_one()
    membership.is_active = False
    await session.commit()

    response = await client.get(
        "/api/v1/storefront/bootstrap",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert "inactive" in response.json()["detail"].lower()


async def test_worker_poll_discovers_checkout_durable_job(
    db_session: AsyncSession,
    db_session_factory,
) -> None:
    tenant = Tenant(name="Worker Store", slug="worker-store", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    user, _ = await create_identity(db_session, tenant)
    variant = await create_catalog(db_session, tenant, price=Decimal("5.00"))
    wallet = await LedgerService.get_or_create_wallet(
        db_session, tenant.id, user.id, "USD"
    )
    await LedgerService.credit(
        db_session, wallet, Decimal("20.00"), reference_type="TEST"
    )
    await db_session.commit()

    checkout_service = CheckoutService(fulfillment_service=NoopFulfillmentService())
    order, attempt = await checkout_service.checkout_cart(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        lines=[CheckoutLine(variant_id=variant.id, quantity=1)],
        recipient="worker-recipient",
        execute_sync=False,
        enqueue_durable=True,
        idempotency_key="worker-poll-checkout-0001",
    )
    assert attempt is None

    worker = FulfillmentWorker(session_factory=db_session_factory)
    async with db_session_factory() as polling_session:
        scheduled = await worker.poll_queued_jobs(polling_session)
        duplicate_poll = await worker.poll_queued_jobs(polling_session)

    assert scheduled == 1
    assert duplicate_poll == 0
    assert worker.queue.qsize() == 1
    queued_job = await worker.queue.get()
    assert queued_job.order_id == order.id
    assert queued_job.recipient == "worker-recipient"
    worker.queue.task_done()


async def test_checkout_rolls_back_debit_when_durable_job_persistence_fails(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(name="Atomic Store", slug="atomic-store", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    user, _ = await create_identity(db_session, tenant)
    variant = await create_catalog(db_session, tenant, price=Decimal("7.00"))
    wallet = await LedgerService.get_or_create_wallet(
        db_session, tenant.id, user.id, "USD"
    )
    await LedgerService.credit(
        db_session, wallet, Decimal("20.00"), reference_type="TEST"
    )
    await db_session.commit()
    tenant_id = tenant.id
    user_id = user.id

    def reject_job_insert(*_: Any) -> None:
        raise RuntimeError("simulated durable job persistence failure")

    event.listen(FulfillmentJobRecord, "before_insert", reject_job_insert)
    try:
        with pytest.raises(RuntimeError, match="simulated durable job persistence failure"):
            await CheckoutService(fulfillment_service=NoopFulfillmentService()).checkout_cart(
                session=db_session,
                tenant_id=tenant_id,
                user_id=user_id,
                lines=[CheckoutLine(variant_id=variant.id, quantity=1)],
                recipient="atomic-recipient",
                execute_sync=False,
                enqueue_durable=True,
                idempotency_key="atomic-checkout-0001",
            )
    finally:
        event.remove(FulfillmentJobRecord, "before_insert", reject_job_insert)
        await db_session.rollback()

    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("20.00")
    orders = list(
        (
            await db_session.execute(
                select(Order).where(
                    Order.tenant_id == tenant_id,
                    Order.user_id == user_id,
                )
            )
        ).scalars().all()
    )
    assert orders == []
