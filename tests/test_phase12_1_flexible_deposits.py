from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.payments.economics import FxPolicyService
from packages.payments.economics_models import (
    AssetWallet,
    FlexibleDepositStatus,
    FxPolicyMode,
)
from packages.payments.flexible_deposits import FlexibleDepositService
from packages.payments.models import PaymentMethodType, PaymentVerificationMode, Wallet
from packages.payments.payment_service import PaymentService
from packages.payments.platform import PaymentPlatformService
from packages.payments.providers.gozapay import GoZaPayProvider
from packages.payments.providers.interface import (
    FlexibleDepositCreateRequest,
    FlexibleDepositResult,
)
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


class StubFlexibleProvider:
    provider_name = "gozapay"
    supports_flexible_deposits = True

    def __init__(self, *, created: FlexibleDepositResult, reconciled: FlexibleDepositResult) -> None:
        self.created = created
        self.reconciled = reconciled
        self.create_calls = 0
        self.get_calls = 0

    async def create_flexible_deposit(self, request: FlexibleDepositCreateRequest) -> FlexibleDepositResult:
        self.create_calls += 1
        assert request.idempotency_key
        assert request.order_reference.startswith("DEP-")
        return self.created

    async def get_flexible_deposit(self, provider_deposit_id: str) -> FlexibleDepositResult:
        self.get_calls += 1
        assert provider_deposit_id == self.reconciled.provider_deposit_id
        return self.reconciled


async def _tenant_user(session: AsyncSession, prefix: str) -> tuple[Tenant, User]:
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(name=f"{prefix} Tenant", slug=f"{prefix}-{suffix}", is_active=True)
    user = User(username=f"{prefix}_{suffix}", is_active=True)
    session.add_all([tenant, user])
    await session.flush()
    return tenant, user


def _service(tenant_id: uuid.UUID, provider: StubFlexibleProvider) -> FlexibleDepositService:
    registry = PaymentProviderRegistry()
    registry.register_instance(tenant_id, "gozapay", provider)  # type: ignore[arg-type]
    payment = PaymentService(registry=registry, secret_storage=EnvSecretStorage({}))
    return FlexibleDepositService(payment_service=payment)


async def _method(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    auto_credit: bool,
    target: str = "ASSET_WALLET",
    extra_settings: dict | None = None,
):
    settings = {
        "flexible_deposits_enabled": True,
        "allowed_assets": ["USDT"],
        "allowed_networks": ["TRON"],
        "auto_credit_basis": "NET_AFTER_FEE",
        **(extra_settings or {}),
    }
    return await PaymentPlatformService().create_method(
        session,
        tenant_id=tenant_id,
        code=f"goza-{uuid.uuid4().hex[:8]}",
        display_name="GoZaPay flexible deposit",
        method_type=PaymentMethodType.CRYPTO_GATEWAY,
        verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
        provider_name="gozapay",
        auto_credit_enabled=auto_credit,
        auto_credit_target=target,
        settings=settings,
    )


async def test_gozapay_open_amount_invoice_uses_flexible_contract_without_parity_ack() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        seen.update(body)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": "flex-1",
                    "order_id": body["order_id"],
                    "status": "awaiting_selection",
                    "payment_url": "https://gozapay.com/pay/flex-1",
                    "flexible": True,
                    "amount_received": "0",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoZaPayProvider(
            settings={"experimental_risk_acknowledged": True},
            api_key="key",
            webhook_secret=None,
            http_client=client,
        )
        result = await provider.create_flexible_deposit(
            FlexibleDepositCreateRequest(
                idempotency_key="flexible-create-001",
                order_reference="DEP-TEST",
            )
        )

    assert seen["flexible"] is True
    assert "amount" not in seen
    assert "chain" not in seen
    assert "coin" not in seen
    assert result.status == FlexibleDepositStatus.PENDING
    assert result.provider_deposit_id == "flex-1"


