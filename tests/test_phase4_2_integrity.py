import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.commerce.checkout import CheckoutService
from packages.commerce.models import Order, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.core.models import Tenant, User
from packages.fulfillment.models import FulfillmentJobRecord, FulfillmentJobStatus
from packages.fulfillment.service import FulfillmentService
from packages.fulfillment.worker import FulfillmentJob, FulfillmentWorker
from packages.notifications.service import NotificationService
from packages.payments.models import LedgerTransaction, TransactionType
from packages.payments.service import CANONICAL_REFUND_TYPE, LedgerService
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.exceptions import ProviderError
from packages.providers.models import Provider, ProviderProductMapping
from packages.providers.router import ProviderRouter


@pytest.mark.asyncio
async def test_canonical_refund_idempotency_across_services(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
):
    """Scenario 1: Canonical refund idempotency across FulfillmentService and ReconciliationService.

    Ensures that when FulfillmentService encounters terminal failure and refunds an order,
    and a subsequent ReconciliationService process sweeps the exact same order,
    strictly ONE refund ledger transaction and wallet credit occurs.
    """
    tenant = Tenant(name="Canon Tenant", slug="canon-tenant")
    user = User(username="canon_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Canon Product")
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(
        product_id=product.id,
        sku="CANON-SKU",
        title="Canon SKU",
        price=Decimal("40.00"),
    )
    db_session.add(variant)
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="FailingVendor", slug="failing-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    mapping = ProviderProductMapping(
        tenant_id=tenant.id,
        provider_id=prov.id,
        product_id=product.id,
        product_variant_id=variant.id,
        external_product_id="ext-failing",
    )
    db_session.add(mapping)

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    await LedgerService.credit(db_session, wallet, Decimal("100.00"), description="Deposit")
    await db_session.commit()

    mock_client = MockProvider(provider_name="FailingVendor")
    mock_client.fail_with_auth_error = True

    registry = ProviderClientRegistry()
    registry.register_singleton(str(prov.id), mock_client)

    notif_service = NotificationService()
    router = ProviderRouter(registry=registry)
    fulfillment_service = FulfillmentService(router=router, notification_service=notif_service)
    checkout_service = CheckoutService(
        fulfillment_service=fulfillment_service,
        notification_service=notif_service,
    )

    # Checkout 1 item: debited 40.00 -> balance becomes 60.00 -> fulfillment fails permanently -> refunded 40.00 -> balance restored to 100.00
    with pytest.raises(ProviderError):
        await checkout_service.checkout(
            session=db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            variant_id=variant.id,
            quantity=1,
            recipient="@buyer",
            execute_sync=True,
        )

    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("100.00")

    # Fetch the order created
    stmt_order = select(Order).where(Order.tenant_id == tenant.id)
    order = (await db_session.execute(stmt_order)).scalars().first()
    assert order is not None
    assert order.status == OrderStatus.FAILED

    # Find the refund transaction created by FulfillmentService
    stmt_tx = select(LedgerTransaction).where(
        LedgerTransaction.wallet_id == wallet.id,
        LedgerTransaction.reference_id == str(order.id),
    )
    refund_txs = (await db_session.execute(stmt_tx)).scalars().all()
    assert len(refund_txs) == 1
    assert refund_txs[0].reference_type == CANONICAL_REFUND_TYPE
    assert refund_txs[0].amount == Decimal("40.00")

    # Now simulate ReconciliationService sweeping this order
    recon_tx = await LedgerService.refund(
        session=db_session,
        wallet=wallet,
        amount=order.total_amount,
        reference_id=str(order.id),
        reference_type=CANONICAL_REFUND_TYPE,
        description="Reconciliation duplicate refund attempt",
    )
    await db_session.commit()
    await db_session.refresh(wallet)

    # Invariant: Must return the SAME transaction, balance unchanged!
    assert recon_tx.id == refund_txs[0].id
    assert wallet.balance == Decimal("100.00")

    # Verify strictly 1 refund transaction exists
    refund_txs_after = (await db_session.execute(stmt_tx)).scalars().all()
    assert len(refund_txs_after) == 1

    # Audit ledger balance
    reconstructed, is_valid = await LedgerService.reconstruct_and_verify_balance(db_session, wallet.id, tenant.id)
    assert is_valid is True
    assert reconstructed == Decimal("100.00")


@pytest.mark.asyncio
async def test_durable_enqueue_db_failure_fails_closed():
    """Scenario 2: Durable enqueue must fail closed on database persistence failure.

    If persist_db=True and writing FulfillmentJobRecord fails, enqueue() must raise
    RuntimeError and never place the job into the in-memory queue.
    """
    class BrokenSessionFactory:
        def __call__(self):
            raise ConnectionError("Database cluster unreachable")

    worker = FulfillmentWorker(session_factory=BrokenSessionFactory())
    dummy_order_id = uuid.uuid4()

    assert worker.queue.empty()

    with pytest.raises(RuntimeError, match="Durable enqueue failed for order"):
        await worker.enqueue(
            order_id=dummy_order_id,
            recipient="@buyer",
            persist_db=True,
        )

    # Crucial assertion: In-memory queue remains strictly empty (fail-closed)
    assert worker.queue.empty()

    # But non-durable enqueue succeeds
    job = await worker.enqueue(
        order_id=dummy_order_id,
        recipient="@buyer",
        persist_db=False,
    )
    assert not worker.queue.empty()
    assert worker.queue.qsize() == 1
    assert job.job_id is None


