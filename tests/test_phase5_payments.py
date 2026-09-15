import asyncio
import hashlib
import hmac
import json
import time
import urllib.parse
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStatus
from packages.core.exceptions import (
    InvalidStateTransitionError,
    TenantAccessViolationError,
)
from packages.core.models import Base
from packages.payments.exceptions import (
    MiniAppExpiredError,
    MiniAppSignatureInvalidError,
    PaymentError,
    PaymentIntegrityError,
    PaymentProviderError,
    UnsupportedProviderCapabilityError,
    WebhookVerificationError,
)
from packages.payments.models import (
    LedgerTransaction,
    PaymentIntent,
    PaymentProviderConfig,
    TransactionType,
)
from packages.payments.payment_service import PaymentService
from packages.payments.providers.mock import MockPaymentProvider
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.payments.reconciliation import PaymentReconciliationService
from packages.payments.service import (
    CANONICAL_PAYMENT_REFUND_TYPE,
    CANONICAL_REFUND_TYPE,
    CANONICAL_SETTLEMENT_TYPE,
    LedgerService,
)
from packages.payments.state_machine import (
    PaymentIntentStatus,
    PaymentStateMachine,
)
from packages.telegram.miniapp import TelegramMiniAppAuthService
from packages.telegram.models import Bot
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test Fixtures & Factory Helpers
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def wal_session_factory(tmp_path: Path):
    """File-backed SQLite session factory in WAL mode with busy timeout for true concurrent transaction tests."""
    db_file = tmp_path / "wal_payment_settle_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}?timeout=30")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.execute(text("PRAGMA busy_timeout=30000;"))

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


async def create_tenant(session: AsyncSession, name: str = "Test Tenant") -> Tenant:
    tenant = Tenant(
        name=name,
        slug=f"tenant-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def create_user(session: AsyncSession, tenant_id: uuid.UUID, telegram_id: int | None = None) -> User:
    user = User(
        telegram_id=telegram_id or int(time.time() * 1000) % 1_000_000_000,
        username=f"user_{uuid.uuid4().hex[:6]}",
        first_name="Alice",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def create_order(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    total_amount: Decimal = Decimal("100.00"),
    currency: str = "USD",
    status: OrderStatus = OrderStatus.PENDING,
) -> Order:
    order = Order(
        tenant_id=tenant_id,
        user_id=user_id,
        order_number=f"ORD-{uuid.uuid4().hex[:8].upper()}",
        status=status,
        total_amount=total_amount,
        currency=currency,
    )
    session.add(order)
    await session.flush()
    return order


async def setup_provider_config(
    session: AsyncSession,
    secret_storage: EnvSecretStorage,
    tenant_id: uuid.UUID,
    provider_name: str = "mock",
    credentials: str = "test_api_key_123",
    webhook_secret: str = "test_webhook_secret_xyz",
    is_enabled: bool = True,
) -> PaymentProviderConfig:
    creds_ref = f"PAY_CREDS_{tenant_id}_{provider_name}"
    wh_ref = f"PAY_WH_{tenant_id}_{provider_name}"
    await secret_storage.set_secret(creds_ref, credentials)
    await secret_storage.set_secret(wh_ref, webhook_secret)

    config = PaymentProviderConfig(
        tenant_id=tenant_id,
        provider_name=provider_name.lower(),
        is_enabled=is_enabled,
        credentials_ref=creds_ref,
        webhook_secret_ref=wh_ref,
        settings_json={"provider_name": provider_name},
    )
    session.add(config)
    await session.flush()
    return config


def make_telegram_init_data(bot_token: str, user_dict: dict[str, Any], auth_date: int | None = None) -> str:
    if auth_date is None:
        auth_date = int(time.time())
    user_str = json.dumps(user_dict, separators=(",", ":"))
    params = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": user_str,
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = hash_val
    return urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# 1. Payment Intent Lifecycle & State Machine Tests
# ---------------------------------------------------------------------------

async def test_create_intent_from_authoritative_order(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("49.99"), currency="USD")

    secret_storage = EnvSecretStorage()
    await setup_provider_config(db_session, secret_storage, tenant.id, "mock")
    payment_service = PaymentService(secret_storage=secret_storage)

    intent = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="intent_key_001",
    )

    # Server authoritative assertions: amount derived from Order
    assert intent.amount == Decimal("49.99")
    assert intent.currency == "USD"
    assert intent.status == PaymentIntentStatus.PENDING
    assert intent.provider == "mock"
    assert intent.provider_payment_id == "mock_pay_intent_key_001"
    assert order.status == OrderStatus.PAYMENT_PENDING


async def test_idempotent_intent_creation_returns_existing(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("100.00"))

    secret_storage = EnvSecretStorage()
    await setup_provider_config(db_session, secret_storage, tenant.id, "mock")
    payment_service = PaymentService(secret_storage=secret_storage)

    intent1 = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="intent_key_dup",
    )

    intent2 = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="intent_key_dup",
    )

    assert intent1.id == intent2.id


