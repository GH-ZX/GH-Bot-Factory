from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.providers.clients.registry import provider_registry
from packages.providers.clients.spider_service import SpiderServiceClient
from packages.providers.contracts import (
    NumberActivationState,
    NumberReservationRequest,
    ProviderDeliveryKind,
    ProviderOrderState,
)
from packages.providers.exceptions import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInsufficientBalanceError,
    ProviderProductUnavailableError,
)
from packages.providers.http_generic import GenericHttpProvider
from packages.providers.interface import ProviderOrderRequest
from packages.providers.models import ProviderCategory, ProviderHealthStatus

pytestmark = pytest.mark.asyncio


def create_spider_client(
    api_key: str = "test_spider_key",
    transport: httpx.AsyncBaseTransport | None = None,
) -> SpiderServiceClient:
    return SpiderServiceClient(
        provider_name="SpiderService",
        config={
            "credentials": {"API_KEY": api_key},
            "base_url": "https://api.spider-service.com",
            "transport": transport,
        },
    )


async def test_spider_service_manifest_and_registration():
    assert "SPIDER_SERVICE" in provider_registry.registered_types()
    definition = provider_registry.get_definition("SPIDER_SERVICE")
    assert definition.display_name == "Spider Service (SMS & Virtual Numbers)"
    assert definition.supports_category(ProviderCategory.NUMBER)
    assert definition.supports_category(ProviderCategory.SERVICE)
    assert "API_KEY" in definition.required_credential_keys()

    with pytest.raises(ProviderConfigurationError, match="API_KEY"):
        SpiderServiceClient(provider_name="SpiderService", config={})


async def test_spider_service_health_and_balance():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("apiKay") == "test_spider_key"
        assert request.url.params.get("action") == "getBalance"
        return httpx.Response(200, json={"ok": True, "result": {"balance": 15.75}})

    client = create_spider_client(transport=httpx.MockTransport(handler))
    health = await client.health_check()
    assert health.status == ProviderHealthStatus.HEALTHY
    assert "Connected" in health.message

    balance = await client.get_balance()
    assert balance.balance == Decimal("15.75")
    assert balance.currency == "USD"


async def test_spider_service_countries():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("action") == "getCountrys"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "currency": "USD",
                    "countries": {
                        "1": {
                            "PS": "1.00",
                            "SA": "0.80",
                        }
                    },
                },
            },
        )

    client = create_spider_client(transport=httpx.MockTransport(handler))
    countries = await client.list_number_countries()
    assert len(countries) == 2
    assert countries[0].code == "PS"
    assert countries[0].name == "Palestine"
    assert countries[0].metadata["price"] == "1.00"
    assert countries[1].code == "SA"
    assert countries[1].name == "Saudi Arabia"

    products = await client.list_products()
    assert len(products) == 2
    assert products[0].external_id == "PS"
    assert products[0].cost == Decimal("1.00")