async def test_flexible_deposit_auto_credit_off_stops_at_review(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session, "flexreview")
    method = await _method(db_session, tenant.id, auto_credit=False)
    provider = StubFlexibleProvider(
        created=FlexibleDepositResult("dep-1", FlexibleDepositStatus.PENDING),
        reconciled=FlexibleDepositResult(
            "dep-1",
            FlexibleDepositStatus.SETTLED_REVIEW,
            asset="USDT",
            network="TRON",
            amount_received=Decimal("12.5"),
            fee_amount=Decimal("0.1"),
        ),
    )
    service = _service(tenant.id, provider)
    deposit = await service.create(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        payment_method_id=method.id,
        idempotency_key="flex-review-0001",
    )
    deposit = await service.reconcile(db_session, tenant_id=tenant.id, deposit_id=deposit.id)

    assert deposit.status == FlexibleDepositStatus.SETTLED_REVIEW
    assert deposit.credited_at is None
    assert await db_session.scalar(select(AssetWallet).where(AssetWallet.tenant_id == tenant.id)) is None


async def test_flexible_asset_auto_credit_is_exactly_once_and_uses_net_basis(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session, "flexasset")
    method = await _method(db_session, tenant.id, auto_credit=True)
    provider = StubFlexibleProvider(
        created=FlexibleDepositResult("dep-asset", FlexibleDepositStatus.PENDING),
        reconciled=FlexibleDepositResult(
            "dep-asset",
            FlexibleDepositStatus.SETTLED_REVIEW,
            asset="USDT",
            network="TRON",
            amount_received=Decimal("25.123456"),
            fee_amount=Decimal("0.123456"),
        ),
    )
    service = _service(tenant.id, provider)
    deposit = await service.create(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        payment_method_id=method.id,
        idempotency_key="flex-asset-0001",
    )
    first = await service.reconcile(db_session, tenant_id=tenant.id, deposit_id=deposit.id)
    second = await service.reconcile(db_session, tenant_id=tenant.id, deposit_id=deposit.id)
    wallet = await db_session.scalar(
        select(AssetWallet).where(
            AssetWallet.tenant_id == tenant.id,
            AssetWallet.user_id == user.id,
            AssetWallet.asset == "USDT",
            AssetWallet.network == "TRON",
        )
    )

    assert first.status == FlexibleDepositStatus.CREDITED
    assert second.status == FlexibleDepositStatus.CREDITED
    assert wallet is not None
    assert wallet.balance == Decimal("25.000000000000000000")
    assert provider.get_calls == 2


async def test_flexible_fiat_auto_credit_requires_explicit_parity_policy(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session, "flexfiat")
    policy = await FxPolicyService.create_policy(
        db_session,
        tenant_id=tenant.id,
        from_asset="USDT",
        from_network="TRON",
        to_currency="USD",
        mode=FxPolicyMode.PARITY,
        rate=Decimal(1),
        max_auto_credit_amount=Decimal(100),
    )
    method = await _method(
        db_session,
        tenant.id,
        auto_credit=True,
        target="SETTLEMENT_WALLET",
        extra_settings={
            "fx_policy_id": str(policy.id),
            "auto_credit_currency": "USD",
            "auto_credit_basis": "GROSS_RECEIVED",
        },
    )
    provider = StubFlexibleProvider(
        created=FlexibleDepositResult("dep-fiat", FlexibleDepositStatus.PENDING),
        reconciled=FlexibleDepositResult(
            "dep-fiat",
            FlexibleDepositStatus.SETTLED_REVIEW,
            asset="USDT",
            network="TRON",
            amount_received=Decimal(40),
            fee_amount=Decimal("0.2"),
        ),
    )
    service = _service(tenant.id, provider)
    deposit = await service.create(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        payment_method_id=method.id,
        idempotency_key="flex-fiat-0001",
    )
    deposit = await service.reconcile(db_session, tenant_id=tenant.id, deposit_id=deposit.id)
    wallet = await db_session.scalar(
        select(Wallet).where(
            Wallet.tenant_id == tenant.id,
            Wallet.user_id == user.id,
            Wallet.currency == "USD",
        )
    )

    assert deposit.status == FlexibleDepositStatus.CREDITED
    assert deposit.credited_currency == "USD"
    assert deposit.credited_amount == Decimal("40.00")
    assert wallet is not None and wallet.balance == Decimal("40.00")


