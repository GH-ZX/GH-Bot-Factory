import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.commerce.models import Order, OrderItem, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.fulfillment.models import (
    FulfillmentAttempt,
    FulfillmentJobRecord,
    FulfillmentJobStatus,
    FulfillmentStatus,
)
from packages.fulfillment.reconciliation import ReconciliationService
from packages.fulfillment.service import FulfillmentService
from packages.fulfillment.worker import FulfillmentWorker
from packages.notifications.service import (
    MockNotificationTransport,
    NotificationEventType,
    NotificationService,
)
from packages.payments.models import LedgerTransaction, TransactionType
from packages.payments.service import CANONICAL_REFUND_TYPE, LedgerService
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.exceptions import (
    ProviderAuthenticationError,
    ProviderError,
)
from packages.providers.models import Provider, ProviderProductMapping
from packages.providers.router import ProviderRouter
from packages.tenants.models import Tenant, User


@pytest.mark.asyncio
async def test_retryable_failure_sets_retrying_and_does_not_prematurely_notify_refund(db_session: AsyncSession):
    """Verifies that transient retryable errors set RETRYING/UNKNOWN, do NOT refund, and do NOT lie to the user."""
    tenant = Tenant(name="Retry Store", slug="retry-store")
    user = User(username="retry_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Stars Package")
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(product_id=product.id, sku="STARS-50", title="50 Stars", price=Decimal("5.00"))
    db_session.add(variant)
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="TimeoutVendor", slug="timeout-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    mapping = ProviderProductMapping(tenant_id=tenant.id, provider_id=prov.id, product_id=product.id, product_variant_id=variant.id, external_product_id="mock-stars")
    db_session.add(mapping)

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    await LedgerService.credit(db_session, wallet, Decimal("50.00"), description="Initial Deposit")

    order = Order(
        tenant_id=tenant.id,
        user_id=user.id,
        order_number="ORD-RETRY-001",
        status=OrderStatus.PAID,
        total_amount=Decimal("5.00"),
    )
    db_session.add(order)
    await db_session.flush()

    item = OrderItem(order_id=order.id, product_variant_id=variant.id, quantity=1, unit_price=Decimal("5.00"), total_price=Decimal("5.00"))
    db_session.add(item)
    await db_session.commit()

    # Configure mock provider to fail with timeout
    mock_client = MockProvider(provider_name="TimeoutVendor")
    mock_client.fail_with_timeout = True

    registry = ProviderClientRegistry()
    registry.register_singleton(str(prov.id), mock_client)
    router = ProviderRouter(registry=registry)

    mock_transport = MockNotificationTransport()
    notif_service = NotificationService(transports=[mock_transport])
    fulfillment_service = FulfillmentService(router=router, notification_service=notif_service)

    # Execute attempt 1 (transient failure)
    with pytest.raises(ProviderError):
        await fulfillment_service.execute_order_fulfillment(
            session=db_session,
            order_id=order.id,
            recipient="@buyer",
            attempt_number=1,
        )

    # Refresh order and wallet
    await db_session.refresh(order)
    await db_session.refresh(wallet)

    # 1. State Invariant: order must NOT be FAILED or CANCELLED on retryable attempt 1
    assert order.status != OrderStatus.FAILED
    assert order.status != OrderStatus.CANCELLED

    # 2. Financial Invariant: Wallet must NOT be refunded yet
    assert wallet.balance == Decimal("50.00")

    # 3. Attempt Status: must be UNKNOWN or RETRYING, NOT FAILED
    stmt = select(FulfillmentAttempt).where(FulfillmentAttempt.order_id == order.id)
    attempt = (await db_session.execute(stmt)).scalar_one()
    assert attempt.status in (FulfillmentStatus.UNKNOWN, FulfillmentStatus.RETRYING)

    # 4. Notification Invariant: Must NOT claim user was refunded
    sent_events = [n.event_type for n in mock_transport.sent_notifications]
    assert NotificationEventType.ORDER_REFUNDED not in sent_events
    for notif in mock_transport.sent_notifications:
        assert "refunded" not in notif.message.lower()


