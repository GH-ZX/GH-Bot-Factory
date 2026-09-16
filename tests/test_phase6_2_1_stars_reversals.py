import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.exceptions import InsufficientFundsError
from packages.payments.models import (
    LedgerTransaction,
    PaymentProviderConfig,
    PaymentTransaction,
    PaymentTransactionType,
    Wallet,
    WalletTopUpReversalStatus,
)
from packages.payments.payment_service import PaymentService
from packages.payments.providers.interface import PaymentCreateRequest, PaymentRefundRequest
from packages.payments.providers.mock import MockPaymentProvider
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.payments.providers.telegram_stars import TelegramStarsProvider
from packages.payments.service import CANONICAL_TOPUP_REVERSAL_TYPE, LedgerService
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


async def _tenant_user(session: AsyncSession) -> tuple[Tenant, User]:
    tenant = Tenant(name="Stars Store", slug=f"stars-{uuid.uuid4().hex[:8]}", is_active=True)
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000) + 1,
        username=f"stars_{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add_all([tenant, user])
    await session.flush()
    return tenant, user


async def _configure(
    session: AsyncSession,
    secret_storage: EnvSecretStorage,
    tenant: Tenant,
    provider_name: str,
    settings: dict[str, Any],
) -> None:
    secret_ref = f"PAYMENT_SECRET_{tenant.id}_{provider_name}"
    await secret_storage.set_secret(secret_ref, "123456:TEST_TOKEN_FOR_PROVIDER")
    session.add(
        PaymentProviderConfig(
            tenant_id=tenant.id,
            provider_name=provider_name,
            is_enabled=True,
            credentials_ref=secret_ref,
            settings_json=settings,
        )
    )
    await session.flush()


async def test_telegram_stars_provider_creates_native_invoice_and_safe_repeat_refund() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(method: str, payload: dict[str, Any]) -> Any:
        calls.append((method, payload))
        if method == "createInvoiceLink":
            return "https://t.me/$test-invoice-link"
        if method == "refundStarPayment":
            from packages.payments.exceptions import PaymentProviderError

            exc = PaymentProviderError("Bad Request: CHARGE_ALREADY_REFUNDED")
            exc.provider_error_description = "Bad Request: CHARGE_ALREADY_REFUNDED"
            raise exc
        raise AssertionError(method)

    provider = TelegramStarsProvider({}, "123456:TEST", api_call=fake_call)
    created = await provider.create_payment(
        PaymentCreateRequest(
            order_id=None,
            amount=Decimal(25),
            currency="XTR",
            idempotency_key="stars-create-001",
            metadata={"payment_intent_id": str(uuid.uuid4())},
        )
    )
    assert created.status == PaymentIntentStatus.PENDING
    assert created.checkout_url == "https://t.me/$test-invoice-link"
    assert calls[0][0] == "createInvoiceLink"
    assert calls[0][1]["currency"] == "XTR"
    assert calls[0][1]["prices"][0]["amount"] == 25

    refunded = await provider.refund(
        PaymentRefundRequest(
            provider_payment_id="charge-123",
            amount=Decimal(25),
            currency="XTR",
            idempotency_key="stars-refund-001",
            metadata={"telegram_user_id": 123456789},
        )
    )
    assert refunded.is_success is True
    assert refunded.provider_refund_id == "charge-123"


async def test_stars_successful_payment_settles_wallet_exactly_once(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session)
    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()

    async def fake_call(method: str, payload: dict[str, Any]) -> Any:
        if method == "createInvoiceLink":
            return "https://t.me/$invoice-abc"
        raise AssertionError(method)

    provider = TelegramStarsProvider({}, "123456:TEST", api_call=fake_call)
    await _configure(
        db_session,
        secret_storage,
        tenant,
        "telegram_stars",
        {
            "topup_enabled": True,
            "topup_min_amount": "1",
            "topup_max_amount": "1000",
            "topup_currencies": ["XTR"],
            "topup_whole_units_only": True,
            "terms_required": True,
            "terms_url": "https://example.com/terms",
        },
    )
    registry.register_instance(tenant.id, "telegram_stars", provider)
    service = PaymentService(registry=registry, secret_storage=secret_storage)

    intent = await service.create_wallet_topup_intent(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        amount=Decimal(40),
        currency="XTR",
        provider_name="telegram_stars",
        idempotency_key="stars-topup-001",
        metadata={"terms_accepted": True},
    )
    payload = f"ghbf:wallet-topup:{intent.id}"

    first = await service.settle_telegram_stars_topup(
        db_session,
        tenant.id,
        user.id,
        payload,
        "charge-stars-001",
        Decimal(40),
        "XTR",
    )
    second = await service.settle_telegram_stars_topup(
        db_session,
        tenant.id,
        user.id,
        payload,
        "charge-stars-001",
        Decimal(40),
        "XTR",
    )
    assert first.id == second.id
    assert first.provider_payment_id == "charge-stars-001"

    wallet = (
        await db_session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "XTR",
            )
        )
    ).scalar_one()
    assert wallet.balance == Decimal(40)


