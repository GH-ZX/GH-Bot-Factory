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

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from apps.api.v1.auth import get_secret_storage
from apps.api.v1.payments import (
    get_db_session,
    get_payment_service,
    get_reconciliation_service,
)
from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStatus
from packages.core.auth import AuthSource, AuthTokenService
from packages.payments.models import PaymentProviderConfig
from packages.payments.payment_service import PaymentService
from packages.payments.providers.mock import MockPaymentProvider
from packages.payments.providers.registry import PaymentProviderRegistry
from packages.payments.reconciliation import PaymentReconciliationService
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.models import Bot
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio

TEST_JWT_SECRET = "test-jwt-secret-key-0123456789abcdef-0123456789abcdef"


# ---------------------------------------------------------------------------
# Helpers & Factories
# ---------------------------------------------------------------------------

async def create_tenant(
    session: AsyncSession,
    name: str = "Auth Test Tenant",
    is_active: bool = True,
) -> Tenant:
    tenant = Tenant(
        name=name,
        slug=f"tenant-{uuid.uuid4().hex[:8]}",
        is_active=is_active,
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def create_user(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    telegram_id: int | None = None,
    role: Role = Role.CUSTOMER,
    is_active: bool = True,
    token_version: int = 1,
) -> tuple[User, str]:
    user = User(
        telegram_id=telegram_id or int(uuid.uuid4().int % 2_000_000_000),
        username=f"user_{uuid.uuid4().hex[:6]}",
        first_name="TestUser",
        is_active=is_active,
        token_version=token_version,
    )
    session.add(user)
    await session.flush()

    membership = Membership(
        tenant_id=tenant_id,
        user_id=user.id,
        role=role,
        is_active=is_active,
    )
    session.add(membership)
    await session.flush()

    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    token = token_service.issue_access_token(
        user_id=user.id,
        tenant_id=tenant_id,
        roles=[role],
        source=AuthSource.TEST,
        token_version=token_version,
    )
    return user, token


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
    secret_storage = EnvSecretStorage()
    registry = PaymentProviderRegistry()
    mock_provider = MockPaymentProvider(default_create_status=PaymentIntentStatus.PENDING)
    payment_service = PaymentService(registry=registry, secret_storage=secret_storage)
    reconcile_service = PaymentReconciliationService(payment_service=payment_service)
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_secret_storage] = lambda: secret_storage
    app.dependency_overrides[get_payment_service] = lambda: payment_service
    app.dependency_overrides[get_reconciliation_service] = lambda: reconcile_service
    app.dependency_overrides[get_auth_token_service] = lambda: token_service

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
            "token_service": token_service,
        }

    app.dependency_overrides.clear()


# ===========================================================================
# 1. Authentication Tests
# ===========================================================================

async def test_auth_missing_header_returns_401(api_env: dict[str, Any]) -> None:
    """Missing Authorization header must be rejected with 401 Unauthorized."""
    client: httpx.AsyncClient = api_env["client"]

    # 1. POST /intents without auth
    resp_post = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(uuid.uuid4()),
            "provider_name": "mock",
            "idempotency_key": "k_no_auth",
        },
    )
    assert resp_post.status_code == 401
    assert "Authorization" in resp_post.json()["detail"]
    assert resp_post.headers.get("www-authenticate") == "Bearer"

    # 2. GET /intents/{id} without auth
    resp_get = await client.get(f"/api/v1/payments/intents/{uuid.uuid4()}")
    assert resp_get.status_code == 401
    assert resp_get.headers.get("www-authenticate") == "Bearer"


