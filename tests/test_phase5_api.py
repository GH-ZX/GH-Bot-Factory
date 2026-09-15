import hashlib
import hmac
import json
import time
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from apps.api.v1.auth import get_secret_storage
from apps.api.v1.payments import (
    get_db_session,
    get_payment_service,
    get_reconciliation_service,
)
from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStatus
from packages.payments.models import (
    PaymentProviderConfig,
)
from packages.payments.payment_service import PaymentService
from packages.payments.providers.mock import MockPaymentProvider
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.payments.reconciliation import PaymentReconciliationService
from packages.payments.service import LedgerService
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.models import Bot
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Tenant, User

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers & Factories
# ---------------------------------------------------------------------------

async def create_tenant(session: AsyncSession, name: str = "Test Tenant", is_active: bool = True) -> Tenant:
    tenant = Tenant(
        name=name,
        slug=f"tenant-{uuid.uuid4().hex[:8]}",
        is_active=is_active,
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def create_user(session: AsyncSession, tenant_id: uuid.UUID, telegram_id: int | None = None) -> User:
    user = User(
        telegram_id=telegram_id or int(time.time() * 1000) % 1_000_000_000,
        username=f"user_{uuid.uuid4().hex[:6]}",
        first_name="Alice",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def create_order(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    total_amount: Decimal = Decimal("100.00"),
    currency: str = "USD",
    status: OrderStatus = OrderStatus.PENDING,
) -> Order:
    order = Order(
        tenant_id=tenant_id,
        user_id=user_id,
        order_number=f"ORD-{uuid.uuid4().hex[:8].upper()}",
        status=status,
        total_amount=total_amount,
        currency=currency,
    )
    session.add(order)
    await session.flush()
    return order


async def setup_provider_config(
    session: AsyncSession,
    secret_storage: EnvSecretStorage,
    tenant_id: uuid.UUID,
    provider_name: str = "mock",
    credentials: str = "test_api_key_123",
    webhook_secret: str = "test_webhook_secret_xyz",
    is_enabled: bool = True,
) -> PaymentProviderConfig:
    creds_ref = f"PAY_CREDS_{tenant_id}_{provider_name}"
    wh_ref = f"PAY_WH_{tenant_id}_{provider_name}"
    await secret_storage.set_secret(creds_ref, credentials)
    await secret_storage.set_secret(wh_ref, webhook_secret)

    config = PaymentProviderConfig(
        tenant_id=tenant_id,
        provider_name=provider_name.lower(),
        is_enabled=is_enabled,
        credentials_ref=creds_ref,
        webhook_secret_ref=wh_ref,
        settings_json={"provider_name": provider_name},
    )
    session.add(config)
    await session.flush()
    return config


def make_telegram_init_data(
    bot_token: str,
    user_dict: dict[str, Any],
    auth_date: int | None = None,
) -> str:
    if auth_date is None:
        auth_date = int(time.time())
    user_str = json.dumps(user_dict, separators=(",", ":"))
    params = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": user_str,
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = hash_val
    return urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# API Test Fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def api_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    """Provides isolated FastAPI test client wired to in-memory SQLite and shared services."""
    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()
    mock_provider = MockPaymentProvider(default_create_status=PaymentIntentStatus.PENDING)
    payment_service = PaymentService(registry=registry, secret_storage=secret_storage)
    reconcile_service = PaymentReconciliationService(payment_service=payment_service)

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_secret_storage] = lambda: secret_storage
    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_reconciliation_service] = lambda: reconcile_service

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "session": db_session,
            "secret_storage": secret_storage,
            "registry": registry,
            "mock_provider": mock_provider,
            "payment_service": payment_service,
            "reconcile_service": reconcile_service,
        }

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. POST /api/v1/payments/intents: Server-Authoritative Amount & Lifecycle
# ---------------------------------------------------------------------------