async def test_topup_reversal_reserves_balance_and_refunds_exactly_once(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session)
    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()
    provider = MockPaymentProvider(default_create_status=PaymentIntentStatus.SUCCEEDED)
    await _configure(
        db_session,
        secret_storage,
        tenant,
        "mock",
        {
            "provider_name": "mock",
            "topup_enabled": True,
            "topup_min_amount": "1",
            "topup_max_amount": "1000",
            "topup_currencies": ["USD"],
        },
    )
    registry.register_instance(tenant.id, "mock", provider)
    service = PaymentService(registry=registry, secret_storage=secret_storage)

    intent = await service.create_wallet_topup_intent(
        db_session,
        tenant.id,
        user.id,
        Decimal("30.00"),
        "USD",
        "mock",
        "reversal-topup-001",
    )
    reversal = await service.request_wallet_topup_reversal(
        db_session,
        tenant.id,
        intent.id,
        "reversal-request-001",
        reason="Customer request",
    )
    same = await service.request_wallet_topup_reversal(
        db_session,
        tenant.id,
        intent.id,
        "reversal-request-001",
    )
    assert same.id == reversal.id
    assert reversal.status == WalletTopUpReversalStatus.FUNDS_RESERVED

    wallet = await db_session.get(Wallet, reversal.wallet_id)
    assert wallet is not None
    assert wallet.balance == Decimal("0.00")
    reversal_debits = list(
        (
            await db_session.execute(
                select(LedgerTransaction).where(
                    LedgerTransaction.wallet_id == wallet.id,
                    LedgerTransaction.reference_type == CANONICAL_TOPUP_REVERSAL_TYPE,
                    LedgerTransaction.reference_id == str(reversal.id),
                )
            )
        ).scalars().all()
    )
    assert len(reversal_debits) == 1

    reversal.status = WalletTopUpReversalStatus.PROCESSING
    await db_session.flush()
    completed = await service.process_wallet_topup_reversal(
        db_session,
        tenant.id,
        reversal.id,
    )
    again = await service.process_wallet_topup_reversal(
        db_session,
        tenant.id,
        reversal.id,
    )
    assert completed.status == WalletTopUpReversalStatus.COMPLETED
    assert again.id == completed.id
    assert len(provider.refunds) == 1
    refund_txs = list(
        (
            await db_session.execute(
                select(PaymentTransaction).where(
                    PaymentTransaction.payment_intent_id == intent.id,
                    PaymentTransaction.transaction_type == PaymentTransactionType.REFUND,
                )
            )
        ).scalars().all()
    )
    assert len(refund_txs) == 1


async def test_topup_reversal_refuses_refund_after_balance_was_spent(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session)
    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()
    provider = MockPaymentProvider(default_create_status=PaymentIntentStatus.SUCCEEDED)
    await _configure(
        db_session,
        secret_storage,
        tenant,
        "mock",
        {
            "provider_name": "mock",
            "topup_enabled": True,
            "topup_min_amount": "1",
            "topup_max_amount": "1000",
            "topup_currencies": ["USD"],
        },
    )
    registry.register_instance(tenant.id, "mock", provider)
    service = PaymentService(registry=registry, secret_storage=secret_storage)
    intent = await service.create_wallet_topup_intent(
        db_session,
        tenant.id,
        user.id,
        Decimal("50.00"),
        "USD",
        "mock",
        "spent-topup-001",
    )
    wallet = (
        await db_session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "USD",
            )
        )
    ).scalar_one()
    await LedgerService.debit(
        db_session,
        wallet,
        Decimal("10.00"),
        reference_id="purchase-after-topup",
        reference_type="ORDER",
    )

    with pytest.raises(InsufficientFundsError):
        await service.request_wallet_topup_reversal(
            db_session,
            tenant.id,
            intent.id,
            "spent-reversal-001",
        )

