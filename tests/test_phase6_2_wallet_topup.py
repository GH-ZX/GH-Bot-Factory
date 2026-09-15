import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from apps.api.v1.storefront import get_storefront_payment_service
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.payments.exceptions import PaymentIntegrityError
from packages.payments.models import (
    LedgerTransaction,
    PaymentIntentPurpose,
    PaymentProviderConfig,
    Wallet,
)
from packages.payments.payment_service import PaymentService
from packages.payments.providers.mock import MockPaymentProvider
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.payments.service import CANONICAL_SETTLEMENT_TYPE
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio

TEST_JWT_SECRET = "phase6-2-test-jwt-secret-0123456789abcdef-0123456789abcdef"


async def create_customer(session: AsyncSession, tenant: Tenant) -> tuple[User, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"topup_{uuid.uuid4().hex[:8]}",
        first_name="Wallet",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    session.add(
        Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=Role.CUSTOMER,
            permissions=[],
            is_active=True,
        )
    )
    await session.flush()
    token = AuthTokenService(secret_key=TEST_JWT_SECRET).issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.CUSTOMER],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )
    return user, token


async def configure_mock_provider(
    session: AsyncSession,
    secret_storage: EnvSecretStorage,
    tenant: Tenant,
) -> None:
    credentials_ref = f"TOPUP_CREDS_{tenant.id}"
    webhook_ref = f"TOPUP_WEBHOOK_{tenant.id}"
    await secret_storage.set_secret(credentials_ref, "mock-key")
    await secret_storage.set_secret(webhook_ref, "mock-webhook")
    session.add(
        PaymentProviderConfig(
            tenant_id=tenant.id,
            provider_name="mock",
            is_enabled=True,
            credentials_ref=credentials_ref,
            webhook_secret_ref=webhook_ref,
            settings_json={
                "provider_name": "mock",
                "display_name": "Mock Pay",
                "topup_enabled": True,
                "topup_min_amount": "5.00",
                "topup_max_amount": "500.00",
                "topup_currencies": ["USD", "EUR"],
            },
        )
    )
    await session.flush()


@pytest_asyncio.fixture
async def topup_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()
    provider = MockPaymentProvider(default_create_status=PaymentIntentStatus.PENDING)
    payment_service = PaymentService(registry=registry, secret_storage=secret_storage)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    app.dependency_overrides[get_storefront_payment_service] = lambda: payment_service

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "session": db_session,
            "secret_storage": secret_storage,
            "registry": registry,
            "provider": provider,
            "payment_service": payment_service,
        }

    app.dependency_overrides.clear()


async def prepare_tenant(env: dict[str, Any], name: str = "Top-up Store") -> tuple[Tenant, User, str]:
    session: AsyncSession = env["session"]
    tenant = Tenant(name=name, slug=f"topup-{uuid.uuid4().hex[:8]}", is_active=True)
    session.add(tenant)
    await session.flush()
    user, token = await create_customer(session, tenant)
    await configure_mock_provider(session, env["secret_storage"], tenant)
    env["registry"].register_instance(tenant.id, "mock", env["provider"])
    return tenant, user, token