async def test_post_payment_intent_server_authoritative_amount(api_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    user = await create_user(session, tenant.id)
    # The order has an authoritative amount of 149.99 USD
    order = await create_order(
        session, tenant.id, user.id, total_amount=Decimal("149.99"), currency="USD"
    )
    await setup_provider_config(session, secret_storage, tenant.id, "mock")

    payload = {
        "order_id": str(order.id),
        "provider_name": "mock",
        "idempotency_key": "intent_key_api_001",
        "metadata": {"source": "miniapp"},
        "return_url": "https://example.com/checkout/return",
    }
    headers = {
        "X-Tenant-ID": str(tenant.id),
        "X-User-ID": str(user.id),
    }

    # 1. Create intent
    response = await client.post("/api/v1/payments/intents", json=payload, headers=headers)
    assert response.status_code == 201, f"Expected 201 Created, got {response.status_code}: {response.text}"

    data = response.json()
    assert data["order_id"] == str(order.id)
    assert data["tenant_id"] == str(tenant.id)
    assert data["user_id"] == str(user.id)
    assert data["provider"] == "mock"
    # Strict server authority: amount & currency come directly from order
    assert Decimal(data["amount"]) == Decimal("149.99")
    assert data["currency"] == "USD"
    assert data["status"] == "PENDING"
    assert data["idempotency_key"] == "intent_key_api_001"
    assert data["provider_payment_id"] == "mock_pay_intent_key_api_001"

    # Verify Order in DB transitioned to PAYMENT_PENDING
    await session.refresh(order)
    assert order.status == OrderStatus.PAYMENT_PENDING

    # 2. Idempotent replay with same idempotency key returns same intent
    dup_response = await client.post("/api/v1/payments/intents", json=payload, headers=headers)
    assert dup_response.status_code == 201
    assert dup_response.json()["id"] == data["id"]

    # 3. Prevent multiple active intents for the same order with different idempotency key
    conflict_payload = dict(payload, idempotency_key="intent_key_api_conflict")
    conflict_response = await client.post("/api/v1/payments/intents", json=conflict_payload, headers=headers)
    assert conflict_response.status_code == 400
    assert "already exists" in conflict_response.json()["detail"]

    # 4. Missing required tenant or user headers rejected
    no_tenant_resp = await client.post(
        "/api/v1/payments/intents",
        json=payload,
        headers={"X-User-ID": str(user.id)},
    )
    assert no_tenant_resp.status_code == 422

    no_user_resp = await client.post(
        "/api/v1/payments/intents",
        json=payload,
        headers={"X-Tenant-ID": str(tenant.id)},
    )
    assert no_user_resp.status_code == 422


# ---------------------------------------------------------------------------
# 2. GET /api/v1/payments/intents/{id}: Tenant Isolation Enforcement
# ---------------------------------------------------------------------------

async def test_get_payment_intent_respects_tenant_isolation(api_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant_a = await create_tenant(session, "Tenant A")
    tenant_b = await create_tenant(session, "Tenant B")
    user_a = await create_user(session, tenant_a.id)
    order_a = await create_order(session, tenant_a.id, user_a.id, total_amount=Decimal("50.00"))
    await setup_provider_config(session, secret_storage, tenant_a.id, "mock")

    # Create intent for Tenant A
    create_resp = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_a.id),
            "provider_name": "mock",
            "idempotency_key": "tenant_iso_key_a",
        },
        headers={
            "X-Tenant-ID": str(tenant_a.id),
            "X-User-ID": str(user_a.id),
        },
    )
    assert create_resp.status_code == 201
    intent_id = create_resp.json()["id"]

    # 1. Tenant A fetches its own intent -> 200 OK
    resp_a = await client.get(
        f"/api/v1/payments/intents/{intent_id}",
        headers={"X-Tenant-ID": str(tenant_a.id)},
    )
    assert resp_a.status_code == 200
    assert resp_a.json()["id"] == intent_id
    assert resp_a.json()["tenant_id"] == str(tenant_a.id)

    # 2. Tenant B attempts to fetch Tenant A's intent -> 403 Forbidden
    resp_b = await client.get(
        f"/api/v1/payments/intents/{intent_id}",
        headers={"X-Tenant-ID": str(tenant_b.id)},
    )
    assert resp_b.status_code == 403
    assert "cannot access payment intent" in resp_b.json()["detail"]

    # 3. Non-existent intent -> 404 Not Found
    random_id = uuid.uuid4()
    resp_404 = await client.get(
        f"/api/v1/payments/intents/{random_id}",
        headers={"X-Tenant-ID": str(tenant_a.id)},
    )
    assert resp_404.status_code == 404