async def test_prevent_multiple_active_intents_per_order(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("100.00"))

    secret_storage = EnvSecretStorage()
    await setup_provider_config(db_session, secret_storage, tenant.id, "mock")
    payment_service = PaymentService(secret_storage=secret_storage)

    await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="attempt_1",
    )

    # Attempting to create a second active intent with different idempotency key must fail
    with pytest.raises(PaymentError, match="An active payment intent already exists"):
        await payment_service.create_payment_intent(
            session=db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            order_id=order.id,
            provider_name="mock",
            idempotency_key="attempt_2",
        )


async def test_payment_state_machine_legal_and_illegal_transitions() -> None:
    intent = PaymentIntent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        order_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        provider="mock",
        currency="USD",
        amount=Decimal("10.00"),
        status=PaymentIntentStatus.CREATED,
        idempotency_key="sm_test",
    )

    # Legal: CREATED -> PENDING
    assert PaymentStateMachine.transition(intent, PaymentIntentStatus.PENDING) == PaymentIntentStatus.PENDING

    # Legal: PENDING -> PROCESSING
    assert PaymentStateMachine.transition(intent, PaymentIntentStatus.PROCESSING) == PaymentIntentStatus.PROCESSING

    # Legal: PROCESSING -> SUCCEEDED
    assert PaymentStateMachine.transition(intent, PaymentIntentStatus.SUCCEEDED) == PaymentIntentStatus.SUCCEEDED

    # Illegal: SUCCEEDED -> PENDING (terminal state violation)
    with pytest.raises(InvalidStateTransitionError):
        PaymentStateMachine.transition(intent, PaymentIntentStatus.PENDING)

    # Illegal: SUCCEEDED -> FAILED
    with pytest.raises(InvalidStateTransitionError):
        PaymentStateMachine.transition(intent, PaymentIntentStatus.FAILED)


# ---------------------------------------------------------------------------
# 2. Webhooks & Signature Verification Tests
# ---------------------------------------------------------------------------

async def test_webhook_valid_signature_and_settlement(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("75.00"))

    secret_storage = EnvSecretStorage()
    webhook_secret = "secret_key_12345"
    await setup_provider_config(
        db_session, secret_storage, tenant.id, "mock", webhook_secret=webhook_secret
    )
    payment_service = PaymentService(secret_storage=secret_storage)

    intent = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="wh_intent_1",
    )

    payload = {
        "event_id": "evt_001",
        "event_type": "payment.succeeded",
        "provider_payment_id": intent.provider_payment_id,
        "amount": "75.00",
        "currency": "USD",
        "status": "SUCCEEDED",
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    headers = {"X-Signature": sig}

    event = await payment_service.process_webhook(
        session=db_session,
        tenant_id=tenant.id,
        provider_name="mock",
        payload_bytes=payload_bytes,
        headers=headers,
    )

    assert event.processed is True
    assert event.signature_verified is True
    assert intent.status == PaymentIntentStatus.SUCCEEDED
    assert order.status == OrderStatus.PAID

    # Verify single ledger credit
    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, "USD")
    assert wallet.balance == Decimal("75.00")


