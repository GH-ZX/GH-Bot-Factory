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
from packages.payments.providers.nowpayments import NowPaymentsProvider
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


def _bybit_settings() -> dict[str, object]:
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


async def test_bybit_timeout_after_create_is_unknown_then_recovers_without_second_create(
    db_session: AsyncSession,
) -> None:
    tenant, user = await _tenant_user(db_session, "AmbiguousBybit")
    create_calls = 0
    recovery_calls = 0
    merchant_trade_no = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_calls, recovery_calls, merchant_trade_no
        if request.method == "POST" and request.url.path == "/v5/bybitpay/create_pay":
            create_calls += 1
            merchant_trade_no = json.loads(request.content)["merchantTradeNo"]
            raise httpx.ReadTimeout("response lost after upstream acceptance", request=request)
        if request.method == "GET" and request.url.path == "/v5/bybitpay/pay_result":
            recovery_calls += 1
            assert request.url.params["merchantTradeNo"] == merchant_trade_no
            return httpx.Response(
                200,
                json={
                    "retCode": 100000,
                    "retMsg": "success",
                    "result": {
                        "order": {
                            "merchantId": "305142568",
                            "paymentType": "E_COMMERCE",
                            "merchantTradeNo": merchant_trade_no,
                            "payId": "RECOVERED-PAY-1",
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
    payment_service = PaymentService(registry=registry, secret_storage=EnvSecretStorage({}))
    platform = PaymentPlatformService(payment_service=payment_service)
    db_session.add(
        PaymentProviderConfig(
            tenant_id=tenant.id,
            provider_name="bybit_pay",
            is_enabled=True,
            credentials_ref="OVERRIDE",
            settings_json=_bybit_settings(),
        )
    )
    await db_session.flush()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        registry.register_instance(
            tenant.id,
            "bybit_pay",
            BybitPayProvider(
                settings=_bybit_settings(),
                credentials=json.dumps(
                    {"api_key": "api", "api_secret": "secret", "merchant_id": "305142568"}
                ),
                http_client=client,
                time_fn=lambda: 1_736_233_200.0,
            ),
        )
        method = await platform.create_method(
            db_session,
            tenant_id=tenant.id,
            code="bybit-pay",
            display_name="Bybit Pay",
            method_type=PaymentMethodType.REGULATED_PROVIDER,
            verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
            provider_name="bybit_pay",
            settings={
                "topup_min_amount": "1.00",
                "topup_max_amount": "1000.00",
                "topup_currencies": ["USD"],
            },
        )
        first = await platform.create_topup_intent(
            db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            method_id=method.id,
            amount=Decimal("50.00"),
            currency="USD",
            idempotency_key="ambiguous-bybit-1",
            provider_context={
                "device": "ghbf-device",
                "browser_version": "Mozilla/5.0",
                "ip": "203.0.113.15",
            },
        )
        assert first.status == PaymentIntentStatus.UNKNOWN
        assert first.provider_payment_id is None
        assert first.metadata_json["_provider_creation_ambiguity"]["reason"] == "transport_outcome_unknown"
        assert "_provider_context" not in first.metadata_json

        retry = await platform.create_topup_intent(
            db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            method_id=method.id,
            amount=Decimal("50.00"),
            currency="USD",
            idempotency_key="ambiguous-bybit-1",
            provider_context={
                "device": "different-device",
                "browser_version": "Different browser",
                "ip": "203.0.113.99",
            },
        )
        assert retry.id == first.id
        assert create_calls == 1

        reconciliation = PaymentReconciliationService(payment_service=payment_service)
        reconciled = await reconciliation.reconcile_pending_intents(
            db_session, tenant_id=tenant.id, limit=10
        )

    assert [item.id for item in reconciled] == [first.id]
    assert first.status == PaymentIntentStatus.SUCCEEDED
    assert first.provider_payment_id == "RECOVERED-PAY-1"
    assert "_provider_creation_ambiguity" not in first.metadata_json
    assert first.metadata_json["_provider_creation_recovery"]["provider"] == "bybit_pay"
    assert create_calls == 1
    assert recovery_calls == 1
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


async def test_nonrecoverable_gateway_timeout_never_blindly_recreates(
    db_session: AsyncSession,
) -> None:
    tenant, user = await _tenant_user(db_session, "AmbiguousNow")
    create_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_calls
        create_calls += 1
        raise httpx.ReadTimeout("unknown create outcome", request=request)

    registry = PaymentProviderRegistry()
    payment_service = PaymentService(registry=registry, secret_storage=EnvSecretStorage({}))
    platform = PaymentPlatformService(payment_service=payment_service)
    settings = {
        "pay_currency": "usdttrc20",
        "topup_enabled": True,
        "topup_min_amount": "1.00",
        "topup_max_amount": "1000.00",
        "topup_currencies": ["USD"],
    }
    db_session.add(
        PaymentProviderConfig(
            tenant_id=tenant.id,
            provider_name="nowpayments",
            is_enabled=True,
            credentials_ref="OVERRIDE",
            settings_json=settings,
        )
    )
    await db_session.flush()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        registry.register_instance(
            tenant.id,
            "nowpayments",
            NowPaymentsProvider(settings=settings, api_key="api", webhook_secret=None, http_client=client),
        )
        method = await platform.create_method(
            db_session,
            tenant_id=tenant.id,
            code="nowpayments",
            display_name="NOWPayments",
            method_type=PaymentMethodType.CRYPTO_GATEWAY,
            verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
            provider_name="nowpayments",
            settings={
                "provider_pay_currency": "usdttrc20",
                "topup_min_amount": "1.00",
                "topup_max_amount": "1000.00",
                "topup_currencies": ["USD"],
            },
        )
        intent = await platform.create_topup_intent(
            db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            method_id=method.id,
            amount=Decimal("10.00"),
            currency="USD",
            idempotency_key="ambiguous-now-1",
        )
        assert intent.status == PaymentIntentStatus.UNKNOWN
        assert intent.provider_payment_id is None

        retry = await platform.create_topup_intent(
            db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            method_id=method.id,
            amount=Decimal("10.00"),
            currency="USD",
            idempotency_key="ambiguous-now-1",
        )
        assert retry.id == intent.id
        assert create_calls == 1

        reconciliation = PaymentReconciliationService(payment_service=payment_service)
        rows = await reconciliation.reconcile_pending_intents(db_session, tenant_id=tenant.id, limit=10)

    assert [row.id for row in rows] == [intent.id]
    assert intent.status == PaymentIntentStatus.UNKNOWN
    assert intent.provider_payment_id is None
    assert create_calls == 1
