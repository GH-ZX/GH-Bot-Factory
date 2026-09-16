from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.payments.exceptions import PaymentError, PaymentIntegrityError
from packages.payments.models import (
    PaymentAssuranceLevel,
    PaymentMethodType,
    PaymentObservation,
    PaymentObservationSource,
    PaymentObservationStatus,
    PaymentVerificationMode,
    Wallet,
)
from packages.payments.onchain_reconciliation import OnChainPaymentReconciliationWorker
from packages.payments.platform import (
    OnChainVerificationResult,
    OnChainVerifierRegistry,
    PaymentPlatformService,
)
from packages.payments.state_machine import PaymentIntentStatus
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


class StubChainVerifier:
    verifier_name = "stub-chain"

    def __init__(self, result: OnChainVerificationResult) -> None:
        self.result = result
        self.calls = 0

    async def verify_transaction(
        self,
        network: str,
        tx_hash: str,
        *,
        expected_asset: str | None = None,
        expected_destination: str | None = None,
    ) -> OnChainVerificationResult:
        self.calls += 1
        return self.result


async def create_tenant_user(session: AsyncSession, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(name=f"Payment {suffix}", slug=f"payment-{suffix}", is_active=True)
    user = User(username=f"pay_{suffix}_{uuid.uuid4().hex[:8]}", is_active=True)
    session.add_all([tenant, user])
    await session.flush()
    return tenant, user


async def create_manual_method(service: PaymentPlatformService, session: AsyncSession, tenant_id: uuid.UUID):
    return await service.create_method(
        session,
        tenant_id=tenant_id,
        code="binance-id",
        display_name="Binance ID transfer",
        method_type=PaymentMethodType.EXCHANGE_TRANSFER,
        verification_mode=PaymentVerificationMode.MANUAL,
        instructions="Transfer and submit the reference for admin review.",
        requires_admin_approval=True,
        settings={"topup_min_amount": "5.00", "topup_max_amount": "500.00", "topup_currencies": ["USD"]},
    )


async def create_onchain_method(service: PaymentPlatformService, session: AsyncSession, tenant_id: uuid.UUID):
    return await service.create_method(
        session,
        tenant_id=tenant_id,
        code="usdt-trc20",
        display_name="USDT TRC20",
        method_type=PaymentMethodType.SELF_CUSTODY,
        verification_mode=PaymentVerificationMode.ONCHAIN,
        asset="USDT",
        network="TRON",
        destination_address="TTESTDESTINATION111111111111111111",
        instructions="Send only USDT on TRON/TRC20.",
        settings={"topup_min_amount": "5.00", "topup_max_amount": "1000.00", "topup_currencies": ["USD"]},
    )


async def test_method_config_rejects_secret_like_settings(db_session: AsyncSession) -> None:
    tenant, _ = await create_tenant_user(db_session, "secret-settings")
    service = PaymentPlatformService()

    with pytest.raises(PaymentIntegrityError, match="credential-like"):
        await service.create_method(
            db_session,
            tenant_id=tenant.id,
            code="unsafe-gateway",
            display_name="Unsafe",
            method_type=PaymentMethodType.CRYPTO_GATEWAY,
            verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
            provider_name="gateway",
            settings={"api_key": "must-not-live-here"},
        )


async def test_self_custody_requires_explicit_chain_identity(db_session: AsyncSession) -> None:
    tenant, _ = await create_tenant_user(db_session, "shape")
    service = PaymentPlatformService()

    with pytest.raises(PaymentError, match="asset, network, and destination_address"):
        await service.create_method(
            db_session,
            tenant_id=tenant.id,
            code="broken-usdt",
            display_name="Broken USDT",
            method_type=PaymentMethodType.SELF_CUSTODY,
            verification_mode=PaymentVerificationMode.ONCHAIN,
            asset="USDT",
            network="TRON",
        )


async def test_local_topup_is_idempotent_and_snapshots_instructions(db_session: AsyncSession) -> None:
    tenant, user = await create_tenant_user(db_session, "idempotent")
    service = PaymentPlatformService()
    method = await create_manual_method(service, db_session, tenant.id)

    first = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        method_id=method.id,
        amount=Decimal("20.00"),
        currency="USD",
        idempotency_key="manual-topup-0001",
    )
    second = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        method_id=method.id,
        amount=Decimal("20.00"),
        currency="USD",
        idempotency_key="manual-topup-0001",
    )

    assert first.id == second.id
    assert first.payment_method_id == method.id
    assert first.metadata_json["payment_instruction"]["method_code"] == "binance-id"