async def test_webhook_invalid_signature_rejected(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    secret_storage = EnvSecretStorage()
    await setup_provider_config(db_session, secret_storage, tenant.id, "mock", webhook_secret="real_secret")
    payment_service = PaymentService(secret_storage=secret_storage)

    payload_bytes = b'{"event_id":"evt_bad","event_type":"payment.succeeded"}'
    headers = {"X-Signature": "invalid_bogus_signature"}

    with pytest.raises(WebhookVerificationError):
        await payment_service.process_webhook(
            session=db_session,
            tenant_id=tenant.id,
            provider_name="mock",
            payload_bytes=payload_bytes,
            headers=headers,
        )


async def test_duplicate_webhook_delivery_is_idempotent(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("50.00"))

    secret_storage = EnvSecretStorage()
    webhook_secret = "secret_dedup"
    await setup_provider_config(
        db_session, secret_storage, tenant.id, "mock", webhook_secret=webhook_secret
    )
    payment_service = PaymentService(secret_storage=secret_storage)

    intent = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="wh_dedup_intent",
    )

    payload = {
        "event_id": "evt_dedup_001",
        "event_type": "payment.succeeded",
        "provider_payment_id": intent.provider_payment_id,
        "amount": "50.00",
        "currency": "USD",
        "status": "SUCCEEDED",
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    headers = {"X-Signature": sig}

    event1 = await payment_service.process_webhook(
        session=db_session,
        tenant_id=tenant.id,
        provider_name="mock",
        payload_bytes=payload_bytes,
        headers=headers,
    )
    event2 = await payment_service.process_webhook(
        session=db_session,
        tenant_id=tenant.id,
        provider_name="mock",
        payload_bytes=payload_bytes,
        headers=headers,
    )

    assert event1.id == event2.id

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, "USD")
    assert wallet.balance == Decimal("50.00")  # Exactly one credit


async def test_webhook_wrong_amount_or_currency_fails_integrity(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("100.00"), currency="USD")

    secret_storage = EnvSecretStorage()
    webhook_secret = "secret_integrity"
    await setup_provider_config(
        db_session, secret_storage, tenant.id, "mock", webhook_secret=webhook_secret
    )
    payment_service = PaymentService(secret_storage=secret_storage)

    intent = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="wh_integrity_intent",
    )

    # 1. Wrong amount payload
    payload_wrong_amount = {
        "event_id": "evt_wrong_amt",
        "event_type": "payment.succeeded",
        "provider_payment_id": intent.provider_payment_id,
        "amount": "50.00",  # Mismatch!
        "currency": "USD",
        "status": "SUCCEEDED",
    }
    raw_bytes = json.dumps(payload_wrong_amount).encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    with pytest.raises(PaymentIntegrityError, match="does not match intent amount"):
        await payment_service.process_webhook(
            session=db_session,
            tenant_id=tenant.id,
            provider_name="mock",
            payload_bytes=raw_bytes,
            headers={"X-Signature": sig},
        )

    # 2. Wrong currency payload
    payload_wrong_curr = {
        "event_id": "evt_wrong_curr",
        "event_type": "payment.succeeded",
        "provider_payment_id": intent.provider_payment_id,
        "amount": "100.00",
        "currency": "EUR",  # Mismatch!
        "status": "SUCCEEDED",
    }
    raw_bytes = json.dumps(payload_wrong_curr).encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    with pytest.raises(PaymentIntegrityError, match="does not match intent currency"):
        await payment_service.process_webhook(
            session=db_session,
            tenant_id=tenant.id,
            provider_name="mock",
            payload_bytes=raw_bytes,
            headers={"X-Signature": sig},
        )


# ---------------------------------------------------------------------------
# 3. Webhook-First Race & User Return Tests
# ---------------------------------------------------------------------------

