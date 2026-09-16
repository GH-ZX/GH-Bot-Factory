from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.payments.models import (
    FinancialResolutionCase,
    PaymentIntent,
    PaymentIntentPurpose,
    PaymentMethodConfig,
    PaymentMethodType,
    PaymentObservation,
    PaymentObservationSource,
    PaymentObservationStatus,
    PaymentProviderConfig,
    PaymentVerificationMode,
    Wallet,
    WalletTopUpReversal,
    WalletTopUpReversalStatus,
)
from packages.payments.operations import PaymentOperationsService
from packages.payments.payment_service import PaymentService
from packages.payments.provider_reconciliation_worker import PaymentProviderReconciliationWorker
from packages.payments.providers.mock import MockPaymentProvider
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.payments.reconciliation import PaymentReconciliationService
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


async def _tenant_user(session: AsyncSession, prefix: str) -> tuple[Tenant, User]:
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(name=f"{prefix} Tenant", slug=f"{prefix.lower()}-{suffix}", is_active=True)
    user = User(username=f"{prefix.lower()}_{suffix}", is_active=True)
    session.add_all([tenant, user])
    await session.flush()
    return tenant, user


async def test_provider_reconciliation_worker_polls_without_webhook_and_credits_once(
    db_session: AsyncSession,
) -> None:
    tenant, user = await _tenant_user(db_session, "WorkerPay")
    registry = PaymentProviderRegistry()
    provider = MockPaymentProvider(default_create_status=PaymentIntentStatus.PENDING)
    registry.register_instance(tenant.id, "mock", provider)
    service = PaymentService(registry=registry, secret_storage=EnvSecretStorage({}))
    db_session.add(
        PaymentProviderConfig(
            tenant_id=tenant.id,
            provider_name="mock",
            is_enabled=True,
            credentials_ref="unused-override",
            settings_json={
                "topup_enabled": True,
                "topup_min_amount": "1.00",
                "topup_max_amount": "1000.00",
                "topup_currencies": ["USD"],
            },
        )
    )
    await db_session.flush()
    intent = await service.create_wallet_topup_intent(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        amount=Decimal("25.00"),
        currency="USD",
        provider_name="mock",
        idempotency_key="provider-worker-0001",
    )
    assert intent.status == PaymentIntentStatus.PENDING
    provider.payments[intent.provider_payment_id]["status"] = PaymentIntentStatus.SUCCEEDED

    worker = PaymentProviderReconciliationWorker(
        reconciliation_service=PaymentReconciliationService(payment_service=service),
        enabled=True,
        batch_size=10,
    )
    first = await worker.run_once(db_session)
    second = await worker.run_once(db_session)

    assert first == {
        "scanned": 1,
        "succeeded": 1,
        "processing": 0,
        "unknown": 0,
        "terminal": 0,
    }
    assert second["scanned"] == 0
    wallet = (
        await db_session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "USD",
            )
        )
    ).scalar_one()
    assert wallet.balance == Decimal("25.00")