async def test_auth_malformed_token_returns_401(api_env: dict[str, Any]) -> None:
    """Malformed, non-Bearer, or corrupted tokens must be rejected with 401."""
    client: httpx.AsyncClient = api_env["client"]

    # 1. Non-Bearer scheme
    resp_scheme = await client.get(
        f"/api/v1/payments/intents/{uuid.uuid4()}",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert resp_scheme.status_code == 401
    assert "Invalid Authorization scheme" in resp_scheme.json()["detail"]

    # 2. Empty token after Bearer
    resp_empty = await client.get(
        f"/api/v1/payments/intents/{uuid.uuid4()}",
        headers={"Authorization": "Bearer   "},
    )
    assert resp_empty.status_code == 401
    assert "Empty access token" in resp_empty.json()["detail"]

    # 3. Corrupt/garbage JWT
    resp_corrupt = await client.get(
        f"/api/v1/payments/intents/{uuid.uuid4()}",
        headers={"Authorization": "Bearer not.a.valid.jwt.payload"},
    )
    assert resp_corrupt.status_code == 401
    assert "Malformed or invalid token" in resp_corrupt.json()["detail"]


async def test_auth_expired_token_returns_401(api_env: dict[str, Any]) -> None:
    """Expired JWT access token must be rejected with 401 Unauthorized."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    token_service: AuthTokenService = api_env["token_service"]

    tenant = await create_tenant(session)
    user, _ = await create_user(session, tenant.id)

    # Issue token that expired 1 hour ago
    expired_token = token_service.issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.CUSTOMER],
        source=AuthSource.TEST,
        expires_in_seconds=-3600,
    )

    resp = await client.get(
        f"/api/v1/payments/intents/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


async def test_auth_invalid_signature_returns_401(api_env: dict[str, Any]) -> None:
    """JWT signed with an untrusted / foreign key must be rejected with 401."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]

    tenant = await create_tenant(session)
    user, _ = await create_user(session, tenant.id)

    # Issue token signed with an invalid / unknown secret key
    forged_token_service = AuthTokenService(secret_key="attacker-forged-secret-key-at-least-256-bits-long-xyz")
    forged_token = forged_token_service.issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.CUSTOMER],
        source=AuthSource.TEST,
    )

    resp = await client.get(
        f"/api/v1/payments/intents/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {forged_token}"},
    )
    assert resp.status_code == 401
    assert "signature is invalid" in resp.json()["detail"].lower()


async def test_auth_inactive_user_returns_403(api_env: dict[str, Any]) -> None:
    """Deactivated user account must be rejected with 403 Forbidden."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]

    tenant = await create_tenant(session)
    user, token = await create_user(session, tenant.id)

    # Deactivate user in database
    user.is_active = False
    await session.flush()

    resp = await client.get(
        f"/api/v1/payments/intents/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "deactivated" in resp.json()["detail"].lower()


async def test_auth_inactive_tenant_returns_403(api_env: dict[str, Any]) -> None:
    """Inactive tenant must be rejected with 403 Forbidden."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]

    tenant = await create_tenant(session)
    _, token = await create_user(session, tenant.id)

    # Deactivate tenant in database
    tenant.is_active = False
    await session.flush()

    resp = await client.get(
        f"/api/v1/payments/intents/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "inactive or deleted" in resp.json()["detail"].lower()


async def test_auth_user_not_member_returns_403(api_env: dict[str, Any]) -> None:
    """Valid user claiming token for a tenant where they have no membership is rejected with 403."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    token_service: AuthTokenService = api_env["token_service"]

    tenant_a = await create_tenant(session, "Tenant A")
    tenant_b = await create_tenant(session, "Tenant B")
    user_a, _ = await create_user(session, tenant_a.id)

    # Issue token claiming Tenant B membership, but user_a only belongs to Tenant A
    forged_tenant_token = token_service.issue_access_token(
        user_id=user_a.id,
        tenant_id=tenant_b.id,
        roles=[Role.CUSTOMER],
        source=AuthSource.TEST,
    )

    resp = await client.get(
        f"/api/v1/payments/intents/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {forged_tenant_token}"},
    )
    assert resp.status_code == 403
    assert "not a member" in resp.json()["detail"].lower()


# ===========================================================================
# 2. Identity Spoofing Tests
# ===========================================================================

async def test_identity_spoofing_user_id_header_ignored(api_env: dict[str, Any]) -> None:
    """API derives user identity strictly from Bearer token, ignoring client-supplied X-User-ID."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    user_a, token_a = await create_user(session, tenant.id)
    user_b, _ = await create_user(session, tenant.id)

    order_a = await create_order(session, tenant.id, user_a.id, Decimal("50.00"))
    order_b = await create_order(session, tenant.id, user_b.id, Decimal("75.00"))
    await setup_provider_config(session, secret_storage, tenant.id, "mock")

    # 1. User A creates intent for their own order while attempting to spoof X-User-ID as User B
    resp_create = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_a.id),
            "provider_name": "mock",
            "idempotency_key": "spoof_user_001",
        },
        headers={
            "Authorization": f"Bearer {token_a}",
            "X-User-ID": str(user_b.id),
        },
    )
    assert resp_create.status_code == 201
    created_intent = resp_create.json()
    # Authoritative principal user_a MUST be recorded, not spoofed user_b
    assert created_intent["user_id"] == str(user_a.id)
    assert created_intent["user_id"] != str(user_b.id)

    # 2. User A attempts to create intent for User B's order while spoofing X-User-ID
    resp_spoof_order = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_b.id),
            "provider_name": "mock",
            "idempotency_key": "spoof_user_002",
        },
        headers={
            "Authorization": f"Bearer {token_a}",
            "X-User-ID": str(user_b.id),
        },
    )
    # Blocked because token principal is user_a who does not own order_b
    assert resp_spoof_order.status_code == 403
    assert "does not have access to order" in resp_spoof_order.json()["detail"]