async def test_webhook_arrives_before_user_return_race(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("30.00"))

    secret_storage = EnvSecretStorage()
    webhook_secret = "secret_race"
    await setup_provider_config(
        db_session, secret_storage, tenant.id, "mock", webhook_secret=webhook_secret
    )
    payment_service = PaymentService(secret_storage=secret_storage)

    intent = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="race_wh_first",
    )

    # Step 1: Webhook arrives first
    payload = {
        "event_id": "evt_race_1",
        "event_type": "payment.succeeded",
        "provider_payment_id": intent.provider_payment_id,
        "amount": "30.00",
        "currency": "USD",
        "status": "SUCCEEDED",
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()
    await payment_service.process_webhook(
        session=db_session,
        tenant_id=tenant.id,
        provider_name="mock",
        payload_bytes=raw_bytes,
        headers={"X-Signature": sig},
    )

    # Step 2: User returns and invokes settle/return
    intent_res, _ = await payment_service.settle_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        intent_id=intent.id,
    )

    assert intent_res.status == PaymentIntentStatus.SUCCEEDED
    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, "USD")
    assert wallet.balance == Decimal("30.00")  # Exactly one credit


async def test_user_returns_before_webhook_race(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("40.00"))

    secret_storage = EnvSecretStorage()
    webhook_secret = "secret_race_user_first"
    await setup_provider_config(
        db_session, secret_storage, tenant.id, "mock", webhook_secret=webhook_secret
    )
    payment_service = PaymentService(secret_storage=secret_storage)

    intent = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="race_user_first",
    )

    # Step 1: User returns first and settles
    await payment_service.settle_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        intent_id=intent.id,
    )

    # Step 2: Webhook arrives subsequently
    payload = {
        "event_id": "evt_race_user_2",
        "event_type": "payment.succeeded",
        "provider_payment_id": intent.provider_payment_id,
        "amount": "40.00",
        "currency": "USD",
        "status": "SUCCEEDED",
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()
    await payment_service.process_webhook(
        session=db_session,
        tenant_id=tenant.id,
        provider_name="mock",
        payload_bytes=raw_bytes,
        headers={"X-Signature": sig},
    )

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, "USD")
    assert wallet.balance == Decimal("40.00")  # Exactly one credit


# ---------------------------------------------------------------------------
# 4. Concurrency & Database-Enforced Settlement Idempotency Tests
# ---------------------------------------------------------------------------

