from __future__ import annotations

import json
import uuid
from decimal import Decimal

import httpx
import pytest

from packages.providers.clients.g2bulk import G2BulkClient
from packages.providers.clients.registry import provider_registry
from packages.providers.contracts import ProviderDeliveryKind, ProviderOrderState
from packages.providers.exceptions import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInsufficientBalanceError,
    ProviderRateLimitError,
)
from packages.providers.interface import ProviderOrderRequest
from packages.providers.models import ProviderCategory, ProviderHealthStatus

pytestmark = pytest.mark.asyncio

SAMPLE_ME = {
    "success": True,
    "user_id": 99887766,
    "username": "pro_gamer_shop",
    "first_name": "Pro Gamer",
    "balance": 150.75,
}

SAMPLE_PRODUCTS = {
    "success": True,
    "products": [
        {
            "id": 1,
            "title": "60 UC Voucher",
            "category_title": "PUBG Mobile UC Vouchers",
            "unit_price": 0.84,
            "face_value": 1,
            "stock": 500,
            "description": "Redeem on Midasbuy",
        },
        {
            "id": 2,
            "title": "PSN $20 USA Card",
            "category_title": "PlayStation Network USA",
            "unit_price": 18.90,
            "face_value": 20,
            "stock": 25,
            "description": "US PlayStation Store",
        },
    ],
}

SAMPLE_PURCHASE_COMPLETED = {
    "success": True,
    "order_id": 4001,
    "transaction_id": 9001,
    "product_id": 1,
    "product_title": "60 UC Voucher",
    "total_price": "0.84",
    "status": "COMPLETED",
    "delivery_items": ["PUBG-UC-60-CODE-9988"],
}

SAMPLE_PURCHASE_PENDING = {
    "success": True,
    "order_id": 4002,
    "transaction_id": 9002,
    "product_id": 2,
    "product_title": "PSN $20 USA Card",
    "total_price": "18.90",
    "status": "PENDING",
    "delivery_items": None,
    "poll_url": "/v1/orders/4002/delivery",
}


def create_g2bulk_client(
    api_key: str = "test_g2bulk_key",
    transport: httpx.AsyncBaseTransport | None = None,
) -> G2BulkClient:
    return G2BulkClient(
        provider_name="G2Bulk",
        config={
            "credentials": {"API_KEY": api_key},
            "base_url": "https://api.g2bulk.com/v1",
            "transport": transport,
        },
    )


async def test_g2bulk_manifest_and_registration():
    assert "G2BULK" in provider_registry.registered_types()
    definition = provider_registry.get_definition("G2BULK")
    assert definition.display_name == "G2Bulk (Gaming Top-Ups & Vouchers)"
    assert definition.supports_category(ProviderCategory.DIGITAL_PRODUCT)
    assert definition.supports_category(ProviderCategory.GIFT)
    assert definition.supports_category(ProviderCategory.SERVICE)
    assert definition.supports_category(ProviderCategory.ACCOUNT)
    assert "API_KEY" in definition.required_credential_keys()

    with pytest.raises(ProviderConfigurationError, match="API_KEY"):
        G2BulkClient(provider_name="G2Bulk", config={})

async def test_g2bulk_uuid_idempotency_formatting():
    # Arbitrary non-UUID string formats into valid 36-char UUID
    key1 = "order:123:attempt:1"
    uuid1 = G2BulkClient._format_uuid_idempotency_key(key1)
    assert len(uuid1) == 36
    assert str(uuid.UUID(uuid1)) == uuid1

    # Deterministic: same input yields exact same UUID
    uuid2 = G2BulkClient._format_uuid_idempotency_key(key1)
    assert uuid1 == uuid2

    # Already valid UUID is preserved
    valid_uuid = str(uuid.uuid4())
    assert G2BulkClient._format_uuid_idempotency_key(valid_uuid) == valid_uuid


async def test_g2bulk_health_and_balance():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-api-key") == "test_g2bulk_key"
        assert request.url.path == "/v1/getMe"
        return httpx.Response(200, json=SAMPLE_ME)

    client = create_g2bulk_client(transport=httpx.MockTransport(handler))
    health = await client.health_check()
    assert health.status == ProviderHealthStatus.HEALTHY
    assert "pro_gamer_shop" in health.message

    balance = await client.get_balance()
    assert balance.balance == Decimal("150.75")
    assert balance.currency == "USD"