async def test_flexible_provider_asset_outside_method_policy_never_credits(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session, "flexmismatch")
    method = await _method(db_session, tenant.id, auto_credit=True)
    provider = StubFlexibleProvider(
        created=FlexibleDepositResult("dep-bad", FlexibleDepositStatus.PENDING),
        reconciled=FlexibleDepositResult(
            "dep-bad",
            FlexibleDepositStatus.SETTLED_REVIEW,
            asset="USDC",
            network="TRON",
            amount_received=Decimal(10),
        ),
    )
    service = _service(tenant.id, provider)
    deposit = await service.create(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        payment_method_id=method.id,
        idempotency_key="flex-mismatch-1",
    )
    deposit = await service.reconcile(db_session, tenant_id=tenant.id, deposit_id=deposit.id)

    assert deposit.status == FlexibleDepositStatus.UNKNOWN
    assert "outside this deposit policy" in (deposit.last_error or "")
    assert await db_session.scalar(select(AssetWallet).where(AssetWallet.tenant_id == tenant.id)) is None


@pytest.mark.parametrize("target", ["ASSET_WALLET", "SETTLEMENT_WALLET"])
async def test_credited_deposit_reversal_freezes_once_without_debit(db_session, target):
    from sqlalchemy import func

    from packages.payments.exceptions import PaymentError
    from packages.payments.models import FinancialResolutionCase
    from packages.payments.resolution import FinancialResolutionAction, FinancialResolutionService
    tenant, user = await _tenant_user(db_session, "flexreverse")
    extra = {}
    if target == "SETTLEMENT_WALLET":
        policy = await FxPolicyService.create_policy(
            db_session, tenant_id=tenant.id, from_asset="USDT", from_network="TRON",
            to_currency="USD", mode=FxPolicyMode.PARITY, rate=Decimal(1),
            max_auto_credit_amount=Decimal(100),
        )
        extra = {"fx_policy_id": str(policy.id), "auto_credit_currency": "USD"}
    method = await _method(db_session, tenant.id, auto_credit=True, target=target, extra_settings=extra)
    provider = StubFlexibleProvider(
        created=FlexibleDepositResult("reverse-1", FlexibleDepositStatus.PENDING),
        reconciled=FlexibleDepositResult("reverse-1", FlexibleDepositStatus.SETTLED_REVIEW,
                                        asset="USDT", network="TRON", amount_received=Decimal(12)),
    )
    service = _service(tenant.id, provider)
    deposit = await service.create(db_session, tenant_id=tenant.id, user_id=user.id,
                                   payment_method_id=method.id, idempotency_key="flexible-reverse-test")
    await service.reconcile(db_session, tenant_id=tenant.id, deposit_id=deposit.id)
    provider.reconciled = FlexibleDepositResult("reverse-1", FlexibleDepositStatus.REVERSED)
    await service.reconcile_open(db_session)
    await service.reconcile(db_session, tenant_id=tenant.id, deposit_id=deposit.id)
    model = AssetWallet if target == "ASSET_WALLET" else Wallet
    wallet = await db_session.scalar(select(model).where(model.tenant_id == tenant.id))
    assert not wallet.is_active
    assert wallet.balance == Decimal(12)
    assert deposit.credited_amount == Decimal(12)
    assert deposit.status == FlexibleDepositStatus.REVERSED
    assert await db_session.scalar(select(func.count()).select_from(FinancialResolutionCase)) == 1
    case = await db_session.scalar(select(FinancialResolutionCase))
    assert case.severity == "CRITICAL"
    resolution = FinancialResolutionService()
    with pytest.raises(PaymentError, match="no wallet impact"):
        await resolution.resolve_case(db_session, tenant.id, case.id, user.id, case.version,
            FinancialResolutionAction.ACKNOWLEDGE_NO_WALLET_IMPACT, "Reviewed reversal evidence", actor_is_owner=True,
            payment_service=service.payment_service)
    await resolution.resolve_case(db_session, tenant.id, case.id, user.id, case.version,
        FinancialResolutionAction.CLOSE_KEEP_WALLET_FROZEN, "Confirmed reversal; preserve freeze", actor_is_owner=True,
        payment_service=service.payment_service)
    assert not wallet.is_active