async def test_topup_options_are_tenant_scoped_and_public(topup_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = topup_env["client"]
    _, _, token = await prepare_tenant(topup_env)

    response = await client.get(
        "/api/v1/storefront/wallet/topups/options",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "providers": [
            {
                "provider_name": "mock",
                "display_name": "Mock Pay",
                "min_amount": "5.00",
                "max_amount": "500.00",
                "currencies": ["EUR", "USD"],
                "checkout_mode": "external",
                "whole_units_only": False,
                "terms_required": False,
                "terms_url": None,
            }
        ]
    }


async def test_topup_create_reconcile_and_settle_exactly_once(topup_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = topup_env["client"]
    session: AsyncSession = topup_env["session"]
    provider: MockPaymentProvider = topup_env["provider"]
    tenant, user, token = await prepare_tenant(topup_env)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "amount": "25.00",
        "currency": "USD",
        "provider_name": "mock",
        "idempotency_key": "topup-create-0001",
    }

    created = await client.post(
        "/api/v1/storefront/wallet/topups",
        json=payload,
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["purpose"] == "WALLET_TOPUP"
    assert body["status"] == "PENDING"
    assert body["amount"] == "25.00"
    assert body["wallet_balance"] == "0.00"
    assert body["checkout_url"].startswith("https://mock-pay.example.com/checkout/")

    intent_id = body["id"]
    provider_payment_id = f"mock_pay_{payload['idempotency_key']}"
    provider.payments[provider_payment_id]["status"] = PaymentIntentStatus.SUCCEEDED

    first = await client.post(
        f"/api/v1/storefront/wallet/topups/{intent_id}/reconcile",
        headers=headers,
    )
    second = await client.post(
        f"/api/v1/storefront/wallet/topups/{intent_id}/reconcile",
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "SUCCEEDED"
    assert first.json()["wallet_balance"] == "25.00"
    assert second.json()["wallet_balance"] == "25.00"

    wallet = (
        await session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "USD",
            )
        )
    ).scalar_one()
    assert wallet.balance == Decimal("25.00")

    ledger_rows = list(
        (
            await session.execute(
                select(LedgerTransaction).where(
                    LedgerTransaction.wallet_id == wallet.id,
                    LedgerTransaction.reference_type == CANONICAL_SETTLEMENT_TYPE,
                    LedgerTransaction.reference_id == intent_id,
                )
            )
        ).scalars().all()
    )
    assert len(ledger_rows) == 1


async def test_topup_idempotency_key_is_bound_to_request(topup_env: dict[str, Any]) -> None:
    payment_service: PaymentService = topup_env["payment_service"]
    session: AsyncSession = topup_env["session"]
    tenant, user, _ = await prepare_tenant(topup_env)

    first = await payment_service.create_wallet_topup_intent(
        session=session,
        tenant_id=tenant.id,
        user_id=user.id,
        amount=Decimal("15.00"),
        currency="USD",
        provider_name="mock",
        idempotency_key="topup-idempotency-1",
    )
    same = await payment_service.create_wallet_topup_intent(
        session=session,
        tenant_id=tenant.id,
        user_id=user.id,
        amount=Decimal("15.00"),
        currency="USD",
        provider_name="mock",
        idempotency_key="topup-idempotency-1",
    )
    assert same.id == first.id
    assert first.order_id is None
    assert first.purpose == PaymentIntentPurpose.WALLET_TOPUP

    with pytest.raises(PaymentIntegrityError):
        await payment_service.create_wallet_topup_intent(
            session=session,
            tenant_id=tenant.id,
            user_id=user.id,
            amount=Decimal("16.00"),
            currency="USD",
            provider_name="mock",
            idempotency_key="topup-idempotency-1",
        )


async def test_topup_policy_rejects_out_of_range_and_currency(topup_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = topup_env["client"]
    _, _, token = await prepare_tenant(topup_env)
    headers = {"Authorization": f"Bearer {token}"}

    too_small = await client.post(
        "/api/v1/storefront/wallet/topups",
        json={
            "amount": "1.00",
            "currency": "USD",
            "provider_name": "mock",
            "idempotency_key": "topup-small-0001",
        },
        headers=headers,
    )
    bad_currency = await client.post(
        "/api/v1/storefront/wallet/topups",
        json={
            "amount": "10.00",
            "currency": "GBP",
            "provider_name": "mock",
            "idempotency_key": "topup-gbp-00001",
        },
        headers=headers,
    )

    assert too_small.status_code == 400
    assert bad_currency.status_code == 400


async def test_customer_cannot_read_another_customers_topup(topup_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = topup_env["client"]
    session: AsyncSession = topup_env["session"]
    tenant, _, owner_token = await prepare_tenant(topup_env)
    _, other_token = await create_customer(session, tenant)

    created = await client.post(
        "/api/v1/storefront/wallet/topups",
        json={
            "amount": "20.00",
            "currency": "USD",
            "provider_name": "mock",
            "idempotency_key": "topup-private-001",
        },
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert created.status_code == 201

    denied = await client.get(
        f"/api/v1/storefront/wallet/topups/{created.json()['id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert denied.status_code == 404


async def test_immediate_success_provider_settles_topup_before_response(
    topup_env: dict[str, Any],
) -> None:
    session: AsyncSession = topup_env["session"]
    tenant, user, _ = await prepare_tenant(topup_env, name="Immediate Store")
    immediate_provider = MockPaymentProvider(default_create_status=PaymentIntentStatus.SUCCEEDED)
    topup_env["registry"].register_instance(tenant.id, "mock", immediate_provider)

    intent = await topup_env["payment_service"].create_wallet_topup_intent(
        session=session,
        tenant_id=tenant.id,
        user_id=user.id,
        amount=Decimal("35.00"),
        currency="USD",
        provider_name="mock",
        idempotency_key="topup-immediate-001",
    )

    assert intent.status == PaymentIntentStatus.SUCCEEDED
    wallet = (
        await session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "USD",
            )
        )
    ).scalar_one()
    assert wallet.balance == Decimal("35.00")


async def test_verified_webhook_settles_wallet_topup_once(topup_env: dict[str, Any]) -> None:
    session: AsyncSession = topup_env["session"]
    provider: MockPaymentProvider = topup_env["provider"]
    payment_service: PaymentService = topup_env["payment_service"]
    secret_storage: EnvSecretStorage = topup_env["secret_storage"]
    tenant, user, _ = await prepare_tenant(topup_env, name="Webhook Store")

    intent = await payment_service.create_wallet_topup_intent(
        session=session,
        tenant_id=tenant.id,
        user_id=user.id,
        amount=Decimal("45.00"),
        currency="USD",
        provider_name="mock",
        idempotency_key="topup-webhook-001",
    )
    provider.payments[intent.provider_payment_id]["status"] = PaymentIntentStatus.SUCCEEDED

    webhook_secret = await secret_storage.get_secret(f"TOPUP_WEBHOOK_{tenant.id}")
    payload = {
        "event_id": "evt_topup_001",
        "event_type": "payment.succeeded",
        "provider_payment_id": intent.provider_payment_id,
        "amount": "45.00",
        "currency": "USD",
        "status": "SUCCEEDED",
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    signature = hmac.new(
        webhook_secret.encode("utf-8"), payload_bytes, hashlib.sha256
    ).hexdigest()

    first = await payment_service.process_webhook(
        session=session,
        tenant_id=tenant.id,
        provider_name="mock",
        payload_bytes=payload_bytes,
        headers={"X-Signature": signature},
    )
    second = await payment_service.process_webhook(
        session=session,
        tenant_id=tenant.id,
        provider_name="mock",
        payload_bytes=payload_bytes,
        headers={"X-Signature": signature},
    )

    assert first.id == second.id
    wallet = (
        await session.execute(
            select(Wallet).where(
                Wallet.tenant_id == tenant.id,
                Wallet.user_id == user.id,
                Wallet.currency == "USD",
            )
        )
    ).scalar_one()
    assert wallet.balance == Decimal("45.00")