async def test_concurrent_settlement_idempotency(
    wal_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """True multi-session concurrent settlement test verifying DB partial unique index uq_settlement_idempotency."""
    async with wal_session_factory() as setup_session:
        tenant = await create_tenant(setup_session)
        user = await create_user(setup_session, tenant.id)
        order = await create_order(setup_session, tenant.id, user.id, total_amount=Decimal("150.00"))
        wallet = await LedgerService.get_or_create_wallet(setup_session, tenant.id, user.id, "USD")

        secret_storage = EnvSecretStorage()
        await setup_provider_config(setup_session, secret_storage, tenant.id, "mock")
        payment_service = PaymentService(secret_storage=secret_storage)

        intent = await payment_service.create_payment_intent(
            session=setup_session,
            tenant_id=tenant.id,
            user_id=user.id,
            order_id=order.id,
            provider_name="mock",
            idempotency_key="concurrent_settle_key",
        )
        await setup_session.commit()
        intent_id = intent.id
        tenant_id = tenant.id
        user_id = user.id

    async def execute_settle(worker_id: int) -> tuple[int, Any]:
        async with wal_session_factory() as session:
            svc = PaymentService(secret_storage=secret_storage)
            try:
                _, tx = await svc.settle_payment_intent(session, tenant_id, intent_id)
                await session.commit()
                return worker_id, tx.id
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                return worker_id, str(exc)

    # Launch 5 concurrent settlement attempts
    results = await asyncio.gather(*[execute_settle(i) for i in range(5)])
    assert len(results) == 5

    # Verify in DB that exactly ONE settlement transaction exists
    async with wal_session_factory() as verify_session:
        stmt = select(func.count()).select_from(LedgerTransaction).where(
            LedgerTransaction.reference_type == CANONICAL_SETTLEMENT_TYPE,
            LedgerTransaction.reference_id == str(intent_id),
        )
        count = (await verify_session.execute(stmt)).scalar_one()
        assert count == 1, f"Expected exactly 1 settlement ledger transaction, found {count}."

        wallet = await LedgerService.get_or_create_wallet(verify_session, tenant_id, user_id, "USD")
        assert wallet.balance == Decimal("150.00"), f"Expected wallet balance 150.00, got {wallet.balance}"

        # Audit ledger integrity from inception
        reconstructed, is_valid = await LedgerService.reconstruct_and_verify_balance(
            verify_session, wallet.id, tenant_id
        )
        assert is_valid is True
        assert reconstructed == Decimal("150.00")


# ---------------------------------------------------------------------------
# 5. Payment Reconciliation Tests
# ---------------------------------------------------------------------------

async def test_reconciliation_resolves_pending_intent_to_succeeded(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("80.00"))

    mock_provider = MockPaymentProvider(default_create_status=PaymentIntentStatus.PENDING)
    registry = PaymentProviderRegistry()
    registry.register_instance(tenant.id, "mock", mock_provider)

    secret_storage = EnvSecretStorage()
    await setup_provider_config(db_session, secret_storage, tenant.id, "mock")
    payment_service = PaymentService(registry=registry, secret_storage=secret_storage)
    reconcile_service = PaymentReconciliationService(payment_service=payment_service)

    intent = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="reconcile_succ_key",
    )
    assert intent.status == PaymentIntentStatus.PENDING

    # Simulate provider gateway asynchronously succeeding
    mock_provider.payments[intent.provider_payment_id]["status"] = PaymentIntentStatus.SUCCEEDED

    reconciled = await reconcile_service.reconcile_intent(db_session, tenant.id, intent.id)
    assert reconciled.status == PaymentIntentStatus.SUCCEEDED
    assert order.status == OrderStatus.PAID

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, "USD")
    assert wallet.balance == Decimal("80.00")


async def test_reconciliation_handles_timeout_as_unknown(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("50.00"))

    mock_provider = MockPaymentProvider(
        default_create_status=PaymentIntentStatus.PENDING,
        simulate_lookup_timeout=True,
    )
    registry = PaymentProviderRegistry()
    registry.register_instance(tenant.id, "mock", mock_provider)

    secret_storage = EnvSecretStorage()
    await setup_provider_config(db_session, secret_storage, tenant.id, "mock")
    payment_service = PaymentService(registry=registry, secret_storage=secret_storage)
    reconcile_service = PaymentReconciliationService(payment_service=payment_service)

    intent = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="reconcile_timeout_key",
    )

    reconciled = await reconcile_service.reconcile_intent(db_session, tenant.id, intent.id)
    assert reconciled.status == PaymentIntentStatus.UNKNOWN

    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, "USD")
    assert wallet.balance == Decimal("0.00")  # No premature credit on UNKNOWN state!


# ---------------------------------------------------------------------------
# 6. Refund Integration (Distinct Accounting Reference) Tests
# ---------------------------------------------------------------------------

async def test_payment_refund_flow_distinct_from_fulfillment_refund(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("120.00"))

    secret_storage = EnvSecretStorage()
    await setup_provider_config(db_session, secret_storage, tenant.id, "mock")
    payment_service = PaymentService(secret_storage=secret_storage)

    intent = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="refund_test_key",
    )

    # Settle payment first
    await payment_service.settle_payment_intent(db_session, tenant.id, intent.id)
    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, "USD")
    assert wallet.balance == Decimal("120.00")

    # Execute payment refund
    pay_tx = await payment_service.refund_payment(
        session=db_session,
        tenant_id=tenant.id,
        intent_id=intent.id,
        amount=Decimal("120.00"),
        reason="Customer requested gateway refund",
    )

    assert pay_tx.status == "SUCCESS"

    # Verify that the ledger recorded the refund under CANONICAL_PAYMENT_REFUND_TYPE
    stmt = select(LedgerTransaction).where(
        LedgerTransaction.wallet_id == wallet.id,
        LedgerTransaction.transaction_type == TransactionType.REFUND,
    )
    refund_tx = (await db_session.execute(stmt)).scalars().first()
    assert refund_tx is not None
    assert refund_tx.reference_type == CANONICAL_PAYMENT_REFUND_TYPE
    assert refund_tx.reference_type != CANONICAL_REFUND_TYPE  # Proves strict distinction!