@pytest.mark.asyncio
async def test_refund_strict_idempotency_prevents_double_crediting(db_session: AsyncSession):
    """Verifies that LedgerService.refund executed multiple times with identical references credits wallet exactly once."""
    tenant = Tenant(name="Idem Refund Store", slug="idem-ref-store")
    user = User(username="ref_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    await LedgerService.credit(db_session, wallet, Decimal("100.00"), description="Initial Deposit")
    await db_session.commit()

    order_ref = str(uuid.uuid4())

    # Call 1: Refund 25.00
    tx1 = await LedgerService.refund(
        session=db_session,
        wallet=wallet,
        amount=Decimal("25.00"),
        reference_id=order_ref,
        reference_type=CANONICAL_REFUND_TYPE,
        description="First refund execution",
    )
    await db_session.commit()
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("125.00")

    # Call 2: Duplicate Refund attempt for the same order reference
    tx2 = await LedgerService.refund(
        session=db_session,
        wallet=wallet,
        amount=Decimal("25.00"),
        reference_id=order_ref,
        reference_type=CANONICAL_REFUND_TYPE,
        description="Duplicate retry refund execution",
    )
    await db_session.commit()
    await db_session.refresh(wallet)

    # Financial Invariant: Wallet balance remains 125.00 (NOT 150.00)
    assert wallet.balance == Decimal("125.00")
    assert tx1.id == tx2.id

    # Verify only ONE refund transaction exists for this reference
    stmt = select(LedgerTransaction).where(
        LedgerTransaction.wallet_id == wallet.id,
        LedgerTransaction.transaction_type == TransactionType.REFUND,
        LedgerTransaction.reference_id == order_ref,
    )
    refund_txs = (await db_session.execute(stmt)).scalars().all()
    assert len(refund_txs) == 1


@pytest.mark.asyncio
async def test_reconciliation_executes_automated_financial_refund_on_confirmed_provider_failure(db_session: AsyncSession):
    """Verifies that ReconciliationService executes an automated ledger refund when upstream confirms an order failed."""
    tenant = Tenant(name="Recon Refund Store", slug="recon-ref-store")
    user = User(username="recon_ref_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    await LedgerService.credit(db_session, wallet, Decimal("10.00"), description="Deposit")

    # Simulate purchase debit: 10.00 -> 0.00
    await LedgerService.debit(db_session, wallet, Decimal("10.00"), description="Purchase")
    await db_session.commit()
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("0.00")

    prov = Provider(tenant_id=tenant.id, name="ReconFailVendor", slug="recon-fail-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    order = Order(
        tenant_id=tenant.id,
        user_id=user.id,
        order_number="ORD-RECON-FAIL-1",
        status=OrderStatus.PROCESSING,
        total_amount=Decimal("10.00"),
    )
    db_session.add(order)
    await db_session.flush()

    attempt = FulfillmentAttempt(
        tenant_id=tenant.id,
        order_id=order.id,
        provider_id=prov.id,
        attempt_number=1,
        idempotency_key=f"order:{order.id}:attempt:1",
        status=FulfillmentStatus.PROCESSING,
        external_order_id="ext-fail-999",
    )
    db_session.add(attempt)
    await db_session.commit()

    # Configure MockProvider to report this order as FAILED
    mock_client = MockProvider(provider_name="ReconFailVendor")
    mock_client.orders["ext-fail-999"] = {
        "external_order_id": "ext-fail-999",
        "status": "FAILED",
        "cost": "0.00",
    }

    registry = ProviderClientRegistry()
    registry.register_singleton(str(prov.id), mock_client)

    mock_transport = MockNotificationTransport()
    notif_service = NotificationService(transports=[mock_transport])
    recon_service = ReconciliationService(registry=registry, notification_service=notif_service)

    discrepancies = await recon_service.scan_and_reconcile_tenant(db_session, tenant.id)
    assert len(discrepancies) == 1
    assert discrepancies[0].issue_type == "UPSTREAM_FAILED_RESOLVED"

    # Invariants: Attempt is FAILED, Order is FAILED, Wallet balance restored to 10.00
    await db_session.refresh(attempt)
    await db_session.refresh(order)
    await db_session.refresh(wallet)

    assert attempt.status == FulfillmentStatus.FAILED
    assert order.status == OrderStatus.FAILED
    assert wallet.balance == Decimal("10.00")

    # Verify notification sent to customer
    events = [n.event_type for n in mock_transport.sent_notifications]
    assert NotificationEventType.ORDER_REFUNDED in events


@pytest.mark.asyncio
async def test_worker_crash_recovery_resumes_abandoned_jobs(db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]):
    """Verifies that durable fulfillment jobs persisted in DB are recovered on worker restart after process crash."""
    tenant = Tenant(name="Crash Store", slug="crash-store")
    user = User(username="crash_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Durable Product")
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(product_id=product.id, sku="SKU-DURABLE", title="Durable Variant", price=Decimal("12.00"))
    db_session.add(variant)
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="CrashVendor", slug="crash-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    mapping = ProviderProductMapping(tenant_id=tenant.id, provider_id=prov.id, product_id=product.id, product_variant_id=variant.id, external_product_id="ext-durable")
    db_session.add(mapping)

    order = Order(
        tenant_id=tenant.id,
        user_id=user.id,
        order_number="ORD-CRASH-001",
        status=OrderStatus.PAID,
        total_amount=Decimal("12.00"),
    )
    db_session.add(order)
    await db_session.flush()

    item = OrderItem(order_id=order.id, product_variant_id=variant.id, quantity=1, unit_price=Decimal("12.00"), total_price=Decimal("12.00"))
    db_session.add(item)
    await db_session.commit()

    # Simulate crash: An enqueued job record exists in the DB marked QUEUED, but the in-memory queue died
    abandoned_job = FulfillmentJobRecord(
        tenant_id=tenant.id,
        order_id=order.id,
        recipient="@survivor",
        attempt_number=1,
        status=FulfillmentJobStatus.QUEUED,
    )
    db_session.add(abandoned_job)
    await db_session.commit()

    # New worker instance starts up after "server reboot"
    worker = FulfillmentWorker(session_factory=db_session_factory)
    assert worker.queue.empty()

    # Run recovery on startup
    recovered_count = await worker.recover_pending_jobs(db_session)
    assert recovered_count == 1
    assert worker.queue.qsize() == 1

    # Execute recovered job
    result = await worker.process_one_now()
    assert result is not None
    assert result.status == FulfillmentStatus.SUCCEEDED

    await db_session.refresh(abandoned_job)
    assert abandoned_job.status == FulfillmentJobStatus.COMPLETED

    await db_session.refresh(order)
    assert order.status == OrderStatus.FULFILLED


@pytest.mark.asyncio
async def test_provider_accepted_then_app_crashed_reconciliation_prevents_duplicate(db_session: AsyncSession):
    """Scenario 9: Upstream provider accepted order, but app crashed before saving external_order_id.
    
    Reconciliation queries provider via idempotency key and syncs external_order_id without double-purchasing.
    """
    tenant = Tenant(name="Scen9 Store", slug="scen9-store")
    user = User(username="scen9_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="Scen9Vendor", slug="scen9-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    order = Order(
        tenant_id=tenant.id,
        user_id=user.id,
        order_number="ORD-SCEN9-999",
        status=OrderStatus.PROCESSING,
        total_amount=Decimal("15.00"),
    )
    db_session.add(order)
    await db_session.flush()

    idempotency_key = f"order:{order.id}:attempt:1"

    # Upstream MockProvider already accepted the order
    mock_client = MockProvider(provider_name="Scen9Vendor")
    ext_id = "ext-scen9-success-abc"
    mock_client.orders[ext_id] = {
        "external_order_id": ext_id,
        "status": "COMPLETED",
        "cost": "1.25",
        "idempotency_key": idempotency_key,
    }
    mock_client.idempotency_map[idempotency_key] = ext_id

    # Internal state in DB was left in UNKNOWN state without external_order_id due to crash
    attempt = FulfillmentAttempt(
        tenant_id=tenant.id,
        order_id=order.id,
        provider_id=prov.id,
        attempt_number=1,
        idempotency_key=idempotency_key,
        status=FulfillmentStatus.UNKNOWN,
        external_order_id=None,  # Not saved before crash!
    )
    db_session.add(attempt)
    await db_session.commit()

    registry = ProviderClientRegistry()
    registry.register_singleton(str(prov.id), mock_client)
    recon_service = ReconciliationService(registry=registry)

    # Reconciliation scans and recovers
    discrepancies = await recon_service.scan_and_reconcile_tenant(db_session, tenant.id)
    assert len(discrepancies) == 1
    assert discrepancies[0].issue_type == "OUT_OF_SYNC_RESOLVED"

    await db_session.refresh(attempt)
    await db_session.refresh(order)

    # Invariants: Attempt is SUCCEEDED, external_order_id was recovered, Order is FULFILLED
    assert attempt.status == FulfillmentStatus.SUCCEEDED
    assert attempt.external_order_id == ext_id
    assert order.status == OrderStatus.FULFILLED

    # Exactly 1 order in provider, NO duplicate purchase
    assert len(mock_client.orders) == 1


@pytest.mark.asyncio
async def test_customer_facing_notifications_do_not_leak_raw_exceptions(db_session: AsyncSession):
    """Verifies that internal raw exception strings and provider credentials are never leaked in customer notifications."""
    tenant = Tenant(name="Security Store", slug="sec-store")
    user = User(username="sec_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Sec Product")
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(product_id=product.id, sku="SEC-1", title="Sec Variant", price=Decimal("10.00"))
    db_session.add(variant)
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="SecretVendor", slug="sec-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    mapping = ProviderProductMapping(tenant_id=tenant.id, provider_id=prov.id, product_id=product.id, product_variant_id=variant.id, external_product_id="mock-sec")
    db_session.add(mapping)

    order = Order(
        tenant_id=tenant.id,
        user_id=user.id,
        order_number="ORD-SEC-007",
        status=OrderStatus.PAID,
        total_amount=Decimal("10.00"),
    )
    db_session.add(order)
    await db_session.flush()

    item = OrderItem(order_id=order.id, product_variant_id=variant.id, quantity=1, unit_price=Decimal("10.00"), total_price=Decimal("10.00"))
    db_session.add(item)
    await db_session.commit()

    # Mock provider throws an exception with sensitive internal details
    mock_client = MockProvider(provider_name="SecretVendor")
    mock_client.fail_with_auth_error = True

    registry = ProviderClientRegistry()
    registry.register_singleton(str(prov.id), mock_client)
    router = ProviderRouter(registry=registry)

    mock_transport = MockNotificationTransport()
    notif_service = NotificationService(transports=[mock_transport])
    fulfillment_service = FulfillmentService(router=router, notification_service=notif_service)

    with pytest.raises(ProviderAuthenticationError):
        await fulfillment_service.execute_order_fulfillment(
            session=db_session,
            order_id=order.id,
            recipient="@customer",
            attempt_number=1,
        )

    # Inspect all notifications sent to the user
    for notif in mock_transport.sent_notifications:
        assert "ProviderAuthenticationError" not in notif.message
        assert "Invalid or expired API token" not in notif.message
        assert "secret" not in notif.message.lower()


@pytest.mark.asyncio
async def test_multi_item_order_fulfillment_success(db_session: AsyncSession):
    """Verifies that an order with multiple distinct items fulfills all items cleanly."""
    tenant = Tenant(name="Multi Item Store", slug="multi-item-store")
    user = User(username="multi_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    product1 = Product(tenant_id=tenant.id, title="Product 1")
    product2 = Product(tenant_id=tenant.id, title="Product 2")
    db_session.add_all([product1, product2])
    await db_session.flush()

    variant1 = ProductVariant(product_id=product1.id, sku="SKU-MULTI-1", title="Item 1", price=Decimal("10.00"))
    variant2 = ProductVariant(product_id=product2.id, sku="SKU-MULTI-2", title="Item 2", price=Decimal("20.00"))
    db_session.add_all([variant1, variant2])
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="MultiVendor", slug="multi-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    mapping1 = ProviderProductMapping(tenant_id=tenant.id, provider_id=prov.id, product_id=product1.id, product_variant_id=variant1.id, external_product_id="ext-multi-1")
    mapping2 = ProviderProductMapping(tenant_id=tenant.id, provider_id=prov.id, product_id=product2.id, product_variant_id=variant2.id, external_product_id="ext-multi-2")
    db_session.add_all([mapping1, mapping2])

    order = Order(
        tenant_id=tenant.id,
        user_id=user.id,
        order_number="ORD-MULTI-100",
        status=OrderStatus.PAID,
        total_amount=Decimal("30.00"),
    )
    db_session.add(order)
    await db_session.flush()

    item1 = OrderItem(order_id=order.id, product_variant_id=variant1.id, quantity=1, unit_price=Decimal("10.00"), total_price=Decimal("10.00"))
    item2 = OrderItem(order_id=order.id, product_variant_id=variant2.id, quantity=1, unit_price=Decimal("20.00"), total_price=Decimal("20.00"))
    db_session.add_all([item1, item2])
    await db_session.commit()

    service = FulfillmentService()
    attempt = await service.execute_order_fulfillment(
        session=db_session,
        order_id=order.id,
        recipient="@multi_buyer",
        attempt_number=1,
    )

    assert attempt.status == FulfillmentStatus.SUCCEEDED
    await db_session.refresh(order)
    assert order.status == OrderStatus.FULFILLED