async def test_stars_topup_rejects_fractional_amount(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session)
    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()
    provider = TelegramStarsProvider({}, "123456:TEST", api_call=lambda method, payload: None)  # type: ignore[arg-type]
    await _configure(
        db_session,
        secret_storage,
        tenant,
        "telegram_stars",
        {
            "topup_enabled": True,
            "topup_min_amount": "1",
            "topup_max_amount": "1000",
            "topup_currencies": ["XTR"],
            "topup_whole_units_only": True,
        },
    )
    registry.register_instance(tenant.id, "telegram_stars", provider)
    service = PaymentService(registry=registry, secret_storage=secret_storage)

    from packages.payments.exceptions import PaymentError

    with pytest.raises(PaymentError, match="whole-unit"):
        await service.create_wallet_topup_intent(
            db_session,
            tenant.id,
            user.id,
            Decimal("1.50"),
            "XTR",
            "telegram_stars",
            "fractional-stars-001",
        )


async def test_reversal_worker_recovers_interrupted_processing(db_session_factory) -> None:
    from packages.payments.models import WalletTopUpReversal
    from packages.payments.reversal_worker import WalletTopUpReversalWorker

    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()
    provider = MockPaymentProvider(default_create_status=PaymentIntentStatus.SUCCEEDED)
    service = PaymentService(registry=registry, secret_storage=secret_storage)

    async with db_session_factory() as session:
        tenant, user = await _tenant_user(session)
        await _configure(
            session,
            secret_storage,
            tenant,
            "mock",
            {
                "provider_name": "mock",
                "topup_enabled": True,
                "topup_min_amount": "1",
                "topup_max_amount": "1000",
                "topup_currencies": ["USD"],
            },
        )
        registry.register_instance(tenant.id, "mock", provider)
        intent = await service.create_wallet_topup_intent(
            session,
            tenant.id,
            user.id,
            Decimal("22.00"),
            "USD",
            "mock",
            "worker-topup-001",
        )
        reversal = await service.request_wallet_topup_reversal(
            session,
            tenant.id,
            intent.id,
            "worker-reversal-001",
        )
        reversal.status = WalletTopUpReversalStatus.PROCESSING
        reversal_id = reversal.id
        await session.commit()

    worker = WalletTopUpReversalWorker(
        payment_service=service,
        session_factory=db_session_factory,
        poll_interval_seconds=0.01,
    )
    assert await worker.recover_processing() == 1
    assert await worker.poll_once() == 1

    async with db_session_factory() as session:
        completed = await session.get(WalletTopUpReversal, reversal_id)
        assert completed is not None
        assert completed.status == WalletTopUpReversalStatus.COMPLETED
        assert completed.attempts == 1

async def _settled_stars_topup(
    session: AsyncSession,
    tenant: Tenant,
    user: User,
    registry: PaymentProviderRegistry,
    secret_storage: EnvSecretStorage,
    provider: TelegramStarsProvider,
    amount: Decimal,
    key: str,
    charge_id: str,
):
    await _configure(
        session,
        secret_storage,
        tenant,
        "telegram_stars",
        {
            "topup_enabled": True,
            "topup_min_amount": "1",
            "topup_max_amount": "1000",
            "topup_currencies": ["XTR"],
            "topup_whole_units_only": True,
            "transaction_scan_pages": 1,
        },
    )
    registry.register_instance(tenant.id, "telegram_stars", provider)
    service = PaymentService(registry=registry, secret_storage=secret_storage)
    intent = await service.create_wallet_topup_intent(
        session,
        tenant.id,
        user.id,
        amount,
        "XTR",
        "telegram_stars",
        key,
    )
    await service.settle_telegram_stars_topup(
        session,
        tenant.id,
        user.id,
        f"ghbf:wallet-topup:{intent.id}",
        charge_id,
        amount,
        "XTR",
    )
    return service, intent