async def test_identity_spoofing_tenant_id_header_ignored(api_env: dict[str, Any]) -> None:
    """API derives tenant identity strictly from Bearer token, ignoring client-supplied X-Tenant-ID."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant_a = await create_tenant(session, "Tenant A")
    tenant_b = await create_tenant(session, "Tenant B")
    user_a, token_a = await create_user(session, tenant_a.id)

    order_a = await create_order(session, tenant_a.id, user_a.id, Decimal("120.00"))
    await setup_provider_config(session, secret_storage, tenant_a.id, "mock")

    # User A asserts Tenant B in X-Tenant-ID header
    resp = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_a.id),
            "provider_name": "mock",
            "idempotency_key": "spoof_tenant_001",
        },
        headers={
            "Authorization": f"Bearer {token_a}",
            "X-Tenant-ID": str(tenant_b.id),
        },
    )
    assert resp.status_code == 201
    created_intent = resp.json()
    # Authoritative tenant_a MUST be recorded, not spoofed tenant_b
    assert created_intent["tenant_id"] == str(tenant_a.id)
    assert created_intent["tenant_id"] != str(tenant_b.id)


# ===========================================================================
# 3. Cross-Tenant Isolation Tests
# ===========================================================================

async def test_cross_tenant_token_cannot_access_or_mutate_other_tenant_resources(
    api_env: dict[str, Any],
) -> None:
    """Tenant A token must be rejected with 403 when trying to access or mutate Tenant B resources."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant_a = await create_tenant(session, "Tenant A")
    tenant_b = await create_tenant(session, "Tenant B")
    _, token_a = await create_user(session, tenant_a.id)
    user_b, token_b = await create_user(session, tenant_b.id)

    order_b = await create_order(session, tenant_b.id, user_b.id, Decimal("99.00"))
    await setup_provider_config(session, secret_storage, tenant_b.id, "mock")

    # Create PaymentIntent in Tenant B
    resp_b_intent = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_b.id),
            "provider_name": "mock",
            "idempotency_key": "tenant_b_intent_001",
        },
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_b_intent.status_code == 201
    intent_b_id = resp_b_intent.json()["id"]

    # 1. Tenant A token attempts to create intent for Tenant B order -> 403 Forbidden
    resp_cross_create = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_b.id),
            "provider_name": "mock",
            "idempotency_key": "cross_create_001",
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_cross_create.status_code == 403

    # 2. Tenant A token attempts to fetch Tenant B PaymentIntent -> 403 Forbidden
    resp_cross_get = await client.get(
        f"/api/v1/payments/intents/{intent_b_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_cross_get.status_code == 403
    assert "cannot access payment intent" in resp_cross_get.json()["detail"]

    # 3. Tenant A token attempts to cancel Tenant B PaymentIntent -> 403 Forbidden
    resp_cross_cancel = await client.post(
        f"/api/v1/payments/intents/{intent_b_id}/cancel",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_cross_cancel.status_code == 403

    # 4. Tenant A token attempts to reconcile Tenant B PaymentIntent -> 403 Forbidden
    resp_cross_reconcile = await client.post(
        f"/api/v1/payments/intents/{intent_b_id}/reconcile",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_cross_reconcile.status_code == 403


# ===========================================================================
# 4. Customer Ownership & RBAC Tests
# ===========================================================================

async def test_customer_cannot_access_or_mutate_other_customer_intent_same_tenant(
    api_env: dict[str, Any],
) -> None:
    """In the same tenant, Customer A cannot read, cancel, or reconcile Customer B's payment intent."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    _, token_a = await create_user(session, tenant.id, role=Role.CUSTOMER)
    user_b, token_b = await create_user(session, tenant.id, role=Role.CUSTOMER)

    order_b = await create_order(session, tenant.id, user_b.id, Decimal("45.00"))
    await setup_provider_config(session, secret_storage, tenant.id, "mock")

    # Customer B creates an intent
    resp_intent = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_b.id),
            "provider_name": "mock",
            "idempotency_key": "customer_b_intent_001",
        },
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_intent.status_code == 201
    intent_id = resp_intent.json()["id"]

    # 1. Customer A tries to read Customer B's intent -> 403 Forbidden
    resp_get = await client.get(
        f"/api/v1/payments/intents/{intent_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_get.status_code == 403
    assert "Customer cannot access another user's payment intent" in resp_get.json()["detail"]

    # 2. Customer A tries to cancel Customer B's intent -> 403 Forbidden
    resp_cancel = await client.post(
        f"/api/v1/payments/intents/{intent_id}/cancel",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_cancel.status_code == 403
    assert "Customer cannot cancel another user's payment intent" in resp_cancel.json()["detail"]

    # 3. Customer A tries to reconcile Customer B's intent -> 403 Forbidden
    resp_reconcile = await client.post(
        f"/api/v1/payments/intents/{intent_id}/reconcile",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_reconcile.status_code == 403
    assert "Customer cannot reconcile another user's payment intent" in resp_reconcile.json()["detail"]


async def test_customer_cannot_create_intent_for_other_customer_order(
    api_env: dict[str, Any],
) -> None:
    """Customer A cannot create a payment intent for Customer B's order within the same tenant."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    _, token_a = await create_user(session, tenant.id, role=Role.CUSTOMER)
    user_b, _ = await create_user(session, tenant.id, role=Role.CUSTOMER)

    order_b = await create_order(session, tenant.id, user_b.id, Decimal("100.00"))
    await setup_provider_config(session, secret_storage, tenant.id, "mock")

    resp = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_b.id),
            "provider_name": "mock",
            "idempotency_key": "cust_a_takes_cust_b_order",
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
    assert "does not have access to order" in resp.json()["detail"]


async def test_staff_and_admin_can_access_and_manage_all_intents_in_tenant(
    api_env: dict[str, Any],
) -> None:
    """Staff and Admin roles can inspect, reconcile, and cancel payment intents across their tenant."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    user_cust, token_cust = await create_user(session, tenant.id, role=Role.CUSTOMER)
    _, token_staff = await create_user(session, tenant.id, role=Role.STAFF)
    _, token_admin = await create_user(session, tenant.id, role=Role.ADMIN)

    await setup_provider_config(session, secret_storage, tenant.id, "mock")

    # Intent 1: For Staff inspection & reconciliation
    order_1 = await create_order(session, tenant.id, user_cust.id, Decimal("50.00"))
    resp_intent_1 = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_1.id),
            "provider_name": "mock",
            "idempotency_key": "staff_manage_001",
        },
        headers={"Authorization": f"Bearer {token_cust}"},
    )
    assert resp_intent_1.status_code == 201
    intent_1_id = resp_intent_1.json()["id"]

    # 1. Staff fetches Customer's intent -> 200 OK
    resp_staff_get = await client.get(
        f"/api/v1/payments/intents/{intent_1_id}",
        headers={"Authorization": f"Bearer {token_staff}"},
    )
    assert resp_staff_get.status_code == 200
    assert resp_staff_get.json()["id"] == intent_1_id

    # 2. Staff reconciles Customer's intent -> 200 OK
    resp_staff_reconcile = await client.post(
        f"/api/v1/payments/intents/{intent_1_id}/reconcile",
        headers={"Authorization": f"Bearer {token_staff}"},
    )
    assert resp_staff_reconcile.status_code == 200

    # Intent 2: For Admin inspection & cancellation
    order_2 = await create_order(session, tenant.id, user_cust.id, Decimal("70.00"))
    resp_intent_2 = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order_2.id),
            "provider_name": "mock",
            "idempotency_key": "admin_manage_002",
        },
        headers={"Authorization": f"Bearer {token_cust}"},
    )
    assert resp_intent_2.status_code == 201
    intent_2_id = resp_intent_2.json()["id"]

    # 3. Admin fetches Customer's intent -> 200 OK
    resp_admin_get = await client.get(
        f"/api/v1/payments/intents/{intent_2_id}",
        headers={"Authorization": f"Bearer {token_admin}"},
    )
    assert resp_admin_get.status_code == 200

    # 4. Admin cancels Customer's intent -> 200 OK
    resp_admin_cancel = await client.post(
        f"/api/v1/payments/intents/{intent_2_id}/cancel",
        headers={"Authorization": f"Bearer {token_admin}"},
    )
    assert resp_admin_cancel.status_code == 200
    assert resp_admin_cancel.json()["status"] == "CANCELLED"


# ===========================================================================
# 5. Mini App Authentication Tests
# ===========================================================================

async def test_telegram_miniapp_auth_returns_bearer_token_and_works_immediately(
    api_env: dict[str, Any],
) -> None:
    """POST /api/v1/auth/telegram-miniapp authenticates initData, returns token, usable immediately."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    bot_token = "123456789:" + "AAHk69MockTelegramBotTokenForTestingAuth"
    token_ref = f"BOT_TOKEN_MINIAPP_{tenant.id}"
    await secret_storage.set_secret(token_ref, bot_token)

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=123456789,
        display_name="MiniApp Store Bot",
        token_secret_ref=token_ref,
        is_enabled=True,
    )
    session.add(bot)
    await session.flush()

    user_payload = {
        "id": 987654321,
        "first_name": "Carol",
        "last_name": "Danvers",
        "username": "carol_d",
        "language_code": "en",
    }
    valid_init_data = make_telegram_init_data(bot_token, user_payload)

    # 1. Exchange initData for access token
    resp = await client.post(
        "/api/v1/auth/telegram-miniapp",
        json={
            "init_data": valid_init_data,
            "bot_id": str(bot.id),
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert body["tenant_id"] == str(tenant.id)
    assert body["telegram_user"]["id"] == 987654321
    user_id = uuid.UUID(body["user_id"])
    access_token = body["access_token"]

    # 2. Use returned token immediately to access payment endpoints
    await setup_provider_config(session, secret_storage, tenant.id, "mock")
    order = await create_order(session, tenant.id, user_id, Decimal("33.00"))

    intent_resp = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order.id),
            "provider_name": "mock",
            "idempotency_key": "miniapp_immediate_token_001",
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert intent_resp.status_code == 201
    assert intent_resp.json()["user_id"] == str(user_id)


async def test_telegram_miniapp_expired_auth_date_returns_401(api_env: dict[str, Any]) -> None:
    """Telegram initData with expired auth_date (> 86400s) must return 401 Unauthorized."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    bot_token = "123456789:" + "AAHk69MockTelegramBotTokenForTestingAuth"
    token_ref = f"BOT_TOKEN_EXPIRED_{tenant.id}"
    await secret_storage.set_secret(token_ref, bot_token)

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=123456789,
        display_name="MiniApp Store Bot",
        token_secret_ref=token_ref,
        is_enabled=True,
    )
    session.add(bot)
    await session.flush()

    user_payload = {"id": 1122334455, "username": "time_traveler"}
    expired_time = int(time.time()) - 100_000  # More than 24 hours ago
    expired_init_data = make_telegram_init_data(bot_token, user_payload, auth_date=expired_time)

    resp = await client.post(
        "/api/v1/auth/telegram-miniapp",
        json={
            "init_data": expired_init_data,
            "bot_id": str(bot.id),
        },
    )
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


async def test_telegram_miniapp_invalid_hmac_hash_returns_401(api_env: dict[str, Any]) -> None:
    """Telegram initData with forged or corrupted HMAC hash must return 401 Unauthorized."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    bot_token = "123456789:" + "AAHk69MockTelegramBotTokenForTestingAuth"
    token_ref = f"BOT_TOKEN_FORGED_{tenant.id}"
    await secret_storage.set_secret(token_ref, bot_token)

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=123456789,
        display_name="MiniApp Store Bot",
        token_secret_ref=token_ref,
        is_enabled=True,
    )
    session.add(bot)
    await session.flush()

    # Forged hash
    tampered_init_data = (
        f"auth_date={int(time.time())}&query_id=AAHdF6IQAAAAAN0XohDhrOrc"
        f"&user=%7B%22id%22%3A999999%7D&hash=badbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadb"
    )

    resp = await client.post(
        "/api/v1/auth/telegram-miniapp",
        json={
            "init_data": tampered_init_data,
            "bot_id": str(bot.id),
        },
    )
    assert resp.status_code == 401
    assert "Cryptographic verification failed" in resp.json()["detail"]


async def test_telegram_miniapp_deactivated_user_returns_403(api_env: dict[str, Any]) -> None:
    """Deactivated user trying to authenticate via Mini App must return 403 Forbidden."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    bot_token = "123456789:" + "AAHk69MockTelegramBotTokenForTestingAuth"
    token_ref = f"BOT_TOKEN_DEACT_{tenant.id}"
    await secret_storage.set_secret(token_ref, bot_token)

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=123456789,
        display_name="MiniApp Store Bot",
        token_secret_ref=token_ref,
        is_enabled=True,
    )
    session.add(bot)
    await session.flush()

    # Create pre-existing deactivated user with telegram_id
    telegram_id = 9988776655
    await create_user(session, tenant.id, telegram_id=telegram_id, is_active=False)

    init_data = make_telegram_init_data(bot_token, {"id": telegram_id, "username": "banned_user"})

    resp = await client.post(
        "/api/v1/auth/telegram-miniapp",
        json={
            "init_data": init_data,
            "bot_id": str(bot.id),
        },
    )
    assert resp.status_code == 403
    assert "deactivated" in resp.json()["detail"].lower()


# ===========================================================================
# 6. Token Revocation / Versioning Tests
# ===========================================================================

async def test_token_revocation_via_token_version(api_env: dict[str, Any]) -> None:
    """Incrementing user.token_version immediately revokes all previously issued tokens."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    token_service: AuthTokenService = api_env["token_service"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    user, _ = await create_user(session, tenant.id, token_version=1)
    await setup_provider_config(session, secret_storage, tenant.id, "mock")
    order = await create_order(session, tenant.id, user.id, Decimal("50.00"))

    # Issue token v1
    token_v1 = token_service.issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.CUSTOMER],
        source=AuthSource.TEST,
        token_version=1,
    )

    # 1. Token v1 works
    resp_v1_ok = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order.id),
            "provider_name": "mock",
            "idempotency_key": "token_version_test_001",
        },
        headers={"Authorization": f"Bearer {token_v1}"},
    )
    assert resp_v1_ok.status_code == 201
    intent_id = resp_v1_ok.json()["id"]

    # 2. Revoke sessions by incrementing token_version in database
    user.token_version = 2
    await session.flush()

    # 3. Old token v1 is immediately rejected with 401 Unauthorized
    resp_v1_revoked = await client.get(
        f"/api/v1/payments/intents/{intent_id}",
        headers={"Authorization": f"Bearer {token_v1}"},
    )
    assert resp_v1_revoked.status_code == 401
    assert "revoked" in resp_v1_revoked.json()["detail"].lower()

    # 4. New token issued with updated version (v2) succeeds
    token_v2 = token_service.issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.CUSTOMER],
        source=AuthSource.TEST,
        token_version=2,
    )
    resp_v2_ok = await client.get(
        f"/api/v1/payments/intents/{intent_id}",
        headers={"Authorization": f"Bearer {token_v2}"},
    )
    assert resp_v2_ok.status_code == 200
    assert resp_v2_ok.json()["id"] == intent_id


