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
from packages.payments.providers.gozapay import GoZaPayProvider
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.payments.reconciliation import PaymentReconciliationService
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


async def test_gozapay_polling_converges_after_clearing_to_exactly_once_credit(
    db_session: AsyncSession,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(name="GoZa Tenant", slug=f"goza-{suffix}", is_active=True)
    user = User(username=f"goza_user_{suffix}", is_active=True)
    db_session.add_all([tenant, user])
    await db_session.flush()
    lookup_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lookup_count
        if request.method == "POST" and request.url.path == "/api/v1/invoices":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "id": "goza-inv-1001",
                        "order_id": body["order_id"],
                        "chain": "tron",
                        "coin": "USDT",
                        "expected_amount": "30.00",
                        "unique_amount": "30.00",
                        "amount_received": "0",
                        "status": "pending",
                        "payment_url": "https://gozapay.com/pay/goza-inv-1001",
                        "flexible": False,
                        "settled": False,
                    },
                },
            )
        if request.method == "GET" and request.url.path == "/api/v1/invoices/goza-inv-1001":
            lookup_count += 1
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "id": "goza-inv-1001",
                        "order_id": str(intent.id),
                        "chain": "tron",
                        "coin": "USDT",
                        "expected_amount": "30.00",
                        "unique_amount": "30.00",
                        "amount_received": "30.00",
                        "status": "settled",
                        "tx_hash": "abc123",
                        "flexible": False,
                        "settled": True,
                    },
                },
            )
        raise AssertionError(f"Unexpected GoZaPay request: {request.method} {request.url}")

    secrets = EnvSecretStorage({"GOZA_KEY": "api-key", "GOZA_WEBHOOK": "wh-secret"})
    registry = PaymentProviderRegistry()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = GoZaPayProvider(
            settings={
                "chain": "tron",
                "coin": "USDT",
                "experimental_risk_acknowledged": True,
                "nominal_stablecoin_parity_acknowledged": True,
            },
            api_key="api-key",
            webhook_secret="wh-secret",
            http_client=client,
        )
        registry.register_instance(tenant.id, "gozapay", adapter)
        payment_service = PaymentService(registry=registry, secret_storage=secrets)
        platform = PaymentPlatformService(payment_service=payment_service)
        db_session.add(
            PaymentProviderConfig(
                tenant_id=tenant.id,
                provider_name="gozapay",
                is_enabled=True,
                credentials_ref="GOZA_KEY",
                webhook_secret_ref="GOZA_WEBHOOK",
                settings_json={
                    "topup_enabled": True,
                    "topup_min_amount": "5.00",
                    "topup_max_amount": "1000.00",
                    "topup_currencies": ["USD"],
                    "chain": "tron",
                    "coin": "USDT",
                    "experimental_risk_acknowledged": True,
                    "nominal_stablecoin_parity_acknowledged": True,
                },
            )
        )
        method = await platform.create_method(
            db_session,
            tenant_id=tenant.id,
            code="gozapay-usdt-tron",
            display_name="GoZaPay USDT TRON",
            method_type=PaymentMethodType.CRYPTO_GATEWAY,
            verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
            provider_name="gozapay",
            asset="USDT",
            network="TRON",
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
            amount=Decimal("30.00"),
            currency="USD",
            idempotency_key="goza-poll-0001",
        )
        assert intent.status == PaymentIntentStatus.PENDING
        assert intent.checkout_url == "https://gozapay.com/pay/goza-inv-1001"

        reconciliation = PaymentReconciliationService(payment_service=payment_service)
        first = await reconciliation.reconcile_intent(db_session, tenant.id, intent.id)
        second = await reconciliation.reconcile_intent(db_session, tenant.id, intent.id)

    assert first.status == PaymentIntentStatus.SUCCEEDED
    assert second.status == PaymentIntentStatus.SUCCEEDED
    assert lookup_count == 1
    wallet = (
        await db_session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "USD",
            )
        )
    ).scalar_one()
    assert wallet.balance == Decimal("30.00")
