from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Order, OrderItem, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.fulfillment.models import FulfillmentAttempt, FulfillmentStatus
from packages.fulfillment.reconciliation import ReconciliationService
from packages.fulfillment.service import FulfillmentService
from packages.fulfillment.worker import FulfillmentWorker
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.models import Provider, ProviderProductMapping
from packages.tenants.models import Tenant, User


@pytest.mark.asyncio
async def test_fulfillment_idempotency_prevents_duplicate_orders(db_session: AsyncSession):
    tenant = Tenant(name="Idempotency Store", slug="idem-store")
    user = User(username="idem_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Idem Product")
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(product_id=product.id, sku="SKU-IDEM", title="Idem Variant", price=Decimal("15.00"))
    db_session.add(variant)
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="IdemVendor", slug="idem-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    mapping = ProviderProductMapping(
        tenant_id=tenant.id,
        provider_id=prov.id,
        product_id=product.id,
        product_variant_id=variant.id,
        external_product_id="ext-idem",
    )
    db_session.add(mapping)

    order = Order(
        tenant_id=tenant.id,
        user_id=user.id,
        order_number="ORD-IDEM-100",
        status=OrderStatus.PAID,
        total_amount=Decimal("15.00"),
    )
    db_session.add(order)
    await db_session.flush()

    item = OrderItem(order_id=order.id, product_variant_id=variant.id, quantity=1, unit_price=Decimal("15.00"), total_price=Decimal("15.00"))
    db_session.add(item)
    await db_session.commit()

    service = FulfillmentService()

    # Attempt 1: Initial Fulfillment
    attempt1 = await service.execute_order_fulfillment(
        session=db_session,
        order_id=order.id,
        recipient="@buyer",
        attempt_number=1,
    )
    assert attempt1.status == FulfillmentStatus.SUCCEEDED
    ext_id_1 = attempt1.external_order_id

    # Attempt 2: Exact same order and attempt key (Simulating worker retry / duplicate webhook)
    attempt2 = await service.execute_order_fulfillment(
        session=db_session,
        order_id=order.id,
        recipient="@buyer",
        attempt_number=1,
    )
    assert attempt2.status == FulfillmentStatus.SUCCEEDED
    assert attempt2.external_order_id == ext_id_1
    assert attempt2.id == attempt1.id

    # Verify only 1 FulfillmentAttempt was persisted
    stmt = select(FulfillmentAttempt).where(FulfillmentAttempt.order_id == order.id)
    attempts = (await db_session.execute(stmt)).scalars().all()
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_fulfillment_worker_queue_and_execution(db_session: AsyncSession, db_session_factory):
    tenant = Tenant(name="Worker Store", slug="worker-store")
    user = User(username="worker_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Worker Product")
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(product_id=product.id, sku="SKU-WORK", title="Worker Variant", price=Decimal("20.00"))
    db_session.add(variant)
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="WorkerVendor", slug="worker-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    mapping = ProviderProductMapping(tenant_id=tenant.id, provider_id=prov.id, product_id=product.id, product_variant_id=variant.id, external_product_id="ext-work")
    db_session.add(mapping)

    order = Order(tenant_id=tenant.id, user_id=user.id, order_number="ORD-WORK-200", status=OrderStatus.PAID, total_amount=Decimal("20.00"))
    db_session.add(order)
    await db_session.flush()

    item = OrderItem(order_id=order.id, product_variant_id=variant.id, quantity=1, unit_price=Decimal("20.00"), total_price=Decimal("20.00"))
    db_session.add(item)
    await db_session.commit()

    worker = FulfillmentWorker(session_factory=db_session_factory)
    await worker.enqueue(order_id=order.id, recipient="@buyer")
    assert worker.queue.qsize() == 1

    # Execute one job synchronously
    res = await worker.process_one_now()
    assert res is not None
    assert res.status == FulfillmentStatus.SUCCEEDED

    await db_session.refresh(order)
    assert order.status == OrderStatus.FULFILLED


@pytest.mark.asyncio
async def test_reconciliation_service_synchronizes_stuck_order(db_session: AsyncSession):
    tenant = Tenant(name="Recon Store", slug="recon-store")
    user = User(username="recon_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="ReconVendor", slug="recon-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    order = Order(
        tenant_id=tenant.id,
        user_id=user.id,
        order_number="ORD-RECON-300",
        status=OrderStatus.PROCESSING,
        total_amount=Decimal("50.00"),
    )
    db_session.add(order)
    await db_session.flush()

    # Simulate an external order that completed upstream, but internal attempt got stuck in PROCESSING
    mock_client = MockProvider(provider_name="ReconVendor")
    mock_client.orders["ext-recon-999"] = {"status": "COMPLETED", "cost": Decimal("5.00")}

    registry = ProviderClientRegistry()
    registry.register_singleton(str(prov.id), mock_client)

    attempt = FulfillmentAttempt(
        tenant_id=tenant.id,
        order_id=order.id,
        provider_id=prov.id,
        attempt_number=1,
        idempotency_key=f"order:{order.id}:attempt:1",
        status=FulfillmentStatus.PROCESSING,  # Stuck!
        external_order_id="ext-recon-999",
    )
    db_session.add(attempt)
    await db_session.commit()

    recon_service = ReconciliationService(registry=registry)
    discrepancies = await recon_service.scan_and_reconcile_tenant(db_session, tenant.id)

    assert len(discrepancies) == 1
    assert discrepancies[0].issue_type == "OUT_OF_SYNC_RESOLVED"

    # Verify attempt and order synchronized to SUCCEEDED and FULFILLED
    await db_session.refresh(attempt)
    await db_session.refresh(order)
    assert attempt.status == FulfillmentStatus.SUCCEEDED
    assert order.status == OrderStatus.FULFILLED
