import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.commerce.models import Order, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.core.exceptions import LedgerIntegrityError
from packages.core.models import Base, Tenant, User
from packages.fulfillment.models import FulfillmentAttempt, FulfillmentStatus
from packages.fulfillment.reconciliation import ReconciliationService
from packages.fulfillment.service import FulfillmentService
from packages.notifications.service import NotificationService
from packages.payments.models import LedgerTransaction, TransactionType, Wallet
from packages.payments.service import CANONICAL_REFUND_TYPE, LedgerService
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.exceptions import ProviderError
from packages.providers.models import Provider, ProviderProductMapping
from packages.providers.router import ProviderRouter


@pytest_asyncio.fixture
async def wal_session_factory(tmp_path: Path):
    """File-backed SQLite session factory in WAL mode with busy timeout for true concurrent transaction tests."""
    db_file = tmp_path / "wal_idempotency_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}?timeout=30")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.execute(text("PRAGMA busy_timeout=30000;"))

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


# ---------------------------------------------------------------------------
# Test A — Sequential idempotency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sequential_refund_idempotency(db_session: AsyncSession):
    """Test A: Calling refund twice sequentially credits wallet exactly once and returns identical transaction."""
    tenant = Tenant(name="Seq Tenant", slug="seq-tenant")
    user = User(username="seq_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    await LedgerService.credit(db_session, wallet, Decimal("100.00"), description="Deposit")
    await db_session.commit()

    ref_id = "ORD-SEQ-001"

    # Call 1
    tx1 = await LedgerService.refund(
        session=db_session,
        wallet=wallet,
        amount=Decimal("30.00"),
        reference_id=ref_id,
        reference_type=CANONICAL_REFUND_TYPE,
        description="First refund call",
    )
    await db_session.commit()
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("130.00")

    # Call 2
    tx2 = await LedgerService.refund(
        session=db_session,
        wallet=wallet,
        amount=Decimal("30.00"),
        reference_id=ref_id,
        reference_type=CANONICAL_REFUND_TYPE,
        description="Second refund call (retry)",
    )
    await db_session.commit()
    await db_session.refresh(wallet)

    # Invariants
    assert wallet.balance == Decimal("130.00")
    assert tx1.id == tx2.id

    stmt = select(LedgerTransaction).where(
        LedgerTransaction.wallet_id == wallet.id,
        LedgerTransaction.transaction_type == TransactionType.REFUND,
    )
    txs = (await db_session.execute(stmt)).scalars().all()
    assert len(txs) == 1

    reconstructed, is_valid = await LedgerService.reconstruct_and_verify_balance(db_session, wallet.id, tenant.id)
    assert is_valid is True
    assert reconstructed == Decimal("130.00")


# ---------------------------------------------------------------------------
# Test B — Concurrent idempotency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_refund_idempotency(wal_session_factory: async_sessionmaker[AsyncSession]):
    """Test B: 5 concurrent workers race to refund the same reference.

    Database unique constraint resolves the race:
    - Exactly ONE transaction is committed
    - Wallet is credited strictly once ($25.00)
    - All 5 callers resolve to the exact same refund transaction UUID
    """
    tenant_id = None
    wallet_id = None

    async with wal_session_factory() as s:
        tenant = Tenant(name="Conc Tenant", slug="conc-tenant")
        user = User(username="conc_user")
        s.add_all([tenant, user])
        await s.flush()
        tenant_id = tenant.id

        wallet = await LedgerService.get_or_create_wallet(s, tenant.id, user.id, currency="USD")
        await LedgerService.credit(s, wallet, Decimal("100.00"), description="Initial Deposit")
        await s.commit()
        wallet_id = wallet.id

    ref_id = "ORD-CONCURRENT-5WAY"

    async def worker_refund_attempt(worker_num: int):
        async with wal_session_factory() as session:
            w = await session.get(Wallet, wallet_id)
            tx = await LedgerService.refund(
                session=session,
                wallet=w,
                amount=Decimal("25.00"),
                reference_id=ref_id,
                reference_type=CANONICAL_REFUND_TYPE,
                description=f"Concurrent refund from worker {worker_num}",
            )
            await session.commit()
            return tx.id

    # Launch 5 concurrent tasks with separate DB sessions/transactions
    results = await asyncio.gather(*[worker_refund_attempt(i) for i in range(5)])

    # All 5 callers resolved to the same logical refund ID
    assert len(set(results)) == 1
    resolved_tx_id = results[0]

    async with wal_session_factory() as session:
        w = await session.get(Wallet, wallet_id)
        # Wallet balance increased strictly once: 100 + 25 = 125
        assert w.balance == Decimal("125.00")

        # Exactly 1 REFUND transaction exists
        stmt = select(LedgerTransaction).where(
            LedgerTransaction.wallet_id == wallet_id,
            LedgerTransaction.transaction_type == TransactionType.REFUND,
        )
        txs = (await session.execute(stmt)).scalars().all()
        assert len(txs) == 1
        assert txs[0].id == resolved_tx_id
        assert txs[0].amount == Decimal("25.00")

        reconstructed, is_valid = await LedgerService.reconstruct_and_verify_balance(session, wallet_id, tenant_id)
        assert is_valid is True
        assert reconstructed == Decimal("125.00")


# ---------------------------------------------------------------------------
# Test C — Concurrent mixed services (Fulfillment + Reconciliation)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_mixed_services_refund(wal_session_factory: async_sessionmaker[AsyncSession]):
    """Test C: FulfillmentService and ReconciliationService trigger refunds concurrently for the same order.

    Both paths use separate sessions and race at the database layer.
    Guarantees exactly ONE refund transaction and exactly ONE wallet credit.
    """
    tenant_id = None
    wallet_id = None
    order_id = None

    async with wal_session_factory() as s:
        tenant = Tenant(name="Mixed Tenant", slug="mixed-tenant")
        user = User(username="mixed_user")
        s.add_all([tenant, user])
        await s.flush()
        tenant_id = tenant.id

        product = Product(tenant_id=tenant.id, title="Mixed Prod")
        s.add(product)
        await s.flush()

        variant = ProductVariant(product_id=product.id, sku="MIXED-SKU", title="Mixed SKU", price=Decimal("50.00"))
        s.add(variant)
        await s.flush()

        prov = Provider(tenant_id=tenant.id, name="MixedVendor", slug="mixed-vendor", provider_type="MOCK")
        s.add(prov)
        await s.flush()

        mapping = ProviderProductMapping(tenant_id=tenant.id, provider_id=prov.id, product_id=variant.id, external_product_id="ext-mix")
        s.add(mapping)

        wallet = await LedgerService.get_or_create_wallet(s, tenant.id, user.id, currency="USD")
        await LedgerService.credit(s, wallet, Decimal("100.00"), description="Initial Deposit")
        wallet_id = wallet.id

        order = Order(
            tenant_id=tenant.id,
            user_id=user.id,
            order_number="ORD-MIXED-001",
            status=OrderStatus.PROCESSING,
            total_amount=Decimal("50.00"),
        )
        s.add(order)
        await s.flush()
        order_id = order.id

        attempt = FulfillmentAttempt(
            tenant_id=tenant.id,
            order_id=order.id,
            provider_id=prov.id,
            attempt_number=1,
            idempotency_key=f"order:{order.id}:attempt:1",
            status=FulfillmentStatus.PROCESSING,
            external_order_id="ext-order-mix",
        )
        s.add(attempt)
        await s.commit()

    # Provider configured to report failure
    mock_client = MockProvider(provider_name="MixedVendor")
    mock_client.fail_with_auth_error = True
    mock_client.orders["ext-order-mix"] = {
        "external_order_id": "ext-order-mix",
        "status": "FAILED",
        "cost": "0.00",
    }
    registry = ProviderClientRegistry()
    registry.register_singleton(str(prov.id), mock_client)

    notif_service = NotificationService()
    router = ProviderRouter(registry=registry)
    fulfillment_service = FulfillmentService(router=router, notification_service=notif_service)
    recon_service = ReconciliationService(registry=registry, notification_service=notif_service)

    # Fulfillment refund path (encounters provider failure and executes automated refund)
    async def fulfillment_path():
        async with wal_session_factory() as s:
            try:
                await fulfillment_service.execute_order_fulfillment(
                    session=s,
                    order_id=order_id,
                    recipient="@mixed_user",
                )
            except (ProviderError, Exception):  # noqa: BLE001, S110
                pass

    # Reconciliation refund path (finds attempt failed upstream and executes automated refund)
    async def reconciliation_path():
        async with wal_session_factory() as s:
            try:
                await recon_service.scan_and_reconcile_tenant(s, tenant_id)
            except Exception:  # noqa: BLE001, S110
                pass

    # Run both services simultaneously against the database
    await asyncio.gather(fulfillment_path(), reconciliation_path())

    async with wal_session_factory() as s:
        w = await s.get(Wallet, wallet_id)
        # Exactly one credit: 100 + 50 = 150
        assert w.balance == Decimal("150.00")

        stmt = select(LedgerTransaction).where(
            LedgerTransaction.wallet_id == wallet_id,
            LedgerTransaction.transaction_type == TransactionType.REFUND,
        )
        txs = (await s.execute(stmt)).scalars().all()
        assert len(txs) == 1
        assert txs[0].amount == Decimal("50.00")

        reconstructed, is_valid = await LedgerService.reconstruct_and_verify_balance(s, wallet_id, tenant_id)
        assert is_valid is True
        assert reconstructed == Decimal("150.00")


# ---------------------------------------------------------------------------
# Test D — Different references remain independent
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_different_references_remain_independent(db_session: AsyncSession):
    """Test D: Refunding ORDER-1 and ORDER-2 for the same wallet produces 2 independent refund entries."""
    tenant = Tenant(name="DiffRef Store", slug="diff-ref")
    user = User(username="diff_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    await LedgerService.credit(db_session, wallet, Decimal("100.00"), description="Deposit")
    await db_session.commit()

    # Refund ORDER-1: $10.00
    tx1 = await LedgerService.refund(
        session=db_session,
        wallet=wallet,
        amount=Decimal("10.00"),
        reference_id="ORDER-1",
        reference_type=CANONICAL_REFUND_TYPE,
    )
    await db_session.commit()

    # Refund ORDER-2: $25.00
    tx2 = await LedgerService.refund(
        session=db_session,
        wallet=wallet,
        amount=Decimal("25.00"),
        reference_id="ORDER-2",
        reference_type=CANONICAL_REFUND_TYPE,
    )
    await db_session.commit()
    await db_session.refresh(wallet)

    assert tx1.id != tx2.id
    assert wallet.balance == Decimal("135.00")  # 100 + 10 + 25

    stmt = select(LedgerTransaction).where(
        LedgerTransaction.wallet_id == wallet.id,
        LedgerTransaction.transaction_type == TransactionType.REFUND,
    )
    txs = (await db_session.execute(stmt)).scalars().all()
    assert len(txs) == 2


# ---------------------------------------------------------------------------
# Test E — Different wallets remain independent
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_different_wallets_remain_independent(db_session: AsyncSession):
    """Test E: Same canonical refund reference on two distinct wallets produces independent refunds without collision."""
    tenant = Tenant(name="MultiWallet Store", slug="multi-wallet")
    user1 = User(username="user_one")
    user2 = User(username="user_two")
    db_session.add_all([tenant, user1, user2])
    await db_session.flush()

    w1 = await LedgerService.get_or_create_wallet(db_session, tenant.id, user1.id, currency="USD")
    w2 = await LedgerService.get_or_create_wallet(db_session, tenant.id, user2.id, currency="USD")
    await db_session.commit()

    shared_ref = "ORD-SHARED-REF"

    tx1 = await LedgerService.refund(
        session=db_session,
        wallet=w1,
        amount=Decimal("15.00"),
        reference_id=shared_ref,
        reference_type=CANONICAL_REFUND_TYPE,
    )
    tx2 = await LedgerService.refund(
        session=db_session,
        wallet=w2,
        amount=Decimal("15.00"),
        reference_id=shared_ref,
        reference_type=CANONICAL_REFUND_TYPE,
    )
    await db_session.commit()
    await db_session.refresh(w1)
    await db_session.refresh(w2)

    assert tx1.id != tx2.id
    assert w1.balance == Decimal("15.00")
    assert w2.balance == Decimal("15.00")


# ---------------------------------------------------------------------------
# Test F — Same reference, different amount raises LedgerIntegrityError
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_same_reference_different_amount_raises_integrity_error(db_session: AsyncSession):
    """Test F: Duplicate refund with conflicting amount raises LedgerIntegrityError and does not credit wallet."""
    tenant = Tenant(name="Conflict Store", slug="conflict-store")
    user = User(username="conflict_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    await LedgerService.credit(db_session, wallet, Decimal("50.00"), description="Deposit")
    await db_session.commit()

    ref_id = "ORD-CONFLICT-001"

    # Initial refund: $10.00
    tx1 = await LedgerService.refund(
        session=db_session,
        wallet=wallet,
        amount=Decimal("10.00"),
        reference_id=ref_id,
        reference_type=CANONICAL_REFUND_TYPE,
    )
    await db_session.commit()
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("60.00")

    # Conflicting refund request: $20.00 for the same reference
    with pytest.raises(LedgerIntegrityError, match="Refund amount mismatch"):
        await LedgerService.refund(
            session=db_session,
            wallet=wallet,
            amount=Decimal("20.00"),
            reference_id=ref_id,
            reference_type=CANONICAL_REFUND_TYPE,
        )

    await db_session.refresh(wallet)
    # Wallet balance MUST remain $60.00 (not $70.00 or $80.00)
    assert wallet.balance == Decimal("60.00")

    # Only 1 refund row exists
    stmt = select(LedgerTransaction).where(
        LedgerTransaction.wallet_id == wallet.id,
        LedgerTransaction.transaction_type == TransactionType.REFUND,
    )
    txs = (await db_session.execute(stmt)).scalars().all()
    assert len(txs) == 1
    assert txs[0].id == tx1.id
    assert txs[0].amount == Decimal("10.00")


# ---------------------------------------------------------------------------
# Test G — Non-refund transactions are unaffected
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_refund_transactions_are_unaffected(db_session: AsyncSession):
    """Test G: The refund partial unique index does not block legitimate CREDIT, DEBIT, or ADJUSTMENT on same ref."""
    tenant = Tenant(name="NonRefund Store", slug="non-refund")
    user = User(username="nonref_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    ref_id = "REF-COMMON-123"

    # 1. CREDIT with ref_id
    tx_credit = await LedgerService.credit(
        session=db_session,
        wallet=wallet,
        amount=Decimal("100.00"),
        reference_id=ref_id,
        reference_type=CANONICAL_REFUND_TYPE,
    )

    # 2. DEBIT with same ref_id
    tx_debit = await LedgerService.debit(
        session=db_session,
        wallet=wallet,
        amount=Decimal("40.00"),
        reference_id=ref_id,
        reference_type=CANONICAL_REFUND_TYPE,
    )

    # 3. REFUND with same ref_id
    tx_refund = await LedgerService.refund(
        session=db_session,
        wallet=wallet,
        amount=Decimal("40.00"),
        reference_id=ref_id,
        reference_type=CANONICAL_REFUND_TYPE,
    )

    await db_session.commit()
    await db_session.refresh(wallet)

    assert wallet.balance == Decimal("100.00")  # 0 + 100 - 40 + 40
    assert tx_credit.id != tx_debit.id != tx_refund.id

    # Verify all 3 transactions coexist peacefully in the database
    stmt = select(LedgerTransaction).where(
        LedgerTransaction.wallet_id == wallet.id,
        LedgerTransaction.reference_id == ref_id,
    )
    txs = (await db_session.execute(stmt)).scalars().all()
    assert len(txs) == 3


# ---------------------------------------------------------------------------
# Schema Verification Tests (Section 7)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_schema_level_unique_constraint_blocks_raw_duplicate_insert(db_session: AsyncSession):
    """Schema Verification 1: Direct raw DB insert of duplicate refund fails with IntegrityError."""
    tenant = Tenant(name="RawSchema Store", slug="raw-schema")
    user = User(username="raw_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    await db_session.commit()

    ref_id = "RAW-INSERT-ORD-001"

    tx1 = LedgerTransaction(
        tenant_id=tenant.id,
        wallet_id=wallet.id,
        transaction_type=TransactionType.REFUND,
        amount=Decimal("10.00"),
        balance_before=Decimal("0.00"),
        balance_after=Decimal("10.00"),
        reference_id=ref_id,
        reference_type=CANONICAL_REFUND_TYPE,
    )
    db_session.add(tx1)
    await db_session.commit()

    # Bypassing LedgerService: attempt direct raw INSERT of identical refund identity
    tx2 = LedgerTransaction(
        tenant_id=tenant.id,
        wallet_id=wallet.id,
        transaction_type=TransactionType.REFUND,
        amount=Decimal("10.00"),
        balance_before=Decimal("10.00"),
        balance_after=Decimal("20.00"),
        reference_id=ref_id,
        reference_type=CANONICAL_REFUND_TYPE,
    )
    db_session.add(tx2)

    # Database schema MUST reject this insert directly
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed|uq_refund_idempotency"):
        await db_session.commit()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_schema_metadata_index_verification(db_session: AsyncSession):
    """Schema Verification 2: Verifies that 'uq_refund_idempotency' exists in database schema."""
    conn = await db_session.connection()

    def check_indexes(sync_conn):
        insp = inspect(sync_conn)
        indexes = insp.get_indexes("ledger_transactions")
        index_names = [idx["name"] for idx in indexes]
        assert "uq_refund_idempotency" in index_names, (
            f"'uq_refund_idempotency' index missing from ledger_transactions! Found: {index_names}"
        )
        target_idx = next(idx for idx in indexes if idx["name"] == "uq_refund_idempotency")
        assert target_idx["unique"] is True or target_idx.get("unique") == 1
        assert "wallet_id" in target_idx["column_names"]
        assert "reference_type" in target_idx["column_names"]
        assert "reference_id" in target_idx["column_names"]

    await conn.run_sync(check_indexes)