# ---------------------------------------------------------------------------
# 3. POST /api/v1/payments/intents/{id}/cancel: Cancel Intent & Restrictions
# ---------------------------------------------------------------------------

async def test_post_payment_intent_cancel_lifecycle(api_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant_a = await create_tenant(session, "Tenant A")
    tenant_b = await create_tenant(session, "Tenant B")
    user_a = await create_user(session, tenant_a.id)
    order_a = await create_order(session, tenant_a.id, user_a.id)
    await setup_provider_config(session, secret_storage, tenant_a.id, "mock")

    create_resp = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_a.id),
            "provider_name": "mock",
            "idempotency_key": "cancel_key_001",
        },
        headers={
            "X-Tenant-ID": str(tenant_a.id),
            "X-User-ID": str(user_a.id),
        },
    )
    assert create_resp.status_code == 201
    intent_id = create_resp.json()["id"]

    # 1. Cross-tenant cancel rejected -> 403 Forbidden
    cross_resp = await client.post(
        f"/api/v1/payments/intents/{intent_id}/cancel",
        headers={"X-Tenant-ID": str(tenant_b.id)},
    )
    assert cross_resp.status_code == 403

    # 2. Legitimate cancel by Tenant A -> 200 OK
    cancel_resp = await client.post(
        f"/api/v1/payments/intents/{intent_id}/cancel",
        headers={"X-Tenant-ID": str(tenant_a.id)},
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"

    # 3. Re-cancelling already CANCELLED terminal intent -> 409 Conflict
    re_cancel_resp = await client.post(
        f"/api/v1/payments/intents/{intent_id}/cancel",
        headers={"X-Tenant-ID": str(tenant_a.id)},
    )
    assert re_cancel_resp.status_code == 409

    # 4. Non-existent intent -> 404 Not Found
    resp_404 = await client.post(
        f"/api/v1/payments/intents/{uuid.uuid4()}/cancel",
        headers={"X-Tenant-ID": str(tenant_a.id)},
    )
    assert resp_404.status_code == 404


# ---------------------------------------------------------------------------
# 4. POST /api/v1/payments/intents/{id}/reconcile: Gateway Reconciliation
# ---------------------------------------------------------------------------

async def test_post_payment_intent_reconcile(api_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]
    registry: PaymentProviderRegistry = api_env["registry"]

    tenant_a = await create_tenant(session, "Tenant A")
    tenant_b = await create_tenant(session, "Tenant B")
    user_a = await create_user(session, tenant_a.id)
    order_a = await create_order(
        session, tenant_a.id, user_a.id, total_amount=Decimal("88.50"), currency="USD"
    )

    mock_provider = MockPaymentProvider(default_create_status=PaymentIntentStatus.PENDING)
    registry.register_instance(tenant_a.id, "mock", mock_provider)
    await setup_provider_config(session, secret_storage, tenant_a.id, "mock")

    create_resp = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_a.id),
            "provider_name": "mock",
            "idempotency_key": "reconcile_api_key",
        },
        headers={
            "X-Tenant-ID": str(tenant_a.id),
            "X-User-ID": str(user_a.id),
        },
    )
    assert create_resp.status_code == 201
    intent_id = create_resp.json()["id"]
    provider_payment_id = create_resp.json()["provider_payment_id"]
    assert create_resp.json()["status"] == "PENDING"

    # 1. Tenant B attempting to reconcile Tenant A's intent -> 403 Forbidden
    cross_resp = await client.post(
        f"/api/v1/payments/intents/{intent_id}/reconcile",
        headers={"X-Tenant-ID": str(tenant_b.id)},
    )
    assert cross_resp.status_code == 403

    # 2. Gateway updates payment status to SUCCEEDED
    mock_provider.payments[provider_payment_id]["status"] = PaymentIntentStatus.SUCCEEDED

    # Reconcile via API
    reconcile_resp = await client.post(
        f"/api/v1/payments/intents/{intent_id}/reconcile",
        headers={"X-Tenant-ID": str(tenant_a.id)},
    )
    assert reconcile_resp.status_code == 200
    data = reconcile_resp.json()
    assert data["status"] == "SUCCEEDED"

    # Verify Order and Wallet settlement in database
    await session.refresh(order_a)
    assert order_a.status == OrderStatus.PAID

    wallet = await LedgerService.get_or_create_wallet(session, tenant_a.id, user_a.id, "USD")
    assert wallet.balance == Decimal("88.50")

    # 3. Repeated reconcile on already SUCCEEDED intent is a safe no-op
    repeat_resp = await client.post(
        f"/api/v1/payments/intents/{intent_id}/reconcile",
        headers={"X-Tenant-ID": str(tenant_a.id)},
    )
    assert repeat_resp.status_code == 200
    assert repeat_resp.json()["status"] == "SUCCEEDED"
    assert wallet.balance == Decimal("88.50")

    # 4. Gateway lookup timeout marks intent UNKNOWN without crediting wallet
    order_timeout = await create_order(
        session, tenant_a.id, user_a.id, total_amount=Decimal("30.00"), currency="USD"
    )
    mock_provider_timeout = MockPaymentProvider(
        default_create_status=PaymentIntentStatus.PENDING,
        simulate_lookup_timeout=True,
    )
    registry.register_instance(tenant_a.id, "mock_timeout", mock_provider_timeout)
    await setup_provider_config(session, secret_storage, tenant_a.id, "mock_timeout")

    timeout_create_resp = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_timeout.id),
            "provider_name": "mock_timeout",
            "idempotency_key": "timeout_reconcile_key",
        },
        headers={
            "X-Tenant-ID": str(tenant_a.id),
            "X-User-ID": str(user_a.id),
        },
    )
    assert timeout_create_resp.status_code == 201
    timeout_intent_id = timeout_create_resp.json()["id"]

    timeout_reconcile_resp = await client.post(
        f"/api/v1/payments/intents/{timeout_intent_id}/reconcile",
        headers={"X-Tenant-ID": str(tenant_a.id)},
    )
    assert timeout_reconcile_resp.status_code == 200
    assert timeout_reconcile_resp.json()["status"] == "UNKNOWN"
    # Wallet balance remains unchanged at 88.50
    assert wallet.balance == Decimal("88.50")


