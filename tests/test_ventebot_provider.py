from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from packages.providers.clients.registry import provider_registry
from packages.providers.clients.ventebot import VenteBotClient
from packages.providers.contracts import ProviderDeliveryKind, ProviderOrderState
from packages.providers.exceptions import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderInsufficientBalanceError,
    ProviderProductUnavailableError,
    ProviderRateLimitError,
)
from packages.providers.interface import ProviderOrderRequest
from packages.providers.models import ProviderCategory, ProviderHealthStatus

pytestmark = pytest.mark.asyncio

SAMPLE_ME = {
    "success": True,
    "user_telegram_id": 987654321,
    "username": "reseller_partner",
    "first_name": "Partner",
    "wallet_balance": 85.50,
    "key_name": "Production Partner Key",
    "key_prefix": "vb_key",
}

SAMPLE_PRODUCTS = {
    "success": True,
    "products": [
        {
            "id": 101,
            "name": "Grok AI 1 Month",
            "description": "Direct activation",
            "price_usd": 6.50,
            "standard_price_usd": 8.00,
            "pricing_type": "reseller_special",
            "delivery_type": "activation",
            "stock": None,
            "warranty_days": 30,
        },
        {
            "id": 102,
            "name": "ChatGPT Plus Shared Account",
            "description": "Pre-made account",
            "price_usd": 15.00,
            "delivery_type": "stock",
            "stock": 12,
            "warranty_days": 14,
        },
    ],
}

SAMPLE_ORDER_COMPLETED = {
    "success": True,
    "status": "ok",
    "idempotent": False,
    "balance_after": 70.50,
    "order": {
        "id": 5501,
        "status": "COMPLETED",
        "product_id": 102,
        "product_name": "ChatGPT Plus Shared Account",
        "quantity": 1,
        "amount_usd": 15.00,
        "delivery_type": "stock",
        "customer_reference": "cust-order-99",
        "idempotency_key": "idem-key-001",
        "created_at": "2026-09-19 12:00:00",
        "items": [
            {
                "id": 8801,
                "account_data": "user_plus@example.com:VerySecretPass99#",
            }
        ],
    },
}

SAMPLE_ORDER_PENDING = {
    "success": True,
    "order": {
        "id": 5502,
        "status": "PAID_PENDING_DELIVERY",
        "product_id": 101,
        "quantity": 1,
        "amount_usd": 6.50,
        "items": [],
    },
}

SAMPLE_ORDER_CANCELLED = {
    "success": True,
    "order": {
        "id": 5503,
        "status": "CANCELLED",
        "product_id": 101,
        "quantity": 1,
        "amount_usd": 6.50,
        "items": [],
    },
}


def create_client(
    api_key: str = "test_vb_api_key",
    transport: httpx.AsyncBaseTransport | None = None,
) -> VenteBotClient:
    return VenteBotClient(
        provider_name="VenteBot",
        config={
            "credentials": {"API_KEY": api_key},
            "base_url": "https://ventetelegrambotrailway-production.up.railway.app",
            "transport": transport,
        },
    )


async def test_ventebot_manifest_and_registration():
    assert "VENTEBOT" in provider_registry.registered_types()
    definition = provider_registry.get_definition("VENTEBOT")
    assert definition.display_name == "VenteBot Reseller API"
    assert definition.supports_category(ProviderCategory.ACCOUNT)
    assert definition.supports_category(ProviderCategory.DIGITAL_PRODUCT)
    assert definition.supports_category(ProviderCategory.SERVICE)
    assert "API_KEY" in definition.required_credential_keys()

    # Missing API_KEY raises configuration error
    with pytest.raises(ProviderConfigurationError, match="API_KEY"):
        VenteBotClient(provider_name="VenteBot", config={})


async def test_ventebot_health_and_balance():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-reseller-key") == "test_vb_api_key"
        assert request.url.path == "/api/reseller/me"
        return httpx.Response(200, json=SAMPLE_ME)

    client = create_client(transport=httpx.MockTransport(handler))
    health = await client.health_check()
    assert health.status == ProviderHealthStatus.HEALTHY
    assert "reseller_partner" in health.message

    balance = await client.get_balance()
    assert balance.balance == Decimal("85.50")
    assert balance.currency == "USD"