async def test_external_stars_reversal_debits_wallet_once_and_is_audited(db_session: AsyncSession) -> None:
    from packages.payments.models import PaymentReconciliationEvent, WalletTopUpReversal
    from packages.payments.stars_reconciliation import TelegramStarsReconciliationService

    tenant, user = await _tenant_user(db_session)
    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()
    transactions: list[dict[str, Any]] = []

    async def fake_call(method: str, payload: dict[str, Any]) -> Any:
        if method == "createInvoiceLink":
            return "https://t.me/$external-reversal"
        if method == "getStarTransactions":
            return {"transactions": list(transactions)}
        raise AssertionError(method)

    provider = TelegramStarsProvider(
        {"transaction_scan_pages": 1},
        "123456:TEST",
        api_call=fake_call,
    )
    service, intent = await _settled_stars_topup(
        db_session,
        tenant,
        user,
        registry,
        secret_storage,
        provider,
        Decimal(40),
        "external-topup-001",
        "charge-external-001",
    )
    transactions.append(
        {
            "id": "charge-external-001",
            "amount": 40,
            "date": 1_700_000_000,
            "receiver": {
                "type": "user",
                "transaction_type": "invoice_payment",
                "user": {"id": user.telegram_id},
                "invoice_payload": f"ghbf:wallet-topup:{intent.id}",
            },
        }
    )

    reconciliation = TelegramStarsReconciliationService(payment_service=service)
    assert await reconciliation.scan_tenant(db_session, tenant.id) == 1
    assert await reconciliation.scan_tenant(db_session, tenant.id) == 1

    wallet = (
        await db_session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "XTR",
            )
        )
    ).scalar_one()
    assert wallet.balance == Decimal(0)
    assert wallet.is_active is True

    reversals = list((await db_session.execute(select(WalletTopUpReversal))).scalars().all())
    assert len(reversals) == 1
    assert reversals[0].status == WalletTopUpReversalStatus.COMPLETED
    assert reversals[0].metadata_json["origin"] == "EXTERNAL_PROVIDER"

    events = list((await db_session.execute(select(PaymentReconciliationEvent))).scalars().all())
    assert len(events) == 1
    assert events[0].classification == "EXTERNAL_REVERSAL_APPLIED"
    assert events[0].requires_review is False

    debit_rows = list(
        (
            await db_session.execute(
                select(LedgerTransaction).where(
                    LedgerTransaction.wallet_id == wallet.id,
                    LedgerTransaction.reference_type == CANONICAL_TOPUP_REVERSAL_TYPE,
                )
            )
        ).scalars().all()
    )
    assert len(debit_rows) == 1


async def test_external_stars_reversal_freezes_spent_wallet_until_manual_resolution(
    db_session: AsyncSession,
) -> None:
    from packages.payments.models import PaymentReconciliationEvent, WalletTopUpReversal
    from packages.payments.stars_reconciliation import TelegramStarsReconciliationService

    tenant, user = await _tenant_user(db_session)
    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()
    transactions: list[dict[str, Any]] = []

    async def fake_call(method: str, payload: dict[str, Any]) -> Any:
        if method == "createInvoiceLink":
            return "https://t.me/$spent-chargeback"
        if method == "getStarTransactions":
            return {"transactions": list(transactions)}
        raise AssertionError(method)

    provider = TelegramStarsProvider(
        {"transaction_scan_pages": 1},
        "123456:TEST",
        api_call=fake_call,
    )
    service, intent = await _settled_stars_topup(
        db_session,
        tenant,
        user,
        registry,
        secret_storage,
        provider,
        Decimal(50),
        "external-spent-topup-001",
        "charge-external-spent-001",
    )
    wallet = (
        await db_session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "XTR",
            )
        )
    ).scalar_one()
    await LedgerService.debit(
        db_session,
        wallet,
        Decimal(20),
        reference_id="spent-before-chargeback",
        reference_type="ORDER_CHECKOUT",
    )
    transactions.append(
        {
            "id": "charge-external-spent-001",
            "amount": 50,
            "date": 1_700_000_001,
            "receiver": {
                "type": "user",
                "transaction_type": "invoice_payment",
                "user": {"id": user.telegram_id},
                "invoice_payload": f"ghbf:wallet-topup:{intent.id}",
            },
        }
    )

    reconciliation = TelegramStarsReconciliationService(payment_service=service)
    await reconciliation.scan_tenant(db_session, tenant.id)
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal(30)
    assert wallet.is_active is False

    reversal = (await db_session.execute(select(WalletTopUpReversal))).scalar_one()
    assert reversal.status == WalletTopUpReversalStatus.MANUAL_REVIEW
    event = (await db_session.execute(select(PaymentReconciliationEvent))).scalar_one()
    assert event.requires_review is True
    assert event.classification == "EXTERNAL_REVERSAL_INSUFFICIENT_FUNDS"

    with pytest.raises(InsufficientFundsError, match="financial reconciliation"):
        await LedgerService.debit(
            db_session,
            wallet,
            Decimal(1),
            reference_id="must-be-blocked",
            reference_type="ORDER_CHECKOUT",
        )

    # Value can be restored administratively or by a later settlement, but remains
    # unspendable until the operator closes the external reversal.
    await LedgerService.credit(
        db_session,
        wallet,
        Decimal(20),
        reference_id="manual-recovery-funds",
        reference_type="MANUAL_RECONCILIATION",
    )
    resolved = await service.resolve_external_wallet_topup_reversal(
        db_session,
        tenant.id,
        reversal.id,
    )
    assert resolved.status == WalletTopUpReversalStatus.COMPLETED
    assert wallet.balance == Decimal(0)
    assert wallet.is_active is True
    await db_session.refresh(event)
    assert event.requires_review is False
    assert event.classification == "EXTERNAL_REVERSAL_RESOLVED"


