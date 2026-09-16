import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from packages.core.exceptions import InsufficientFundsError
from packages.payments.economics import AssetLedgerService, WalletHoldService
from packages.payments.economics_models import AssetLedgerTransaction, AssetWallet, WalletHold
from packages.payments.models import Wallet
from packages.payments.service import LedgerService
from packages.tenants.models import Tenant, User

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def seed(factory):
    async with factory() as session:
        tenant = Tenant(name="Economics race", slug=f"race-{uuid.uuid4().hex}")
        user = User(username=f"race-{uuid.uuid4().hex}")
        session.add_all([tenant, user])
        await session.flush()
        wallet = await LedgerService.get_or_create_wallet(session, tenant_id=tenant.id, user_id=user.id, currency="USD")
        await LedgerService.credit(session, wallet, Decimal(100), description="Test opening balance")
        await session.commit()
        return tenant.id, user.id, wallet.id


async def test_asset_wallet_creation_and_credit_race(postgres_session_factory):
    factory = postgres_session_factory
    tenant_id, user_id, _ = await seed(factory)
    async def credit():
        async with factory() as session:
            wallet = await AssetLedgerService.get_or_create_wallet(session, tenant_id=tenant_id, user_id=user_id, asset="USDT", network="TRON")
            tx = await AssetLedgerService.credit(session, wallet=wallet, amount=Decimal("7.125"), idempotency_key="one-deposit")
            await session.commit()
            return tx.id
    ids = await asyncio.wait_for(asyncio.gather(credit(), credit()), timeout=15)
    assert ids[0] == ids[1]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(AssetWallet)) == 1
        assert await session.scalar(select(func.count()).select_from(AssetLedgerTransaction)) == 1
        assert await session.scalar(select(AssetWallet.balance)) == Decimal("7.125")


async def test_hold_and_debit_race_cannot_overspend(postgres_session_factory):
    factory = postgres_session_factory
    _, _, wallet_id = await seed(factory)
    async def mutate(reserve):
        async with factory() as session:
            wallet = await session.get(Wallet, wallet_id)
            try:
                if reserve:
                    await WalletHoldService.reserve(session, wallet=wallet, amount=Decimal(80), idempotency_key="hold-race", reference_type="TEST", reference_id="test")
                else:
                    await LedgerService.debit(session, wallet, Decimal(80))
                await session.commit()
                return True
            except InsufficientFundsError:
                await session.rollback()
                return False
    results = await asyncio.wait_for(asyncio.gather(mutate(True), mutate(False)), timeout=15)
    assert sorted(results) == [False, True]
    async with factory() as session:
        wallet = await session.get(Wallet, wallet_id)
        assert await WalletHoldService.available_balance(session, wallet=wallet) == Decimal(20)


async def test_duplicate_hold_capture_debits_once(postgres_session_factory):
    factory = postgres_session_factory
    _, _, wallet_id = await seed(factory)
    async with factory() as session:
        wallet = await session.get(Wallet, wallet_id)
        hold = await WalletHoldService.reserve(session, wallet=wallet, amount=Decimal(30), idempotency_key="capture-race", reference_type="TEST", reference_id="test")
        await session.commit()
        hold_id = hold.id
    async def capture():
        async with factory() as session:
            hold = await session.get(WalletHold, hold_id)
            tx = await WalletHoldService.capture(session, hold=hold)
            await session.commit()
            return tx.id
    ids = await asyncio.wait_for(asyncio.gather(capture(), capture()), timeout=15)
    assert ids[0] == ids[1]
    async with factory() as session:
        assert await session.scalar(select(Wallet.balance).where(Wallet.id == wallet_id)) == Decimal(70)


@pytest.mark.parametrize("target", ["ASSET_WALLET", "SETTLEMENT_WALLET"])
async def test_flexible_deposit_settlement_race_credits_once(postgres_session_factory, target):
    from packages.payments.economics import FxPolicyService
    from packages.payments.economics_models import (
        AutoCreditTarget,
        FlexibleDepositSession,
        FlexibleDepositStatus,
        FxPolicyMode,
    )
    from packages.payments.flexible_deposits import FlexibleDepositService
    from packages.payments.models import (
        PaymentMethodConfig,
        PaymentMethodType,
        PaymentVerificationMode,
    )
    from packages.payments.payment_service import PaymentService
    from packages.payments.providers.interface import FlexibleDepositResult
    from packages.payments.providers.registry import PaymentProviderRegistry
    from packages.telegram.secrets import EnvSecretStorage

    factory = postgres_session_factory
    tenant_id, user_id, wallet_id = await seed(factory)
    async with factory() as session:
        method = PaymentMethodConfig(tenant_id=tenant_id, code="race-method", display_name="Race",
            method_type=PaymentMethodType.CRYPTO_GATEWAY, verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
            provider_name="gozapay", settings_json={}, is_enabled=True)
        session.add(method)
        await session.flush()
        policy_id = None
        if target == "SETTLEMENT_WALLET":
            policy = await FxPolicyService.create_policy(session, tenant_id=tenant_id, from_asset="USDT",
                from_network="TRON", to_currency="USD", mode=FxPolicyMode.PARITY, rate=Decimal(1), max_auto_credit_amount=Decimal(100))
            policy_id = policy.id
        deposit = FlexibleDepositSession(tenant_id=tenant_id, user_id=user_id, payment_method_id=method.id,
            provider="gozapay", provider_deposit_id="concurrent-deposit", idempotency_key="concurrent-deposit",
            status=FlexibleDepositStatus.PENDING, auto_credit_enabled=True, auto_credit_target=AutoCreditTarget(target),
            auto_credit_basis="GROSS_RECEIVED", settlement_currency="USD" if policy_id else None,
            fx_policy_id=policy_id, policy_snapshot_json={})
        session.add(deposit)
        await session.commit()
        deposit_id = deposit.id

    class Provider:
        supports_flexible_deposits = True
        async def create_flexible_deposit(self, request):
            raise AssertionError("Reconciliation must not create a deposit")
        async def get_flexible_deposit(self, provider_deposit_id):
            return FlexibleDepositResult(provider_deposit_id, FlexibleDepositStatus.SETTLED_REVIEW,
                asset="USDT", network="TRON", amount_received=Decimal(12))
    registry = PaymentProviderRegistry()
    registry.register_instance(tenant_id, "gozapay", Provider())
    service = FlexibleDepositService(PaymentService(registry=registry, secret_storage=EnvSecretStorage({})))
    async def settle():
        async with factory() as session:
            result = await service.reconcile(session, tenant_id=tenant_id, deposit_id=deposit_id)
            await session.commit()
            return result.status
    results = await asyncio.wait_for(asyncio.gather(settle(), settle()), timeout=15)
    assert results == [FlexibleDepositStatus.CREDITED, FlexibleDepositStatus.CREDITED]
    async with factory() as session:
        if target == "ASSET_WALLET":
            assert await session.scalar(select(AssetWallet.balance)) == Decimal(12)
            assert await session.scalar(select(func.count()).select_from(AssetLedgerTransaction)) == 1
        else:
            assert await session.scalar(select(Wallet.balance).where(Wallet.id == wallet_id)) == Decimal(112)
