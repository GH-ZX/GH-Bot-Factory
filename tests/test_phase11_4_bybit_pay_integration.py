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
from packages.payments.providers.bybit_pay import BybitPayProvider
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.payments.reconciliation import PaymentReconciliationService
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


def _settings() -> dict[str, object]:
    return {
        "sandbox": True,
        "timeout_seconds": 5,
        "recv_window_ms": 5000,
        "webhook_tolerance_seconds": 300,
        "success_url": "https://merchant.example/success",
        "failed_url": "https://merchant.example/failed",
        "webhook_url": "https://merchant.example/webhooks/bybit",
        "shopping_name": "GHBF Store",
        "goods_name": "Wallet top-up",
        "goods_detail": "Digital wallet balance",
        "mcc_code": "5816",
        "terminal_type": "WEB",
        "currency_types": {"USD": "fiat"},
        "topup_enabled": True,
        "topup_min_amount": "1.00",
        "topup_max_amount": "1000.00",
        "topup_currencies": ["USD"],
    }


async def _tenant_user(session: AsyncSession) -> tuple[Tenant, User]:
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(name="Bybit Tenant", slug=f"bybit-{suffix}", is_active=True)
    user = User(username=f"bybit_user_{suffix}", is_active=True)
    session.add_all([tenant, user])
    await session.flush()
    return tenant, user


async def test_bybit_polling_converges_to_exactly_once_wallet_credit(
    db_session: AsyncSession,
) -> None:
    tenant, user = await _tenant_user(db_session)
    lookup_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lookup_calls
        if request.method == "POST" and request.url.path == "/v5/bybitpay/create_pay":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "retCode": 100000,
                    "retMsg": "success",
                    "result": {
                        "payId": "BYBIT-PAY-1",
                        "terminalType": "WEB",
                        "expireTime": 1_736_236_800,
                        "checkoutLink": "bybitapp://payment/BYBIT-PAY-1",
                        "qrContent": "data:image/png;base64,AAAA",
                        "order": {
                            "merchantId": "305142568",
                            "paymentType": "E_COMMERCE",
                            "merchantTradeNo": body["merchantTradeNo"],
                            "payId": "BYBIT-PAY-1",
                            "status": "INIT",
                            "amount": "50.00",
                            "currency": "USD",
                            "currencyType": "fiat",
                        },
                    },
                },
            )
        if request.method == "GET" and request.url.path == "/v5/bybitpay/pay_result":
            lookup_calls += 1
            return httpx.Response(
                200,
                json={
                    "retCode": 100000,
                    "retMsg": "success",
                    "result": {
                        "order": {
                            "merchantId": "305142568",
                            "paymentType": "E_COMMERCE",
                            "merchantTradeNo": intent_id,
                            "payId": "BYBIT-PAY-1",
                            "status": "PAY_SUCCESS",
                            "amount": "50.00",
                            "currency": "USD",
                            "currencyType": "fiat",
                            "finishTime": 1_736_233_260,
                        }
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
            provider_name="bybit_pay",
            is_enabled=True,
            credentials_ref="IGNORED_OVERRIDE",
            webhook_secret_ref="IGNORED_OVERRIDE_WEBHOOK",
            settings_json=_settings(),
        )
    )
    await db_session.flush()

    intent_id = ""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        provider = BybitPayProvider(
            settings=_settings(),
            credentials=json.dumps(
                {
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "merchant_id": "305142568",
                }
            ),
            http_client=client,
            time_fn=lambda: 1_736_233_200.0,
        )
        registry.register_instance(tenant.id, "bybit_pay", provider)
        method = await platform.create_method(
            db_session,
            tenant_id=tenant.id,
            code="bybit-regulated",
            display_name="Bybit Pay",
            method_type=PaymentMethodType.REGULATED_PROVIDER,
            verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
            provider_name="bybit_pay",
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
            idempotency_key="bybit-poll-1",
            provider_context={
                "device": "ghbf-device",
                "browser_version": "Mozilla/5.0",
                "ip": "203.0.113.11",
            },
        )
        intent_id = str(intent.id)
        assert intent.status == PaymentIntentStatus.PENDING
        assert intent.checkout_url is None
        assert intent.metadata_json["provider_create"]["qr_content"].startswith("data:image/png")
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