async def test_unsupported_provider_capabilities_fail_cleanly(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    user = await create_user(db_session, tenant.id)
    order = await create_order(db_session, tenant.id, user.id, total_amount=Decimal("60.00"))

    mock_provider = MockPaymentProvider(supports_refunds=False)
    registry = PaymentProviderRegistry()
    registry.register_instance(tenant.id, "mock", mock_provider)

    secret_storage = EnvSecretStorage()
    await setup_provider_config(db_session, secret_storage, tenant.id, "mock")
    payment_service = PaymentService(registry=registry, secret_storage=secret_storage)

    intent = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        order_id=order.id,
        provider_name="mock",
        idempotency_key="unsupported_cap_key",
    )
    await payment_service.settle_payment_intent(db_session, tenant.id, intent.id)

    with pytest.raises(UnsupportedProviderCapabilityError, match="does not support refunds"):
        await payment_service.refund_payment(
            session=db_session,
            tenant_id=tenant.id,
            intent_id=intent.id,
        )


# ---------------------------------------------------------------------------
# 7. Telegram Mini App initData Authentication Tests
# ---------------------------------------------------------------------------

async def test_miniapp_auth_valid_init_data(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    bot_token = "123456789:AAHk69MockTelegramBotTokenForTesting"
    secret_storage = EnvSecretStorage()
    token_ref = f"BOT_TOKEN_{tenant.id}"
    await secret_storage.set_secret(token_ref, bot_token)

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=123456789,
        display_name="Store Bot",
        token_secret_ref=token_ref,
        is_enabled=True,
    )
    db_session.add(bot)
    await db_session.flush()

    user_payload = {
        "id": 987654321,
        "first_name": "Charlie",
        "last_name": "Root",
        "username": "charlie_test",
        "language_code": "en",
    }
    raw_init_data = make_telegram_init_data(bot_token, user_payload)

    resolved_tenant, user, payload = await TelegramMiniAppAuthService.authenticate_and_resolve_tenant(
        session=db_session,
        raw_init_data=raw_init_data,
        bot_id=bot.id,
        secret_storage=secret_storage,
    )

    assert resolved_tenant.id == tenant.id
    assert user.telegram_id == 987654321
    assert payload["username"] == "charlie_test"


async def test_miniapp_auth_invalid_signature_rejected(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    secret_storage = EnvSecretStorage()
    token_ref = f"BOT_TOKEN_BAD_{tenant.id}"
    await secret_storage.set_secret(token_ref, "123456:RealToken")

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=123456,
        display_name="Bad Sig Bot",
        token_secret_ref=token_ref,
        is_enabled=True,
    )
    db_session.add(bot)
    await db_session.flush()

    # Tampered init data with forged hash
    tampered_init_data = "query_id=AAHdF6IQAAAAAN0XohDhrOrc&user=%7B%22id%22%3A123%7D&auth_date=1620000000&hash=0000000000000000000000000000000000000000000000000000000000000000"

    with pytest.raises(MiniAppSignatureInvalidError):
        await TelegramMiniAppAuthService.authenticate_and_resolve_tenant(
            session=db_session,
            raw_init_data=tampered_init_data,
            bot_id=bot.id,
            secret_storage=secret_storage,
        )