async def test_customer_submission_is_observation_not_credit(db_session: AsyncSession) -> None:
    tenant, user = await create_tenant_user(db_session, "manual-observation")
    service = PaymentPlatformService()
    method = await create_manual_method(service, db_session, tenant.id)
    intent = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        method_id=method.id,
        amount=Decimal("25.00"),
        currency="USD",
        idempotency_key="manual-topup-0002",
    )

    observation = await service.submit_observation(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        intent_id=intent.id,
        source=PaymentObservationSource.EXCHANGE,
        external_reference="binance-transfer-abc",
        details={"note": "customer says paid"},
    )

    assert observation.status == PaymentObservationStatus.MANUAL_REVIEW
    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.tenant_id == tenant.id, Wallet.user_id == user.id)
        )
    ).scalar_one_or_none()
    assert wallet is None


async def test_manual_approval_credits_exactly_once(db_session: AsyncSession) -> None:
    tenant, user = await create_tenant_user(db_session, "approve-once")
    admin = User(username=f"admin_{uuid.uuid4().hex[:8]}", is_active=True)
    db_session.add(admin)
    await db_session.flush()
    service = PaymentPlatformService()
    method = await create_manual_method(service, db_session, tenant.id)
    intent = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        method_id=method.id,
        amount=Decimal("30.00"),
        currency="USD",
        idempotency_key="manual-topup-0003",
    )
    observation = await service.submit_observation(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        intent_id=intent.id,
        source=PaymentObservationSource.EXCHANGE,
        external_reference="manual-ref-001",
    )

    first = await service.approve_manual_observation(
        db_session,
        tenant_id=tenant.id,
        observation_id=observation.id,
        actor_user_id=admin.id,
    )
    second = await service.approve_manual_observation(
        db_session,
        tenant_id=tenant.id,
        observation_id=observation.id,
        actor_user_id=admin.id,
    )

    assert first.id == second.id
    assert first.status == PaymentObservationStatus.VERIFIED
    assert first.assurance_level == PaymentAssuranceLevel.MANUAL_APPROVED
    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.tenant_id == tenant.id, Wallet.user_id == user.id)
        )
    ).scalar_one()
    assert wallet.balance == Decimal("30.00")


async def test_manual_approval_cannot_override_authoritative_amount(db_session: AsyncSession) -> None:
    tenant, user = await create_tenant_user(db_session, "wrong-amount")
    admin = User(username=f"admin_{uuid.uuid4().hex[:8]}", is_active=True)
    db_session.add(admin)
    await db_session.flush()
    service = PaymentPlatformService()
    method = await create_manual_method(service, db_session, tenant.id)
    intent = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        method_id=method.id,
        amount=Decimal("40.00"),
        currency="USD",
        idempotency_key="manual-topup-0004",
    )
    observation = await service.submit_observation(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        intent_id=intent.id,
        source=PaymentObservationSource.EXCHANGE,
        external_reference="manual-ref-002",
    )

    with pytest.raises(PaymentIntegrityError, match="cannot change"):
        await service.approve_manual_observation(
            db_session,
            tenant_id=tenant.id,
            observation_id=observation.id,
            actor_user_id=admin.id,
            approved_amount=Decimal("41.00"),
            approved_currency="USD",
        )