async def test_spider_service_number_reserve_and_activation():
    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params.get("action")
        if action == "getNumber":
            assert request.url.params.get("country") == "PS"
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "number": "+970599123456",
                        "hash_code": "hash_palestine_001",
                    },
                },
            )
        if action == "getCode":
            hash_code = request.url.params.get("hash_code")
            if hash_code == "hash_waiting":
                return httpx.Response(200, json={"ok": False, "error": "WAIT_CODE", "msg": "error"})
            return httpx.Response(200, json={"ok": True, "result": {"code": "882194"}})
        return httpx.Response(404)

    client = create_spider_client(transport=httpx.MockTransport(handler))

    # 1. Reserve number
    req = NumberReservationRequest(service="telegram", country="PS")
    res = await client.reserve_number(req)
    assert res.external_order_id == "hash_palestine_001"
    assert res.phone_number == "+970599123456"
    assert res.state == NumberActivationState.WAITING_SMS

    # 2. Get code when waiting
    poll_waiting = await client.get_number_activation("hash_waiting")
    assert poll_waiting.state == NumberActivationState.WAITING_SMS

    # 3. Get code when received
    poll_done = await client.get_number_activation("hash_palestine_001")
    assert poll_done.state == NumberActivationState.SMS_RECEIVED
    assert len(poll_done.messages) == 1
    assert poll_done.messages[0].code == "882194"

    # 4. Standard BaseProviderClient order methods
    order_res = await client.create_order(ProviderOrderRequest(external_product_id="PS", quantity=1, recipient=""))
    assert order_res.external_order_id == "hash_palestine_001"
    assert order_res.canonical_state == ProviderOrderState.PROCESSING
    assert order_res.delivery[0].kind == ProviderDeliveryKind.PHONE_NUMBER
    assert order_res.delivery[0].value == "+970599123456"

    order_check = await client.get_order("hash_palestine_001")
    assert order_check.is_completed is True
    assert order_check.delivery[0].kind == ProviderDeliveryKind.CODE
    assert order_check.delivery[0].value == "882194"


async def test_spider_service_error_handling():
    # 1. Invalid key (BAD_KEY)
    client_bad = create_spider_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": False, "error": "BAD_KEY", "msg": "error"})))
    with pytest.raises(ProviderAuthenticationError, match="Invalid Spider Service API key"):
        await client_bad.get_balance()

    # 2. Low balance (NO_BALANCE)
    client_bal = create_spider_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": False, "error": "NO_BALANCE", "msg": "error"})))
    with pytest.raises(ProviderInsufficientBalanceError, match="insufficient balance"):
        await client_bal.reserve_number(NumberReservationRequest(service="tg", country="PS"))

    # 3. No numbers (NO_NUMBER)
    client_num = create_spider_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": False, "error": "NO_NUMBER", "msg": "error"})))
    with pytest.raises(ProviderProductUnavailableError, match="No numbers available"):
        await client_num.reserve_number(NumberReservationRequest(service="tg", country="PS"))