@pytest.mark.asyncio
async def test_concurrent_worker_atomic_job_claim(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
):
    """Scenario 3: Atomic conditional update prevents concurrent workers from claiming the same job.

    UPDATE fulfillment_jobs SET status='RUNNING', locked_at=... WHERE id=... AND status='QUEUED'
    Exactly one concurrent worker must claim it (rowcount=1); the second must fail (rowcount=0).
    """
    tenant_id = uuid.uuid4()
    order_id = uuid.uuid4()
    job_id = uuid.uuid4()

    job_rec = FulfillmentJobRecord(
        id=job_id,
        tenant_id=tenant_id,
        order_id=order_id,
        recipient="@concurrent_buyer",
        attempt_number=1,
        status=FulfillmentJobStatus.QUEUED,
    )
    db_session.add(job_rec)
    await db_session.commit()

    async def worker_attempt_claim():
        async with db_session_factory() as session:
            return await FulfillmentWorker.claim_job(session, job_id)

    # Spawn two concurrent claim tasks
    res1, res2 = await asyncio.gather(
        worker_attempt_claim(),
        worker_attempt_claim(),
    )

    # Exactly one succeeded, one failed
    results = [res1, res2]
    assert results.count(True) == 1
    assert results.count(False) == 1

    # Verify in DB: record is RUNNING with locked_at populated
    await db_session.refresh(job_rec)
    assert job_rec.status == FulfillmentJobStatus.RUNNING
    assert job_rec.locked_at is not None

    # Third attempt after it is already running returns False
    async with db_session_factory() as session:
        third_attempt = await FulfillmentWorker.claim_job(session, job_id)
        assert third_attempt is False


@pytest.mark.asyncio
async def test_refund_amount_uses_frozen_order_total(db_session: AsyncSession):
    """Scenario 4: Refund amount strictly uses frozen order.total_amount, immune to catalog price changes.

    Verifies that if catalog variant price changes after checkout, the refund is still
    issued for the exact historical order.total_amount debited from the customer.
    """
    tenant = Tenant(name="PriceFreeze Store", slug="price-freeze")
    user = User(username="freeze_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Frozen Product")
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(
        product_id=product.id,
        sku="FREEZE-SKU",
        title="Frozen Variant",
        price=Decimal("20.00"),
    )
    db_session.add(variant)
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="FailingVendor2", slug="failing-vendor-2", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    mapping = ProviderProductMapping(
        tenant_id=tenant.id,
        provider_id=prov.id,
        product_id=product.id,
        product_variant_id=variant.id,
        external_product_id="ext-fail-2",
    )
    db_session.add(mapping)

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    await LedgerService.credit(db_session, wallet, Decimal("100.00"), description="Initial Balance")
    await db_session.commit()

    # Mock provider will fail execution
    mock_client = MockProvider(provider_name="FailingVendor2")
    mock_client.fail_with_auth_error = True

    registry = ProviderClientRegistry()
    registry.register_singleton(str(prov.id), mock_client)

    notif_service = NotificationService()
    router = ProviderRouter(registry=registry)
    fulfillment_service = FulfillmentService(router=router, notification_service=notif_service)
    checkout_service = CheckoutService(
        fulfillment_service=fulfillment_service,
        notification_service=notif_service,
    )

    # Checkout 2 items at $20.00 each -> $40.00 total debited -> balance $60.00
    # Sync fulfillment fails and executes refund
    with pytest.raises(ProviderError):
        await checkout_service.checkout(
            session=db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            variant_id=variant.id,
            quantity=2,
            recipient="@buyer",
            execute_sync=True,
        )

    # Merchant updates variant price in catalog to $100.00
    variant.price = Decimal("100.00")
    await db_session.commit()

    # Inspect refund transaction
    stmt_order = select(Order).where(Order.tenant_id == tenant.id)
    order = (await db_session.execute(stmt_order)).scalars().first()
    assert order is not None
    assert order.total_amount == Decimal("40.00")

    stmt_refund = select(LedgerTransaction).where(
        LedgerTransaction.wallet_id == wallet.id,
        LedgerTransaction.transaction_type == TransactionType.REFUND,
    )
    refund_tx = (await db_session.execute(stmt_refund)).scalars().first()
    assert refund_tx is not None
    assert refund_tx.amount == Decimal("40.00")  # Frozen price, NOT 2 * 100.00!

    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("100.00")

    # Ledger audit
    reconstructed, is_valid = await LedgerService.reconstruct_and_verify_balance(db_session, wallet.id, tenant.id)
    assert is_valid is True
    assert reconstructed == Decimal("100.00")


@pytest.mark.asyncio
async def test_worker_skips_already_claimed_job(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
):
    """Scenario 5: Fulfillment worker skips processing if job record is already claimed/running."""
    tenant = Tenant(name="Skip Tenant", slug="skip-tenant")
    user = User(username="skip_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    order = Order(
        tenant_id=tenant.id,
        user_id=user.id,
        order_number="ORD-SKIP-001",
        status=OrderStatus.PAID,
        total_amount=Decimal("15.00"),
    )
    db_session.add(order)
    await db_session.flush()

    job_id = uuid.uuid4()
    # Job record is already RUNNING (claimed by worker A)
    rec = FulfillmentJobRecord(
        id=job_id,
        tenant_id=tenant.id,
        order_id=order.id,
        recipient="@skip_user",
        attempt_number=1,
        status=FulfillmentJobStatus.RUNNING,
    )
    db_session.add(rec)
    await db_session.commit()

    # Worker B gets this job in queue
    worker = FulfillmentWorker(session_factory=db_session_factory)
    job = FulfillmentJob(
        order_id=order.id,
        recipient="@skip_user",
        attempt_number=1,
        job_id=job_id,
    )
    await worker.queue.put(job)

    # Worker B attempts to process it
    result = await worker.process_one_now()
    # Must skip because claim_job returns False (status is RUNNING, not QUEUED)
    assert result is None