async def test_g2bulk_catalog_and_product_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/products/1":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "product": SAMPLE_PRODUCTS["products"][0],
                },
            )
        assert request.url.path == "/v1/products"
        return httpx.Response(200, json=SAMPLE_PRODUCTS)

    client = create_g2bulk_client(transport=httpx.MockTransport(handler))
    products = await client.list_products()
    assert len(products) == 2
    assert products[0].external_id == "1"
    assert "PUBG Mobile" in products[0].name
    assert products[0].cost == Decimal("0.84")
    assert products[0].stock == 500

    single = await client.get_product("1")
    assert single.external_id == "1"
    assert single.cost == Decimal("0.84")


async def test_g2bulk_purchase_instant_completed():
    captured_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers["idempotency"] = request.headers.get("x-idempotency-key")
        assert request.method == "POST"
        assert request.url.path == "/v1/products/1/purchase"
        body = json.loads(request.read().decode("utf-8"))
        assert body["quantity"] == 2
        return httpx.Response(200, json=SAMPLE_PURCHASE_COMPLETED)

    client = create_g2bulk_client(transport=httpx.MockTransport(handler))
    order_req = ProviderOrderRequest(
        external_product_id="1",
        quantity=2,
        idempotency_key="order:test:attempt:1",
        recipient="player_uid",
    )
    res = await client.create_order(order_req)
    assert res.external_order_id == "4001"
    assert res.canonical_state == ProviderOrderState.COMPLETED
    assert res.cost == Decimal("0.84")
    assert len(res.delivery) == 1
    assert res.delivery[0].kind == ProviderDeliveryKind.CODE
    assert res.delivery[0].value == "PUBG-UC-60-CODE-9988"
    assert len(captured_headers["idempotency"]) == 36


async def test_g2bulk_purchase_pending_and_delivery_polling():
    def purchase_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SAMPLE_PURCHASE_PENDING)

    client = create_g2bulk_client(transport=httpx.MockTransport(purchase_handler))
    order_req = ProviderOrderRequest(
        external_product_id="2",
        quantity=1,
        idempotency_key=str(uuid.uuid4()),
        recipient="gamer",
    )
    pending_res = await client.create_order(order_req)
    assert pending_res.external_order_id == "4002"
    assert pending_res.canonical_state == ProviderOrderState.PROCESSING

    # 1. Poll while processing (HTTP 202)
    client_202 = create_g2bulk_client(transport=httpx.MockTransport(lambda r: httpx.Response(202, json={"status": "PROCESSING"})))
    check_202 = await client_202.get_order("4002")
    assert check_202.is_completed is False
    assert check_202.canonical_state == ProviderOrderState.PROCESSING

    # 2. Poll when ready (HTTP 200)
    ready_data = {
        "success": True,
        "order_id": 4002,
        "status": "COMPLETED",
        "delivery_items": ["PSN-XXXX-YYYY-ZZZZ"],
    }
    client_200 = create_g2bulk_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=ready_data)))
    check_200 = await client_200.get_order("4002")
    assert check_200.is_completed is True
    assert check_200.delivery[0].value == "PSN-XXXX-YYYY-ZZZZ"

    # 3. Poll when refunded/terminal failure (HTTP 410)
    refund_data = {"success": False, "status": "REFUNDED", "message": "Order failed and was refunded automatically."}
    client_410 = create_g2bulk_client(transport=httpx.MockTransport(lambda r: httpx.Response(410, json=refund_data)))
    check_410 = await client_410.get_order("4002")
    assert check_410.is_failed is True
    assert check_410.canonical_state == ProviderOrderState.FAILED


async def test_g2bulk_error_handling():
    # 401 Unauthorized
    client_401 = create_g2bulk_client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "Invalid key"})))
    with pytest.raises(ProviderAuthenticationError, match="API key"):
        await client_401.get_balance()

    # 402 Insufficient Balance
    client_402 = create_g2bulk_client(transport=httpx.MockTransport(lambda r: httpx.Response(402, json={"error": "Low balance"})))
    with pytest.raises(ProviderInsufficientBalanceError, match="balance"):
        await client_402.create_order(ProviderOrderRequest(external_product_id="1", quantity=1, recipient=""))

    # 429 Rate Limit
    client_429 = create_g2bulk_client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    with pytest.raises(ProviderRateLimitError, match="rate limit"):
        await client_429.get_balance()


async def test_g2bulk_live_public_catalog_connectivity():
    """Verify live connectivity with G2Bulk public catalog endpoint."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get("https://api.g2bulk.com/v1/category")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert len(data.get("categories", [])) > 50