async def test_generic_http_provider_supports_spider_service():
    """Verify that GenericHttpProvider (HTTP_OPENAPI) can also be used for Spider Service via response.success_field."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("apiKay") == "generic_spider_key"
        action = request.url.params.get("action")
        if action == "getBalance":
            return httpx.Response(200, json={"ok": True, "result": {"balance": 45.00}})
        if action == "badAuth":
            return httpx.Response(200, json={"ok": False, "error": "BAD_KEY", "msg": "error"})
        return httpx.Response(404)

    generic_cfg = {
        "http_adapter": {
            "base_url": "https://api.spider-service.com",
            "auth": [{"source": "API_KEY", "location": "query", "name": "apiKay"}],
            "operations": {
                "BALANCE": {
                    "method": "GET",
                    "path": "/",
                    "constants_query": {"action": "getBalance"},
                    "response": {
                        "success_field": "ok",
                        "error_field": "error",
                        "fields": {"balance": "result.balance"},
                    },
                },
                "HEALTH": {
                    "method": "GET",
                    "path": "/",
                    "constants_query": {"action": "badAuth"},
                    "response": {
                        "success_field": "ok",
                        "error_field": "error",
                    },
                },
            },
        },
        "credentials": {"API_KEY": "generic_spider_key"},
        "transport": httpx.MockTransport(handler),
    }

    provider = GenericHttpProvider(provider_name="GenericSpider", config=generic_cfg)

    # 1. Success reading balance
    bal = await provider.get_balance()
    assert bal.balance == Decimal("45.00")

    # 2. Error detection on HTTP 200 payload
    health = await provider.health_check()
    assert health.status == ProviderHealthStatus.UNAVAILABLE
    assert "BAD_KEY" in health.message


async def test_reconciliation_activation_timeout_auto_refund(db_session: AsyncSession):
    """Verify that virtual number activations waiting for SMS beyond timeout are auto-cancelled and refunded."""
    import uuid
    from datetime import UTC, datetime, timedelta

    from packages.commerce.models import Order
    from packages.commerce.state_machine import OrderStatus
    from packages.fulfillment.models import FulfillmentAttempt, FulfillmentStatus
    from packages.fulfillment.reconciliation import ReconciliationService
    from packages.payments.service import LedgerService
    from packages.providers.models import Provider
    from packages.tenants.models import Tenant, User

    tenant = Tenant(name="Spider SMS Store", slug=f"spider-store-{uuid.uuid4().hex[:6]}")
    user = User(username=f"shopper_{uuid.uuid4().hex[:6]}")
    db_session.add_all([tenant, user])
    await db_session.flush()

    # User wallet funded with $10
    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, "USD")
    await LedgerService.credit(db_session, wallet, Decimal("10.00"), reference_id="seed-wallet", reference_type="TOPUP")

    # Order placed for $1.00 virtual number
    order = Order(
        tenant_id=tenant.id,
        user_id=user.id,
        order_number=f"ORD-SPIDER-{uuid.uuid4().hex[:6].upper()}",
        status=OrderStatus.PROCESSING,
        currency="USD",
        total_amount=Decimal("1.00"),
    )
    db_session.add(order)
    await db_session.flush()

    # Provider record for Spider Service with 60s test timeout
    provider = Provider(
        tenant_id=tenant.id,
        name="Spider-Auto-Refund",
        slug=f"spider-auto-refund-{uuid.uuid4().hex[:6]}",
        provider_type="SPIDER_SERVICE",
        category=ProviderCategory.NUMBER,
        is_enabled=True,
        metadata_json={"activation_timeout_seconds": 60},
    )
    db_session.add(provider)
    await db_session.flush()

    from packages.providers.models import ProviderCredential
    from packages.providers.router import ProviderRouter
    from packages.telegram.secrets import EnvSecretStorage

    secret_storage = EnvSecretStorage({"SPIDER_KEY_REF": "test_spider_key"})
    db_session.add(
        ProviderCredential(
            tenant_id=tenant.id,
            provider_id=provider.id,
            credential_type="API_KEY",
            secret_ref="SPIDER_KEY_REF",
        )
    )
    await db_session.flush()
    # Create active attempt started 120s ago (exceeding 60s timeout)
    attempt = FulfillmentAttempt(
        tenant_id=tenant.id,
        order_id=order.id,
        provider_id=provider.id,
        attempt_number=1,
        idempotency_key=f"order:{order.id}:attempt:1",
        external_order_id="hash_spider_pending_123",
        status=FulfillmentStatus.PROCESSING,
        cost_amount=Decimal("1.00"),
        cost_currency="USD",
        started_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    db_session.add(attempt)
    await db_session.commit()

    # Mock Spider Service client returning WAIT_CODE
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "WAIT_CODE", "msg": "error"})

    client = create_spider_client(transport=httpx.MockTransport(handler))
    provider_registry.register_singleton(str(provider.id), client)

    # Reconcile attempt
    router = ProviderRouter(registry=provider_registry, secret_storage=secret_storage)
    reconciliation = ReconciliationService(registry=provider_registry, provider_router=router)
    discrepancies = await reconciliation.reconcile_order(db_session, tenant.id, order.id)

    assert len(discrepancies) == 1
    assert discrepancies[0].issue_type == "ACTIVATION_TIMED_OUT_REFUNDED"

    # Verify attempt and order are FAILED
    await db_session.refresh(attempt)
    await db_session.refresh(order)
    assert attempt.status == FulfillmentStatus.FAILED
    assert attempt.error_classification == "UPSTREAM_ACTIVATION_TIMEOUT"
    assert order.status == OrderStatus.FAILED

    # Verify customer wallet received automated refund (balance is $10.00 + $1.00 = $11.00)
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("11.00")
