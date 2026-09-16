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
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.payments.providers.triplea import TripleAPaymentProvider
from packages.payments.reconciliation import PaymentReconciliationService
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


async def _tenant_user(session: AsyncSession) -> tuple[Tenant, User]:
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(name="TripleA Tenant", slug=f"triplea-{suffix}", is_active=True)
    user = User(username=f"triplea_user_{suffix}", is_active=True)
    session.add_all([tenant, user])
    await session.flush()
    return tenant, user


async def test_triplea_polling_converges_to_exactly_once_wallet_credit(
    db_session: AsyncSession,
) -> None:
    tenant, user = await _tenant_user(db_session)
    lookup_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lookup_calls
        if request.url.path == "/api/v2/oauth/token":
            return httpx.Response(200, json={"access_token": "oauth-token", "expires_in": 3600})
        if request.method == "POST" and request.url.path == "/api/v2/payment":
            body = json.loads(request.content)
            assert body["merchant_key"] == "mkey-test"
            return httpx.Response(
                200,
                json={
                    "payment_reference": "T3A-PAY-1001",
                    "order_currency": "USD",
                    "order_amount": "50",
                    "hosted_url": "https://triple-a.io/pay/T3A-PAY-1001",
                },
            )
        if request.method == "GET" and request.url.path == "/api/v2/payment/T3A-PAY-1001":
            lookup_calls += 1
            return httpx.Response(
                200,
                json={
                    "payment_reference": "T3A-PAY-1001",
                    "order_currency": "USD",
                    "order_amount": "50",
                    "status": "good",
                    "receive_amount": "50.00",
                },
            )
        raise AssertionError(f"Unexpected Triple-A request: {request.method} {request.url}")

    secrets = EnvSecretStorage({"T3A_CREDS": json.dumps({
        "client_id": "client-id",
        "client_secret": "client-secret",
        "merchant_key": "mkey-test",
    })})
    registry = PaymentProviderRegistry()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TripleAPaymentProvider(
            settings={"sandbox": True},
            credentials=await secrets.get_secret("T3A_CREDS"),
            http_client=client,
        )
        registry.register_instance(tenant.id, "triplea", adapter)
        payment_service = PaymentService(registry=registry, secret_storage=secrets)
        platform = PaymentPlatformService(payment_service=payment_service)
        db_session.add(
            PaymentProviderConfig(
                tenant_id=tenant.id,
                provider_name="triplea",
                is_enabled=True,
                credentials_ref="T3A_CREDS",
                webhook_secret_ref=None,
                settings_json={
                    "topup_enabled": True,
                    "topup_min_amount": "5.00",
                    "topup_max_amount": "1000.00",
                    "topup_currencies": ["USD"],
                    "sandbox": True,
                },
            )
        )
        method = await platform.create_method(
            db_session,
            tenant_id=tenant.id,
            code="triplea-regulated",
            display_name="Stablecoin payment via Triple-A",
            method_type=PaymentMethodType.REGULATED_PROVIDER,
            verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
            provider_name="triplea",
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
            idempotency_key="triplea-poll-1",
        )
        assert intent.status == PaymentIntentStatus.PENDING
        assert intent.checkout_url == "https://triple-a.io/pay/T3A-PAY-1001"
        assert intent.metadata_json["_provider_verification"] == {
            "order_currency": "usd",
            "order_amount": "50",
        }

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