async def test_same_onchain_tx_cannot_credit_two_tenants(db_session: AsyncSession) -> None:
    service = PaymentPlatformService()
    tenant_a, user_a = await create_tenant_user(db_session, "chain-a")
    tenant_b, user_b = await create_tenant_user(db_session, "chain-b")
    method_a = await create_onchain_method(service, db_session, tenant_a.id)
    method_b = await create_onchain_method(service, db_session, tenant_b.id)
    intent_a = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant_a.id,
        user_id=user_a.id,
        method_id=method_a.id,
        amount=Decimal("10.00"),
        currency="USD",
        idempotency_key="chain-topup-a1",
    )
    intent_b = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant_b.id,
        user_id=user_b.id,
        method_id=method_b.id,
        amount=Decimal("10.00"),
        currency="USD",
        idempotency_key="chain-topup-b1",
    )

    await service.submit_observation(
        db_session,
        tenant_id=tenant_a.id,
        user_id=user_a.id,
        intent_id=intent_a.id,
        source=PaymentObservationSource.ONCHAIN,
        external_reference="0x-global-tx-001",
        asset_amount=Decimal(10),
    )
    with pytest.raises(PaymentIntegrityError, match="already been used"):
        await service.submit_observation(
            db_session,
            tenant_id=tenant_b.id,
            user_id=user_b.id,
            intent_id=intent_b.id,
            source=PaymentObservationSource.ONCHAIN,
            external_reference="0x-global-tx-001",
            asset_amount=Decimal(10),
        )


async def test_onchain_finality_does_not_imply_fx_or_stablecoin_parity(db_session: AsyncSession) -> None:
    tenant, user = await create_tenant_user(db_session, "chain-manual-review")
    registry = OnChainVerifierRegistry()
    verifier = StubChainVerifier(
        OnChainVerificationResult(
            network="TRON",
            tx_hash="tx-final-001",
            asset="USDT",
            destination_address="TTESTDESTINATION111111111111111111",
            asset_amount=Decimal("50.000000"),
            confirmations=30,
            is_final=True,
            succeeded=True,
            observed_at=datetime.now(UTC),
        )
    )
    registry.register("TRON", verifier)
    service = PaymentPlatformService(onchain_registry=registry)
    method = await create_onchain_method(service, db_session, tenant.id)
    intent = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        method_id=method.id,
        amount=Decimal("50.00"),
        currency="USD",
        idempotency_key="chain-topup-finality",
    )
    observation = await service.submit_observation(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        intent_id=intent.id,
        source=PaymentObservationSource.ONCHAIN,
        external_reference="tx-final-001",
        asset_amount=Decimal(50),
    )

    verified = await service.verify_onchain_observation(
        db_session,
        tenant_id=tenant.id,
        observation_id=observation.id,
    )

    assert verified.status == PaymentObservationStatus.MANUAL_REVIEW
    assert verified.assurance_level == PaymentAssuranceLevel.ONCHAIN_VERIFIED
    assert verified.is_final is True
    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.tenant_id == tenant.id, Wallet.user_id == user.id)
        )
    ).scalar_one_or_none()
    assert wallet is None


async def test_onchain_verified_settlement_with_explicit_authoritative_quote_credits_once(
    db_session: AsyncSession,
) -> None:
    tenant, user = await create_tenant_user(db_session, "chain-credit")
    registry = OnChainVerifierRegistry()
    verifier = StubChainVerifier(
        OnChainVerificationResult(
            network="TRON",
            tx_hash="tx-final-002",
            asset="USDT",
            destination_address="TTESTDESTINATION111111111111111111",
            asset_amount=Decimal("75.000000"),
            confirmations=30,
            is_final=True,
            succeeded=True,
        )
    )
    registry.register("TRON", verifier)
    service = PaymentPlatformService(onchain_registry=registry)
    method = await create_onchain_method(service, db_session, tenant.id)
    intent = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        method_id=method.id,
        amount=Decimal("75.00"),
        currency="USD",
        idempotency_key="chain-topup-credit",
    )
    await service.record_quote(
        db_session,
        tenant_id=tenant.id,
        intent_id=intent.id,
        settlement_amount=Decimal("75.00"),
        settlement_currency="USD",
        asset_amount=Decimal("75.000000"),
        asset="USDT",
        network="TRON",
        rate=Decimal("1.0"),
        rate_source="test-authoritative-quote",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        quote_reference="quote-75",
    )
    observation = await service.submit_observation(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        intent_id=intent.id,
        source=PaymentObservationSource.ONCHAIN,
        external_reference="tx-final-002",
        asset_amount=Decimal(75),
    )

    first = await service.verify_onchain_observation(
        db_session,
        tenant_id=tenant.id,
        observation_id=observation.id,
    )
    second = await service.verify_onchain_observation(
        db_session,
        tenant_id=tenant.id,
        observation_id=observation.id,
    )

    assert first.status == PaymentObservationStatus.VERIFIED
    assert second.id == first.id
    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.tenant_id == tenant.id, Wallet.user_id == user.id)
        )
    ).scalar_one()
    assert wallet.balance == Decimal("75.00")