# ===========================================================================
# 7. Webhook Authentication Tests
# ===========================================================================

async def test_webhook_auth_does_not_use_customer_jwt(api_env: dict[str, Any]) -> None:
    """Webhooks do not use customer JWT and require provider cryptographic verification."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    _, token = await create_user(session, tenant.id)
    await setup_provider_config(session, secret_storage, tenant.id, "mock")

    payload = {
        "event_id": "evt_wh_jwt_test",
        "event_type": "payment.succeeded",
        "provider_payment_id": "mock_pay_123",
        "amount": "25.00",
        "currency": "USD",
        "status": "SUCCEEDED",
    }
    payload_bytes = json.dumps(payload).encode("utf-8")

    # Caller presents customer JWT in Authorization header, but no provider signature
    resp = await client.post(
        f"/api/v1/payments/webhooks/{tenant.id}/mock",
        content=payload_bytes,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    # The webhook endpoint ignores customer JWT and rejects with 401 due to missing/invalid signature
    assert resp.status_code == 401
    assert "signature" in resp.json()["detail"].lower()


async def test_webhook_requires_provider_specific_signature(api_env: dict[str, Any]) -> None:
    """Webhooks strictly enforce provider-specific HMAC signature verification."""
    client: httpx.AsyncClient = api_env["client"]
    session: AsyncSession = api_env["session"]
    secret_storage: EnvSecretStorage = api_env["secret_storage"]

    tenant = await create_tenant(session)
    user, token = await create_user(session, tenant.id)
    order = await create_order(session, tenant.id, user.id, Decimal("85.00"))

    webhook_secret = "webhook_crypto_secret_key_888"
    await setup_provider_config(
        session, secret_storage, tenant.id, "mock", webhook_secret=webhook_secret
    )

    # Create intent
    create_resp = await client.post(
        "/api/v1/payments/intents",
        json={
            "order_id": str(order.id),
            "provider_name": "mock",
            "idempotency_key": "wh_crypto_test_001",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 201
    provider_payment_id = create_resp.json()["provider_payment_id"]

    valid_payload = {
        "event_id": "evt_crypto_001",
        "event_type": "payment.succeeded",
        "provider_payment_id": provider_payment_id,
        "amount": "85.00",
        "currency": "USD",
        "status": "SUCCEEDED",
    }
    payload_bytes = json.dumps(valid_payload).encode("utf-8")

    # 1. Missing signature header -> 401 Unauthorized
    resp_no_sig = await client.post(
        f"/api/v1/payments/webhooks/{tenant.id}/mock",
        content=payload_bytes,
        headers={"Content-Type": "application/json"},
    )
    assert resp_no_sig.status_code == 401
    assert "signature" in resp_no_sig.json()["detail"].lower()

    # 2. Corrupt / mismatched signature -> 401 Unauthorized
    resp_bad_sig = await client.post(
        f"/api/v1/payments/webhooks/{tenant.id}/mock",
        content=payload_bytes,
        headers={
            "X-Signature": "bad_signature_deadbeef1234",
            "Content-Type": "application/json",
        },
    )
    assert resp_bad_sig.status_code == 401
    assert "signature" in resp_bad_sig.json()["detail"].lower()

    # 3. Valid HMAC-SHA256 signature -> 200 OK without any JWT header
    valid_sig = hmac.new(webhook_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    resp_valid = await client.post(
        f"/api/v1/payments/webhooks/{tenant.id}/mock",
        content=payload_bytes,
        headers={
            "X-Signature": valid_sig,
            "Content-Type": "application/json",
        },
    )
    assert resp_valid.status_code == 200
    assert resp_valid.json()["status"] == "ok"
    assert resp_valid.json()["processed"] is True