async def test_miniapp_auth_expired_timestamp_rejected(db_session: AsyncSession) -> None:
    tenant = await create_tenant(db_session)
    bot_token = "123456:ExpiredTestToken"
    secret_storage = EnvSecretStorage()
    token_ref = f"BOT_TOKEN_EXP_{tenant.id}"
    await secret_storage.set_secret(token_ref, bot_token)

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=123456,
        display_name="Exp Bot",
        token_secret_ref=token_ref,
        is_enabled=True,
    )
    db_session.add(bot)
    await db_session.flush()

    user_payload = {"id": 111222, "first_name": "Old"}
    expired_timestamp = int(time.time()) - 100_000  # Older than 86400s
    expired_init_data = make_telegram_init_data(bot_token, user_payload, auth_date=expired_timestamp)

    with pytest.raises(MiniAppExpiredError):
        await TelegramMiniAppAuthService.authenticate_and_resolve_tenant(
            session=db_session,
            raw_init_data=expired_init_data,
            bot_id=bot.id,
            secret_storage=secret_storage,
            max_age_seconds=86400,
        )


async def test_miniapp_auth_tenant_mismatch_rejected(db_session: AsyncSession) -> None:
    tenant_a = await create_tenant(db_session, "Tenant A")
    tenant_b = await create_tenant(db_session, "Tenant B")

    bot_token = "123456:TenantMismatchToken"
    secret_storage = EnvSecretStorage()
    token_ref = f"BOT_TOKEN_MISMATCH_{tenant_a.id}"
    await secret_storage.set_secret(token_ref, bot_token)

    bot = Bot(
        tenant_id=tenant_a.id,
        telegram_bot_id=55555,
        display_name="Bot A",
        token_secret_ref=token_ref,
        is_enabled=True,
    )
    db_session.add(bot)
    await db_session.flush()

    user_payload = {"id": 333444, "first_name": "Spoofer"}
    init_data = make_telegram_init_data(bot_token, user_payload)

    # Request claims Tenant B context but bot belongs to Tenant A
    with pytest.raises(TenantAccessViolationError):
        await TelegramMiniAppAuthService.authenticate_and_resolve_tenant(
            session=db_session,
            raw_init_data=init_data,
            bot_id=bot.id,
            secret_storage=secret_storage,
            expected_tenant_id=tenant_b.id,
        )


# ---------------------------------------------------------------------------
# 8. Negative Multi-Tenant Isolation Tests
# ---------------------------------------------------------------------------

async def test_cross_tenant_payment_intent_access_rejected(db_session: AsyncSession) -> None:
    tenant_a = await create_tenant(db_session, "Tenant A")
    tenant_b = await create_tenant(db_session, "Tenant B")

    user_a = await create_user(db_session, tenant_a.id)
    order_a = await create_order(db_session, tenant_a.id, user_a.id)

    secret_storage = EnvSecretStorage()
    await setup_provider_config(db_session, secret_storage, tenant_a.id, "mock")
    payment_service = PaymentService(secret_storage=secret_storage)

    intent_a = await payment_service.create_payment_intent(
        session=db_session,
        tenant_id=tenant_a.id,
        user_id=user_a.id,
        order_id=order_a.id,
        provider_name="mock",
        idempotency_key="tenant_iso_key_1",
    )

    # Tenant B tries to access Tenant A's PaymentIntent
    with pytest.raises(TenantAccessViolationError):
        await payment_service.get_payment_intent(db_session, tenant_b.id, intent_a.id)

    # Tenant B tries to cancel Tenant A's PaymentIntent
    with pytest.raises(TenantAccessViolationError):
        await payment_service.cancel_payment_intent(db_session, tenant_b.id, intent_a.id)


async def test_cross_tenant_provider_config_access_rejected(db_session: AsyncSession) -> None:
    tenant_a = await create_tenant(db_session, "Tenant A")
    tenant_b = await create_tenant(db_session, "Tenant B")

    secret_storage = EnvSecretStorage()
    # Configure mock provider only for Tenant A
    await setup_provider_config(db_session, secret_storage, tenant_a.id, "mock")

    registry = PaymentProviderRegistry()

    # Tenant B querying provider configuration must fail
    with pytest.raises(PaymentProviderError, match="not configured for tenant"):
        await registry.get_provider(db_session, tenant_b.id, "mock", secret_storage)