async def test_observation_tenant_isolation(db_session: AsyncSession) -> None:
    service = PaymentPlatformService()
    tenant_a, user_a = await create_tenant_user(db_session, "iso-a")
    tenant_b, _ = await create_tenant_user(db_session, "iso-b")
    method = await create_manual_method(service, db_session, tenant_a.id)
    intent = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant_a.id,
        user_id=user_a.id,
        method_id=method.id,
        amount=Decimal("10.00"),
        currency="USD",
        idempotency_key="iso-topup-001",
    )
    observation = await service.submit_observation(
        db_session,
        tenant_id=tenant_a.id,
        user_id=user_a.id,
        intent_id=intent.id,
        source=PaymentObservationSource.EXCHANGE,
        external_reference="iso-ref",
    )

    with pytest.raises(PaymentError, match="not found"):
        await service.get_observation(
            db_session,
            tenant_id=tenant_b.id,
            observation_id=observation.id,
        )

    rows = (
        await db_session.execute(
            select(PaymentObservation).where(PaymentObservation.tenant_id == tenant_a.id)
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_onchain_worker_converges_pending_observation_without_public_webhook(
    db_session: AsyncSession,
) -> None:
    tenant, user = await create_tenant_user(db_session, "chain-worker")
    registry = OnChainVerifierRegistry()
    verifier = StubChainVerifier(
        OnChainVerificationResult(
            network="TRON",
            tx_hash="tx-worker-001",
            asset="USDT",
            destination_address="TTESTDESTINATION111111111111111111",
            asset_amount=Decimal("60.000000"),
            confirmations=40,
            is_final=True,
            succeeded=True,
            observed_at=datetime.now(UTC),
        )
    )
    registry.register("TRON", verifier)
    service = PaymentPlatformService(onchain_registry=registry)
    method = await create_onchain_method(service, db_session, tenant.id)
    intent = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        method_id=method.id,
        amount=Decimal("60.00"),
        currency="USD",
        idempotency_key="chain-worker-topup",
    )
    await service.record_quote(
        db_session,
        tenant_id=tenant.id,
        intent_id=intent.id,
        settlement_amount=Decimal("60.00"),
        settlement_currency="USD",
        asset_amount=Decimal("60.000000"),
        asset="USDT",
        network="TRON",
        rate=Decimal("1.0"),
        rate_source="worker-test-quote",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    observation = await service.submit_observation(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        intent_id=intent.id,
        source=PaymentObservationSource.ONCHAIN,
        external_reference="tx-worker-001",
        asset_amount=Decimal(60),
    )

    worker = OnChainPaymentReconciliationWorker(
        registry=registry,
        interval_seconds=30,
        batch_size=10,
        enabled=True,
    )
    stats = await worker.run_once(db_session)

    assert stats == {"scanned": 1, "verified": 1, "review": 0, "pending": 0, "errors": 0}
    assert observation.status == PaymentObservationStatus.VERIFIED
    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.tenant_id == tenant.id, Wallet.user_id == user.id)
        )
    ).scalar_one()
    assert wallet.balance == Decimal("60.00")