async def test_operations_snapshot_is_tenant_scoped_read_only_and_surfaces_financial_risk(
    db_session: AsyncSession,
) -> None:
    tenant, user = await _tenant_user(db_session, "Ops")
    other_tenant, other_user = await _tenant_user(db_session, "OtherOps")
    now = datetime.now(UTC)
    old = now - timedelta(hours=2)

    method = PaymentMethodConfig(
        tenant_id=tenant.id,
        code="manual-usdt",
        display_name="Manual USDT",
        method_type=PaymentMethodType.MANUAL_TRANSFER,
        verification_mode=PaymentVerificationMode.MANUAL,
        asset="USDT",
        network="TRC20",
        is_enabled=True,
        requires_admin_approval=True,
        settings_json={},
    )
    db_session.add(method)
    db_session.add(
        PaymentProviderConfig(
            tenant_id=tenant.id,
            provider_name="mock",
            is_enabled=True,
            credentials_ref="unused",
            settings_json={},
        )
    )
    await db_session.flush()

    ambiguous = PaymentIntent(
        tenant_id=tenant.id,
        user_id=user.id,
        purpose=PaymentIntentPurpose.WALLET_TOPUP,
        payment_method_id=method.id,
        provider="bybit_pay",
        provider_payment_id=None,
        amount=Decimal("10.00"),
        currency="USD",
        status=PaymentIntentStatus.UNKNOWN,
        idempotency_key="ops-ambiguous-0001",
        metadata_json={
            "_provider_creation_ambiguity": {"reason": "transport_outcome_unknown"}
        },
        updated_at=old,
    )
    stale = PaymentIntent(
        tenant_id=tenant.id,
        user_id=user.id,
        purpose=PaymentIntentPurpose.WALLET_TOPUP,
        payment_method_id=method.id,
        provider="mock",
        provider_payment_id="mock-stale-1",
        amount=Decimal("11.00"),
        currency="USD",
        status=PaymentIntentStatus.PROCESSING,
        idempotency_key="ops-stale-0001",
        metadata_json={},
        updated_at=old,
    )
    other = PaymentIntent(
        tenant_id=other_tenant.id,
        user_id=other_user.id,
        purpose=PaymentIntentPurpose.WALLET_TOPUP,
        provider="mock",
        provider_payment_id="other-stale-1",
        amount=Decimal("999.00"),
        currency="USD",
        status=PaymentIntentStatus.UNKNOWN,
        idempotency_key="other-ops-0001",
        metadata_json={
            "_provider_creation_ambiguity": {"reason": "transport_outcome_unknown"}
        },
        updated_at=old,
    )
    db_session.add_all([ambiguous, stale, other])
    await db_session.flush()

    observation = PaymentObservation(
        tenant_id=tenant.id,
        payment_intent_id=ambiguous.id,
        payment_method_id=method.id,
        source=PaymentObservationSource.MANUAL,
        status=PaymentObservationStatus.MANUAL_REVIEW,
        external_reference="proof-1",
        evidence_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
        details_json={},
        is_final=False,
    )
    wallet = Wallet(
        tenant_id=tenant.id,
        user_id=user.id,
        currency="USD",
        balance=Decimal("100.00"),
        is_active=True,
    )
    db_session.add_all([observation, wallet])
    await db_session.flush()
    reversal = WalletTopUpReversal(
        tenant_id=tenant.id,
        payment_intent_id=stale.id,
        user_id=user.id,
        wallet_id=wallet.id,
        provider="mock",
        original_provider_payment_id="mock-stale-1",
        amount=Decimal("11.00"),
        currency="USD",
        idempotency_key="ops-reversal-0001",
        status=WalletTopUpReversalStatus.RECONCILIATION_REQUIRED,
        metadata_json={},
    )
    case = FinancialResolutionCase(
        tenant_id=tenant.id,
        source_key="ops-case-1",
        case_type="PAYMENT_TEST",
        severity="HIGH",
        payment_intent_id=stale.id,
        metadata_json={},
    )
    db_session.add_all([reversal, case])
    await db_session.flush()

    before_balance = wallet.balance
    snapshot = await PaymentOperationsService().snapshot(
        db_session,
        tenant_id=tenant.id,
        stale_after_seconds=300,
        now=now,
    )

    assert snapshot.status == "ATTENTION"
    assert snapshot.open_intents == 2
    assert snapshot.processing_intents == 1
    assert snapshot.unknown_intents == 1
    assert snapshot.creation_ambiguities == 1
    assert snapshot.stale_provider_intents == 1
    assert snapshot.manual_review_observations == 1
    assert snapshot.reversal_reconciliation_items == 1
    assert snapshot.open_financial_cases == 1
    assert snapshot.enabled_provider_configs == 1
    assert snapshot.enabled_payment_methods == 1
    assert {alert.code for alert in snapshot.alerts} == {
        "PAYMENT_CREATION_AMBIGUITY",
        "STALE_PROVIDER_PAYMENT",
        "MANUAL_PAYMENT_REVIEW",
        "REVERSAL_RECONCILIATION",
        "FINANCIAL_CASES_OPEN",
    }
    assert wallet.balance == before_balance