async def test_provider_observation_closes_interrupted_merchant_stars_refund(
    db_session: AsyncSession,
) -> None:
    from packages.payments.models import PaymentReconciliationEvent
    from packages.payments.stars_reconciliation import TelegramStarsReconciliationService

    tenant, user = await _tenant_user(db_session)
    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()
    transactions: list[dict[str, Any]] = []
    refund_calls = 0

    async def fake_call(method: str, payload: dict[str, Any]) -> Any:
        nonlocal refund_calls
        if method == "createInvoiceLink":
            return "https://t.me/$merchant-refund-recovery"
        if method == "getStarTransactions":
            return {"transactions": list(transactions)}
        if method == "refundStarPayment":
            refund_calls += 1
            return True
        raise AssertionError(method)

    provider = TelegramStarsProvider(
        {"transaction_scan_pages": 1},
        "123456:TEST",
        api_call=fake_call,
    )
    service, intent = await _settled_stars_topup(
        db_session,
        tenant,
        user,
        registry,
        secret_storage,
        provider,
        Decimal(25),
        "merchant-recovery-topup-001",
        "charge-merchant-recovery-001",
    )
    reversal = await service.request_wallet_topup_reversal(
        db_session,
        tenant.id,
        intent.id,
        "merchant-reversal-recovery-001",
    )
    reversal.status = WalletTopUpReversalStatus.RECONCILIATION_REQUIRED
    transactions.append(
        {
            "id": "charge-merchant-recovery-001",
            "amount": 25,
            "date": 1_700_000_002,
            "receiver": {
                "type": "user",
                "transaction_type": "invoice_payment",
                "user": {"id": user.telegram_id},
                "invoice_payload": f"ghbf:wallet-topup:{intent.id}",
            },
        }
    )

    reconciliation = TelegramStarsReconciliationService(payment_service=service)
    await reconciliation.scan_tenant(db_session, tenant.id)
    assert reversal.status == WalletTopUpReversalStatus.COMPLETED
    assert refund_calls == 0
    event = (await db_session.execute(select(PaymentReconciliationEvent))).scalar_one()
    assert event.classification == "EXPECTED_MERCHANT_REFUND"


async def test_stars_reconciliation_integrity_mismatch_freezes_wallet(db_session: AsyncSession) -> None:
    from packages.payments.models import PaymentReconciliationEvent
    from packages.payments.stars_reconciliation import TelegramStarsReconciliationService

    tenant, user = await _tenant_user(db_session)
    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()
    transactions: list[dict[str, Any]] = []

    async def fake_call(method: str, payload: dict[str, Any]) -> Any:
        if method == "createInvoiceLink":
            return "https://t.me/$integrity-mismatch"
        if method == "getStarTransactions":
            return {"transactions": list(transactions)}
        raise AssertionError(method)

    provider = TelegramStarsProvider(
        {"transaction_scan_pages": 1},
        "123456:TEST",
        api_call=fake_call,
    )
    service, intent = await _settled_stars_topup(
        db_session,
        tenant,
        user,
        registry,
        secret_storage,
        provider,
        Decimal(15),
        "mismatch-topup-001",
        "charge-mismatch-001",
    )
    transactions.append(
        {
            "id": "charge-mismatch-001",
            "amount": 14,
            "date": 1_700_000_003,
            "receiver": {
                "type": "user",
                "transaction_type": "invoice_payment",
                "user": {"id": user.telegram_id},
                "invoice_payload": f"ghbf:wallet-topup:{intent.id}",
            },
        }
    )
    reconciliation = TelegramStarsReconciliationService(payment_service=service)
    await reconciliation.scan_tenant(db_session, tenant.id)

    wallet = (
        await db_session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "XTR",
            )
        )
    ).scalar_one()
    assert wallet.balance == Decimal(15)
    assert wallet.is_active is False
    event = (await db_session.execute(select(PaymentReconciliationEvent))).scalar_one()
    assert event.classification == "OUTBOUND_INTEGRITY_MISMATCH"
    assert event.requires_review is True