async def test_provider_backed_method_uses_gateway_flow_and_binds_method(db_session: AsyncSession) -> None:
    from packages.payments.models import PaymentProviderConfig
    from packages.payments.payment_service import PaymentService
    from packages.payments.providers.registry import PaymentProviderRegistry
    from packages.telegram.secrets import EnvSecretStorage

    tenant, user = await create_tenant_user(db_session, "provider-method")
    secret_storage = EnvSecretStorage({"MOCK_PAY_KEY": "unused-test-secret"})
    registry = PaymentProviderRegistry()
    payment_service = PaymentService(registry=registry, secret_storage=secret_storage)
    service = PaymentPlatformService(payment_service=payment_service)

    db_session.add(
        PaymentProviderConfig(
            tenant_id=tenant.id,
            provider_name="mock",
            is_enabled=True,
            credentials_ref="MOCK_PAY_KEY",
            settings_json={
                "topup_enabled": True,
                "topup_min_amount": "5.00",
                "topup_max_amount": "500.00",
                "topup_currencies": ["USD"],
            },
        )
    )
    method = await service.create_method(
        db_session,
        tenant_id=tenant.id,
        code="gateway-usd",
        display_name="Gateway USD",
        method_type=PaymentMethodType.CRYPTO_GATEWAY,
        verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
        provider_name="mock",
        settings={
            "topup_min_amount": "5.00",
            "topup_max_amount": "500.00",
            "topup_currencies": ["USD"],
        },
    )
    await db_session.flush()

    intent = await service.create_topup_intent(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        method_id=method.id,
        amount=Decimal("20.00"),
        currency="USD",
        idempotency_key="gateway-method-0001",
    )

    assert intent.payment_method_id == method.id
    assert intent.provider == "mock"
    assert intent.provider_payment_id == "mock_pay_gateway-method-0001"
    assert intent.status == PaymentIntentStatus.PENDING

    with pytest.raises(PaymentError, match="provider top-up flow"):
        await service.create_local_topup_intent(
            db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            method_id=method.id,
            amount=Decimal("21.00"),
            currency="USD",
            idempotency_key="gateway-method-local-bypass",
        )


async def test_provider_verification_attributes_detect_upstream_identity_drift(
    db_session: AsyncSession,
) -> None:
    from packages.payments.models import PaymentIntent, PaymentIntentPurpose
    from packages.payments.payment_service import PaymentService

    tenant, user = await create_tenant_user(db_session, "provider-drift")
    intent = PaymentIntent(
        tenant_id=tenant.id,
        order_id=None,
        purpose=PaymentIntentPurpose.WALLET_TOPUP,
        user_id=user.id,
        provider="nowpayments",
        currency="USD",
        amount=Decimal("10.00"),
        status=PaymentIntentStatus.PENDING,
        idempotency_key="provider-drift-0001",
        metadata_json={"_provider_verification": {"pay_currency": "usdttrc20"}},
    )
    db_session.add(intent)
    await db_session.flush()

    PaymentService.assert_provider_verification_attributes(
        intent, {"pay_currency": "usdttrc20"}
    )
    with pytest.raises(PaymentIntegrityError, match="pay_currency"):
        PaymentService.assert_provider_verification_attributes(
            intent, {"pay_currency": "usdtbsc"}
        )


async def test_same_hex_onchain_tx_with_different_case_cannot_bypass_global_replay_guard(
    db_session: AsyncSession,
) -> None:
    service = PaymentPlatformService()
    tenant_a, user_a = await create_tenant_user(db_session, "chain-case-a")
    tenant_b, user_b = await create_tenant_user(db_session, "chain-case-b")
    method_a = await create_onchain_method(service, db_session, tenant_a.id)
    method_b = await create_onchain_method(service, db_session, tenant_b.id)
    intent_a = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant_a.id,
        user_id=user_a.id,
        method_id=method_a.id,
        amount=Decimal("10.00"),
        currency="USD",
        idempotency_key="chain-case-a",
    )
    intent_b = await service.create_local_topup_intent(
        db_session,
        tenant_id=tenant_b.id,
        user_id=user_b.id,
        method_id=method_b.id,
        amount=Decimal("10.00"),
        currency="USD",
        idempotency_key="chain-case-b",
    )
    tx_lower = "ab" * 32
    tx_upper = tx_lower.upper()

    first = await service.submit_observation(
        db_session,
        tenant_id=tenant_a.id,
        user_id=user_a.id,
        intent_id=intent_a.id,
        source=PaymentObservationSource.ONCHAIN,
        external_reference=tx_upper,
    )
    assert first.external_reference == tx_lower

    with pytest.raises(PaymentIntegrityError, match="already been used"):
        await service.submit_observation(
            db_session,
            tenant_id=tenant_b.id,
            user_id=user_b.id,
            intent_id=intent_b.id,
            source=PaymentObservationSource.ONCHAIN,
            external_reference=tx_lower,
        )
