from __future__ import annotations

import json
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.payments.models import (
    PaymentMethodType,
    PaymentProviderConfig,
    PaymentVerificationMode,
    Wallet,
)
from packages.payments.payment_service import PaymentService
from packages.payments.platform import PaymentPlatformService
from packages.payments.providers.binance_pay import BinancePayProvider
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.payments.reconciliation import PaymentReconciliationService
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


async def _tenant_user(session: AsyncSession) -> tuple[Tenant, User]:
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(name="Binance Tenant", slug=f"binance-{suffix}", is_active=True)
    user = User(username=f"binance_user_{suffix}", is_active=True)
    session.add_all([tenant, user])
    await session.flush()
    return tenant, user


def _settings() -> dict[str, object]:
    return {
        "topup_enabled": True,
        "topup_min_amount": "1.00",
        "topup_max_amount": "1000.00",
        "topup_currencies": ["USD"],
        "timeout_seconds": 5,
        "terminal_type": "WEB",
        "goods_type": "02",
        "goods_category": "6000",
        "goods_name": "Wallet top up",
        "goods_detail": "Digital wallet balance",
        "description": "Digital wallet balance top up",
        "order_expire_seconds": 3600,
        "support_pay_currencies": ["USDT", "USDC"],
    }


async def test_binance_polling_converges_to_exactly_once_wallet_credit(
    db_session: AsyncSession,
) -> None:
    tenant, user = await _tenant_user(db_session)
    lookup_calls = 0
    created_trade_no = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lookup_calls, created_trade_no
        body = json.loads(request.content)
        if request.url.path == "/binancepay/openapi/v3/order":
            created_trade_no = body["merchantTradeNo"]
            return httpx.Response(
                200,
                json={
                    "status": "SUCCESS",
                    "code": "000000",
                    "data": {
                        "prepayId": "2938393749303836729",
                        "terminalType": "WEB",
                        "expireTime": 1_736_236_800_000,
                        "qrcodeLink": "https://pay.binance.com/qr/abc",
                        "qrContent": "https://pay.binance.com/qr-content/abc",
                        "checkoutUrl": "https://pay.binance.com/checkout/abc",
                        "universalUrl": "https://app.binance.com/payment/abc",
                        "currency": "USD",
                        "totalFee": "50.00",
                    },
                },
            )
        if request.url.path == "/binancepay/openapi/order/query":
            lookup_calls += 1
            return httpx.Response(
                200,
                json={
                    "status": "SUCCESS",
                    "code": "000000",
                    "data": {
                        "merchantId": 123,
                        "prepayId": "2938393749303836729",
                        "transactionId": "23729202729220282",
                        "merchantTradeNo": created_trade_no,
                        "tradeType": "WEB",
                        "status": "PAID",
                        "currency": "USD",
                        "totalFee": "50.00",
                        "createTime": 1_736_233_200_000,
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    registry = PaymentProviderRegistry()
    secret_storage = EnvSecretStorage({})
    payment_service = PaymentService(registry=registry, secret_storage=secret_storage)
    platform = PaymentPlatformService(payment_service=payment_service)
    db_session.add(
        PaymentProviderConfig(
            tenant_id=tenant.id,
            provider_name="binance_pay",
            is_enabled=True,
            credentials_ref="IGNORED_OVERRIDE",
            settings_json=_settings(),
        )
    )
    await db_session.flush()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        provider = BinancePayProvider(
            settings=_settings(),
            credentials=json.dumps({"api_key": "api-key", "api_secret": "api-secret"}),
            http_client=client,
            time_fn=lambda: 1_736_233_200.0,
            nonce_fn=lambda: "AbCdEfGhIjKlMnOpQrStUvWxYz012345",
        )
        registry.register_instance(tenant.id, "binance_pay", provider)
        method = await platform.create_method(
            db_session,
            tenant_id=tenant.id,
            code="binance-pay",
            display_name="Binance Pay",
            method_type=PaymentMethodType.REGULATED_PROVIDER,
            verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
            provider_name="binance_pay",
            settings={
                "topup_min_amount": "5.00",
                "topup_max_amount": "1000.00",
                "topup_currencies": ["USD"],
            },
        )
        intent = await platform.create_topup_intent(
            db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            method_id=method.id,
            amount=Decimal("50.00"),
            currency="USD",
            idempotency_key="binance-poll-1",
            provider_context={
                "device": "ghbf-device",
                "browser_version": "Mozilla/5.0",
                "ip": "203.0.113.12",
            },
        )
        assert intent.status == PaymentIntentStatus.PENDING
        assert intent.checkout_url == "https://pay.binance.com/checkout/abc"
        assert "_provider_context" not in intent.metadata_json

        reconciliation = PaymentReconciliationService(payment_service=payment_service)
        first = await reconciliation.reconcile_intent(db_session, tenant.id, intent.id)
        second = await reconciliation.reconcile_intent(db_session, tenant.id, intent.id)

    assert first.status == PaymentIntentStatus.SUCCEEDED
    assert second.status == PaymentIntentStatus.SUCCEEDED
    assert lookup_calls == 1
    wallet = (
        await db_session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "USD",
            )
        )
    ).scalar_one()
    assert wallet.balance == Decimal("50.00")
