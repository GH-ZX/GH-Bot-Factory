from __future__ import annotations

import hashlib
import hmac
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
from packages.payments.providers.nowpayments import NowPaymentsProvider
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.payments.reconciliation import PaymentReconciliationService
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


async def _tenant_user(session: AsyncSession, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(name=f"NP {suffix}", slug=f"np-{suffix}-{uuid.uuid4().hex[:6]}", is_active=True)
    user = User(username=f"np_{suffix}_{uuid.uuid4().hex[:8]}", is_active=True)
    session.add_all([tenant, user])
    await session.flush()
    return tenant, user


async def _setup(
    session: AsyncSession,
    tenant: Tenant,
    client: httpx.AsyncClient,
) -> tuple[PaymentPlatformService, PaymentService, object]:
    secrets = EnvSecretStorage(
        {
            "NP_API_KEY": "test-api-key",
            "NP_IPN_SECRET": "test-ipn-secret",
        }
    )
    registry = PaymentProviderRegistry()
    adapter = NowPaymentsProvider(
        settings={"pay_currencies": ["usdttrc20", "usdtbsc"], "default_pay_currency": "usdttrc20"},
        api_key="test-api-key",
        webhook_secret="test-ipn-secret",
        http_client=client,
    )
    registry.register_instance(tenant.id, "nowpayments", adapter)
    payment_service = PaymentService(registry=registry, secret_storage=secrets)
    platform = PaymentPlatformService(payment_service=payment_service)
    session.add(
        PaymentProviderConfig(
            tenant_id=tenant.id,
            provider_name="nowpayments",
            is_enabled=True,
            credentials_ref="NP_API_KEY",
            webhook_secret_ref="NP_IPN_SECRET",
            settings_json={
                "topup_enabled": True,
                "topup_min_amount": "5.00",
                "topup_max_amount": "1000.00",
                "topup_currencies": ["USD"],
                "pay_currencies": ["usdttrc20", "usdtbsc"],
                "default_pay_currency": "usdttrc20",
            },
        )
    )
    method = await platform.create_method(
        session,
        tenant_id=tenant.id,
        code="nowpayments-usdt-trc20",
        display_name="USDT TRC20 via NOWPayments",
        method_type=PaymentMethodType.CRYPTO_GATEWAY,
        verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
        provider_name="nowpayments",
        asset="USDT",
        network="TRC20",
        settings={
            "provider_pay_currency": "usdttrc20",
            "topup_min_amount": "5.00",
            "topup_max_amount": "1000.00",
            "topup_currencies": ["USD"],
        },
    )
    await session.flush()
    return platform, payment_service, method


async def test_nowpayments_polling_converges_to_exactly_once_wallet_credit(
    db_session: AsyncSession,
) -> None:
    tenant, user = await _tenant_user(db_session, "poll")
    lookup_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lookup_count
        if request.method == "POST" and request.url.path == "/v1/payment":
            body = json.loads(request.content)
            assert body["pay_currency"] == "usdttrc20"
            return httpx.Response(
                200,
                json={
                    "payment_id": 7001,
                    "payment_status": "waiting",
                    "pay_address": "TNPTESTADDRESS",
                    "price_amount": 40,
                    "price_currency": "usd",
                    "pay_amount": "40.25",
                    "actually_paid": "0",
                    "pay_currency": "usdttrc20",
                },
            )
        if request.method == "GET" and request.url.path == "/v1/payment/7001":
            lookup_count += 1
            return httpx.Response(
                200,
                json={
                    "payment_id": 7001,
                    "payment_status": "finished",
                    "pay_address": "TNPTESTADDRESS",
                    "price_amount": 40,
                    "price_currency": "usd",
                    "pay_amount": "40.25",
                    "actually_paid": "40.25",
                    "pay_currency": "usdttrc20",
                },
            )
        raise AssertionError(f"Unexpected NOWPayments request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        platform, payment_service, method = await _setup(db_session, tenant, client)
        intent = await platform.create_topup_intent(
            db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            method_id=method.id,
            amount=Decimal("40.00"),
            currency="USD",
            idempotency_key="nowpayments-poll-0001",
        )
        assert intent.status == PaymentIntentStatus.PENDING
        assert intent.metadata_json["provider_create"]["pay_address"] == "TNPTESTADDRESS"
        assert intent.metadata_json["_provider_verification"] == {"pay_currency": "usdttrc20"}

        reconciliation = PaymentReconciliationService(payment_service=payment_service)
        first = await reconciliation.reconcile_intent(db_session, tenant.id, intent.id)
        second = await reconciliation.reconcile_intent(db_session, tenant.id, intent.id)

    assert first.status == PaymentIntentStatus.SUCCEEDED
    assert second.status == PaymentIntentStatus.SUCCEEDED
    assert lookup_count == 1  # terminal local intent is not queried a second time
    wallet = (
        await db_session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "USD",
            )
        )
    ).scalar_one()
    assert wallet.balance == Decimal("40.00")


async def test_nowpayments_signed_ipn_converges_to_same_ledger_gate(db_session: AsyncSession) -> None:
    tenant, user = await _tenant_user(db_session, "ipn")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/payment":
            return httpx.Response(
                200,
                json={
                    "payment_id": 8001,
                    "payment_status": "waiting",
                    "pay_address": "TNPIPNADDRESS",
                    "price_amount": 15,
                    "price_currency": "usd",
                    "pay_amount": "15.1",
                    "actually_paid": "0",
                    "pay_currency": "usdttrc20",
                },
            )
        raise AssertionError(f"Unexpected request {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        platform, payment_service, method = await _setup(db_session, tenant, client)
        intent = await platform.create_topup_intent(
            db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            method_id=method.id,
            amount=Decimal("15.00"),
            currency="USD",
            idempotency_key="nowpayments-ipn-0001",
        )

        payload_obj = {
            "payment_id": 8001,
            "payment_status": "finished",
            "price_amount": 15,
            "price_currency": "usd",
            "pay_amount": "15.1",
            "actually_paid": "15.1",
            "pay_currency": "usdttrc20",
        }
        canonical = NowPaymentsProvider._canonical_signature_payload(payload_obj)
        signature = hmac.new(b"test-ipn-secret", canonical, hashlib.sha512).hexdigest()
        raw = json.dumps(payload_obj, separators=(",", ":")).encode()
        first = await payment_service.process_webhook(
            db_session,
            tenant_id=tenant.id,
            provider_name="nowpayments",
            payload_bytes=raw,
            headers={"x-nowpayments-sig": signature},
        )
        second = await payment_service.process_webhook(
            db_session,
            tenant_id=tenant.id,
            provider_name="nowpayments",
            payload_bytes=raw,
            headers={"x-nowpayments-sig": signature},
        )

    assert first.id == second.id
    assert first.processed is True
    assert intent.status == PaymentIntentStatus.SUCCEEDED
    wallet = (
        await db_session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "USD",
            )
        )
    ).scalar_one()
    assert wallet.balance == Decimal("15.00")