async def test_ventebot_catalog_and_product_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/reseller/products"
        return httpx.Response(200, json=SAMPLE_PRODUCTS)

    client = create_client(transport=httpx.MockTransport(handler))
    products = await client.list_products()
    assert len(products) == 2
    assert products[0].external_id == "101"
    assert products[0].name == "Grok AI 1 Month"
    assert products[0].cost == Decimal("6.50")
    assert products[1].stock == 12

    product = await client.get_product("102")
    assert product.name == "ChatGPT Plus Shared Account"
    assert product.cost == Decimal("15.00")

    with pytest.raises(ProviderProductUnavailableError):
        await client.get_product("9999")


async def test_ventebot_create_order_and_delivery_artifacts():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/reseller/orders"
        body = json.loads(request.read().decode("utf-8"))
        assert body["product_id"] == 102
        assert body["quantity"] == 1
        assert body["idempotency_key"] == "attempt-vb-001"
        assert body["activation_identifier"] == "@customer_telegram"
        return httpx.Response(200, json=SAMPLE_ORDER_COMPLETED)

    client = create_client(transport=httpx.MockTransport(handler))
    order_req = ProviderOrderRequest(
        external_product_id="102",
        quantity=1,
        idempotency_key="attempt-vb-001",
        recipient="@customer_telegram",
    )
    response = await client.create_order(order_req)
    assert response.external_order_id == "5501"
    assert response.canonical_state == ProviderOrderState.COMPLETED
    assert response.cost == Decimal("15.00")
    assert len(response.delivery) == 1
    assert response.delivery[0].kind == ProviderDeliveryKind.ACCOUNT
    assert response.delivery[0].value == "user_plus@example.com:VerySecretPass99#"


async def test_ventebot_order_status_checks():
    # Completed order
    client_comp = create_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=SAMPLE_ORDER_COMPLETED)))
    check_comp = await client_comp.get_order("5501")
    assert check_comp.is_completed is True
    assert check_comp.is_failed is False
    assert check_comp.canonical_state == ProviderOrderState.COMPLETED
    assert check_comp.delivery[0].value == "user_plus@example.com:VerySecretPass99#"

    # Pending order
    client_pend = create_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=SAMPLE_ORDER_PENDING)))
    check_pend = await client_pend.get_order("5502")
    assert check_pend.is_completed is False
    assert check_pend.is_failed is False
    assert check_pend.canonical_state == ProviderOrderState.PROCESSING

    # Cancelled order
    client_fail = create_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=SAMPLE_ORDER_CANCELLED)))
    check_fail = await client_fail.get_order("5503")
    assert check_fail.is_completed is False
    assert check_fail.is_failed is True
    assert check_fail.canonical_state == ProviderOrderState.FAILED


async def test_ventebot_error_mappings():
    # 401 Unauthorized
    client_401 = create_client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"code": "INVALID_API_KEY"})))
    with pytest.raises(ProviderAuthenticationError, match="API key"):
        await client_401.get_balance()

    # 402 Insufficient Balance
    client_402 = create_client(transport=httpx.MockTransport(lambda r: httpx.Response(402, json={"code": "INSUFFICIENT_BALANCE"})))
    with pytest.raises(ProviderInsufficientBalanceError, match="balance"):
        await client_402.create_order(ProviderOrderRequest(external_product_id="101", quantity=1, recipient="client"))

    # 429 Rate Limit
    client_429 = create_client(transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "30"}, json={"code": "RATE_LIMIT"})))
    with pytest.raises(ProviderRateLimitError, match="rate limit"):
        await client_429.get_balance()

    # 500 Server Error
    client_500 = create_client(transport=httpx.MockTransport(lambda r: httpx.Response(500, json={"code": "SERVER_ERROR"})))
    with pytest.raises(ProviderError, match="upstream"):
        await client_500.get_balance()


async def test_ventebot_live_openapi_connectivity():
    """Live integration check fetching public OpenAPI document from Railway deployment."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get("https://ventetelegrambotrailway-production.up.railway.app/api/reseller/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("openapi", "").startswith("3.")
        assert data.get("info", {}).get("title") == "VenteBot Reseller API"
        assert "/api/reseller/me" in data.get("paths", {})
        assert "/api/reseller/orders" in data.get("paths", {})
        assert "/api/reseller/products" in data.get("paths", {})