# ---------------------------------------------------------------------------
# 5. POST /api/v1/payments/webhooks/{provider_name}: Signature Verification
# ---------------------------------------------------------------------------

async def test_post_payment_webhooks_signature_and_deduplication(api_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    user = await create_user(session, tenant.id)
    order = await create_order(
        session, tenant.id, user.id, total_amount=Decimal("60.00"), currency="USD"
    )

    webhook_secret = "api_wh_super_secret_999"
    await setup_provider_config(
        session, secret_storage, tenant.id, "mock", webhook_secret=webhook_secret
    )

    # Create Payment Intent
    create_resp = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order.id),
            "provider_name": "mock",
            "idempotency_key": "wh_test_key_001",
        },
        headers={
            "X-Tenant-ID": str(tenant.id),
            "X-User-ID": str(user.id),
        },
    )
    assert create_resp.status_code == 201
    provider_payment_id = create_resp.json()["provider_payment_id"]

    valid_payload = {
        "event_id": "evt_api_wh_100",
        "event_type": "payment.succeeded",
        "provider_payment_id": provider_payment_id,
        "amount": "60.00",
        "currency": "USD",
        "status": "SUCCEEDED",
    }
    payload_bytes = json.dumps(valid_payload).encode("utf-8")
    valid_sig = hmac.new(webhook_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

    # 1. Invalid signature returns 401 Unauthorized
    invalid_resp = await client.post(
        "/api/v1/payments/webhooks/mock",
        content=payload_bytes,
        headers={
            "X-Tenant-ID": str(tenant.id),
            "X-Signature": "invalid_forged_sig_abc123",
            "Content-Type": "application/json",
        },
    )
    assert invalid_resp.status_code == 401
    assert "signature" in invalid_resp.json()["detail"].lower()

    # 2. Valid signature returns 200 OK and processes settlement
    valid_resp = await client.post(
        "/api/v1/payments/webhooks/mock",
        content=payload_bytes,
        headers={
            "X-Tenant-ID": str(tenant.id),
            "X-Signature": valid_sig,
            "Content-Type": "application/json",
        },
    )
    assert valid_resp.status_code == 200
    assert valid_resp.json()["status"] == "ok"
    assert valid_resp.json()["processed"] is True
    event_id = valid_resp.json()["event_id"]

    # Verify wallet ledger credit
    wallet = await LedgerService.get_or_create_wallet(session, tenant.id, user.id, "USD")
    assert wallet.balance == Decimal("60.00")

    # Verify order status
    await session.refresh(order)
    assert order.status == OrderStatus.PAID

    # 3. Duplicate webhook delivery is idempotent (returns 200, same event_id, no double-credit)
    dup_resp = await client.post(
        "/api/v1/payments/webhooks/mock",
        content=payload_bytes,
        headers={
            "X-Tenant-ID": str(tenant.id),
            "X-Signature": valid_sig,
            "Content-Type": "application/json",
        },
    )
    assert dup_resp.status_code == 200
    assert dup_resp.json()["event_id"] == event_id
    assert wallet.balance == Decimal("60.00")

    # 4. Tampered webhook amount returns 409 Conflict
    tampered_payload = {
        "event_id": "evt_tampered_amt",
        "event_type": "payment.succeeded",
        "provider_payment_id": provider_payment_id,
        "amount": "10.00",  # Mismatch with 60.00
        "currency": "USD",
        "status": "SUCCEEDED",
    }
    tampered_bytes = json.dumps(tampered_payload).encode("utf-8")
    tampered_sig = hmac.new(webhook_secret.encode("utf-8"), tampered_bytes, hashlib.sha256).hexdigest()

    conflict_resp = await client.post(
        "/api/v1/payments/webhooks/mock",
        content=tampered_bytes,
        headers={
            "X-Tenant-ID": str(tenant.id),
            "X-Signature": tampered_sig,
            "Content-Type": "application/json",
        },
    )
    assert conflict_resp.status_code == 409
    assert "does not match intent amount" in conflict_resp.json()["detail"]


# ---------------------------------------------------------------------------
# 6. POST /api/v1/auth/telegram-miniapp: Mini App Authentication Boundary
# ---------------------------------------------------------------------------

async def test_post_auth_telegram_miniapp_full_suite(api_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant_a = await create_tenant(session, "Tenant A")
    tenant_b = await create_tenant(session, "Tenant B")

    bot_token = "987654321:AAHk69MockTelegramBotTokenForTesting"
    token_ref = f"BOT_TOKEN_API_{tenant_a.id}"
    await secret_storage.set_secret(token_ref, bot_token)

    bot_a = Bot(
        tenant_id=tenant_a.id,
        telegram_bot_id=987654321,
        display_name="Store Bot A",
        token_secret_ref=token_ref,
        is_enabled=True,
    )
    session.add(bot_a)
    await session.flush()

    user_payload = {
        "id": 543216789,
        "first_name": "Bob",
        "last_name": "Builder",
        "username": "bob_builder",
        "language_code": "en",
    }

    # 1. Valid initData -> 200 OK with authenticated tenant and user
    valid_init_data = make_telegram_init_data(bot_token, user_payload)
    resp_valid = await client.post(
        "/api/v1/auth/telegram-miniapp",
        json={
            "init_data": valid_init_data,
            "bot_id": str(bot_a.id),
        },
    )
    assert resp_valid.status_code == 200
    body = resp_valid.json()
    assert body["tenant_id"] == str(tenant_a.id)
    assert body["telegram_user"]["id"] == 543216789
    assert body["telegram_user"]["username"] == "bob_builder"
    user_id = body["user_id"]
    assert user_id is not None

    # 2. Invalid cryptographic signature -> 401 Unauthorized
    tampered_init_data = (
        f"auth_date={int(time.time())}&query_id=AAHdF6IQAAAAAN0XohDhrOrc"
        f"&user=%7B%22id%22%3A543216789%7D&hash=0000000000000000000000000000000000000000000000000000000000000000"
    )
    resp_invalid_sig = await client.post(
        "/api/v1/auth/telegram-miniapp",
        json={
            "init_data": tampered_init_data,
            "bot_id": str(bot_a.id),
        },
    )
    assert resp_invalid_sig.status_code == 401
    assert "Cryptographic verification failed" in resp_invalid_sig.json()["detail"]

    # 3. Expired initData auth_date (> 86400s) -> 401 Unauthorized
    expired_auth_date = int(time.time()) - 100_000
    expired_init_data = make_telegram_init_data(bot_token, user_payload, auth_date=expired_auth_date)
    resp_expired = await client.post(
        "/api/v1/auth/telegram-miniapp",
        json={
            "init_data": expired_init_data,
            "bot_id": str(bot_a.id),
        },
    )
    assert resp_expired.status_code == 401
    assert "expired" in resp_expired.json()["detail"].lower()

    # 4. Tenant mismatch (client asserts Tenant B context for Bot belonging to Tenant A) -> 403 Forbidden
    # 4a. Via body 'expected_tenant_id'
    resp_mismatch_body = await client.post(
        "/api/v1/auth/telegram-miniapp",
        json={
            "init_data": valid_init_data,
            "bot_id": str(bot_a.id),
            "expected_tenant_id": str(tenant_b.id),
        },
    )
    assert resp_mismatch_body.status_code == 403
    assert "belongs to tenant" in resp_mismatch_body.json()["detail"]

    # 4b. Via header 'X-Tenant-ID'
    resp_mismatch_header = await client.post(
        "/api/v1/auth/telegram-miniapp",
        json={
            "init_data": valid_init_data,
            "bot_id": str(bot_a.id),
        },
        headers={"X-Tenant-ID": str(tenant_b.id)},
    )
    assert resp_mismatch_header.status_code == 403
    assert "belongs to tenant" in resp_mismatch_header.json()["detail"]

    # 5. Inactive tenant -> 403 Forbidden
    inactive_tenant = await create_tenant(session, "Inactive Tenant", is_active=False)
    token_ref_inactive = f"BOT_TOKEN_INACT_{inactive_tenant.id}"
    await secret_storage.set_secret(token_ref_inactive, bot_token)
    bot_inactive = Bot(
        tenant_id=inactive_tenant.id,
        telegram_bot_id=11223344,
        display_name="Inactive Bot",
        token_secret_ref=token_ref_inactive,
        is_enabled=True,
    )
    session.add(bot_inactive)
    await session.flush()

    init_data_inactive = make_telegram_init_data(bot_token, user_payload)
    resp_inactive = await client.post(
        "/api/v1/auth/telegram-miniapp",
        json={
            "init_data": init_data_inactive,
            "bot_id": str(bot_inactive.id),
        },
    )
    assert resp_inactive.status_code == 403
    assert "inactive or deleted" in resp_inactive.json()["detail"]

    # 6. Malformed JSON user payload -> 400 Bad Request
    corrupt_params = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": "{malformed_not_json: true",
    }
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(corrupt_params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    corrupt_params["hash"] = hmac.new(secret_key, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    corrupt_init_data = urllib.parse.urlencode(corrupt_params)

    resp_corrupt = await client.post(
        "/api/v1/auth/telegram-miniapp",
        json={
            "init_data": corrupt_init_data,
            "bot_id": str(bot_a.id),
        },
    )
    assert resp_corrupt.status_code == 400
