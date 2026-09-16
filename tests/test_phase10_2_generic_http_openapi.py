from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from packages.providers.catalog import ProviderCapability
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.contracts import NumberActivationState, NumberReservationRequest
from packages.providers.exceptions import ProviderConfigurationError
from packages.providers.http_generic import (
    GenericHttpProvider,
    inspect_openapi_document,
    parse_generic_http_config,
)
from packages.providers.interface import ProviderOrderRequest
from packages.providers.models import ProviderCategory


def _generic_metadata(base_url: str = "https://supplier.example") -> dict[str, object]:
    return {
        "http_adapter": {
            "base_url": base_url,
            "auth": [
                {
                    "source": "API_KEY",
                    "location": "header",
                    "name": "X-Api-Key",
                }
            ],
            "operations": {
                "BALANCE": {
                    "method": "GET",
                    "path": "/balance",
                    "response": {"fields": {"balance": "data.balance", "currency": "data.currency"}},
                },
                "CATALOG": {
                    "method": "GET",
                    "path": "/products",
                    "response": {
                        "root": "data.items",
                        "fields": {
                            "external_id": "id",
                            "name": "title",
                            "cost": "price",
                            "currency": "currency",
                            "stock": "stock",
                        },
                    },
                },
                "CREATE_ORDER": {
                    "method": "POST",
                    "path": "/orders",
                    "json_body": [
                        {"source": "external_product_id", "target": "product", "required": True},
                        {"source": "quantity", "target": "qty", "required": True},
                        {"source": "recipient", "target": "target", "required": True},
                        {"source": "idempotency_key", "target": "request_id", "required": True},
                    ],
                    "response": {
                        "fields": {
                            "external_order_id": "order.id",
                            "status": "order.state",
                            "cost": "order.cost",
                        },
                        "status_map": {"ok": "COMPLETED"},
                    },
                },
                "ORDER_STATUS": {
                    "method": "GET",
                    "path": "/orders/{id}",
                    "path_params": [
                        {"source": "external_order_id", "target": "id", "required": True}
                    ],
                    "response": {
                        "fields": {
                            "external_order_id": "order.id",
                            "status": "order.state",
                        },
                        "status_map": {"ok": "COMPLETED"},
                    },
                },
                "NUMBER_SERVICES": {
                    "method": "GET",
                    "path": "/numbers/services",
                    "response": {
                        "root": "services",
                        "fields": {"code": "code", "name": "name"},
                    },
                },
                "NUMBER_COUNTRIES": {
                    "method": "GET",
                    "path": "/numbers/countries",
                    "query": [{"source": "service", "target": "service"}],
                    "response": {
                        "root": "countries",
                        "fields": {"code": "code", "name": "name", "dial_code": "dial"},
                    },
                },
                "NUMBER_OFFERS": {
                    "method": "GET",
                    "path": "/numbers/offers",
                    "query": [
                        {"source": "service", "target": "service", "required": True},
                        {"source": "country", "target": "country", "required": True},
                    ],
                    "response": {
                        "root": "offers",
                        "fields": {
                            "service": "service",
                            "country": "country",
                            "cost": "price",
                            "currency": "currency",
                            "available_quantity": "stock",
                            "provider_offer_id": "id",
                        },
                    },
                },
                "NUMBER_RESERVE": {
                    "method": "POST",
                    "path": "/numbers",
                    "json_body": [
                        {"source": "service", "target": "service", "required": True},
                        {"source": "country", "target": "country", "required": True},
                        {"source": "idempotency_key", "target": "request_id", "required": True},
                    ],
                    "response": {
                        "fields": {
                            "external_order_id": "activation.id",
                            "status": "activation.state",
                            "phone_number": "activation.number",
                            "cost": "activation.cost",
                            "currency": "activation.currency",
                        },
                        "status_map": {"waiting": "WAITING_SMS"},
                    },
                },
                "NUMBER_ACTIVATION": {
                    "method": "GET",
                    "path": "/numbers/{id}",
                    "path_params": [
                        {"source": "external_order_id", "target": "id", "required": True}
                    ],
                    "response": {
                        "fields": {
                            "external_order_id": "activation.id",
                            "status": "activation.state",
                            "phone_number": "activation.number",
                            "messages": "activation.messages",
                        },
                        "status_map": {"sms": "SMS_RECEIVED"},
                    },
                },
            },
        }
    }


def test_openapi_inspection_is_inventory_only_and_rejects_refs() -> None:
    document = {
        "openapi": "3.0.3",
        "info": {"title": "Reseller API", "version": "1"},
        "paths": {
            "/balance": {
                "get": {"operationId": "getBalance", "summary": "Balance"},
            },
            "/orders": {"post": {"operationId": "createOrder"}},
        },
    }
    result = inspect_openapi_document(document)
    assert result.version == "3.0.3"
    assert result.title == "Reseller API"
    assert {(item.operation_id, item.method) for item in result.operations} == {
        ("getBalance", "GET"),
        ("createOrder", "POST"),
    }

    with pytest.raises(ProviderConfigurationError):
        inspect_openapi_document(
            {
                "openapi": "3.0.0",
                "paths": {"/unsafe": {"$ref": "https://evil.example/path.json"}},
            }
        )


def test_generic_http_config_rejects_private_targets_and_exposes_dynamic_capabilities() -> None:
    with pytest.raises(ProviderConfigurationError):
        parse_generic_http_config(_generic_metadata("http://127.0.0.1:9000"))

    registry = ProviderClientRegistry()
    metadata = _generic_metadata()
    registry.validate_config("HTTP_OPENAPI", metadata, ProviderCategory.NUMBER)
    capabilities = set(
        registry.capabilities_for("HTTP_OPENAPI", ProviderCategory.NUMBER, metadata)
    )
    assert ProviderCapability.BALANCE in capabilities
    assert ProviderCapability.NUMBER_RESERVE in capabilities
    assert ProviderCapability.NUMBER_FINISH not in capabilities
    assert registry.required_credentials_for("HTTP_OPENAPI", metadata) == {"API_KEY"}


@pytest.mark.asyncio
async def test_generic_http_maps_catalog_orders_and_number_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("X-Api-Key")))
        if request.url.path == "/balance":
            return httpx.Response(200, json={"data": {"balance": "42.50", "currency": "USD"}})
        if request.url.path == "/products":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "items": [
                            {
                                "id": "prod-1",
                                "title": "Example",
                                "price": "1.25",
                                "currency": "USD",
                                "stock": 7,
                            }
                        ]
                    }
                },
            )
        if request.url.path == "/orders" and request.method == "POST":
            payload = json.loads(request.content)
            assert payload["request_id"] == "idem-1"
            return httpx.Response(
                200,
                json={"order": {"id": "ord-1", "state": "ok", "cost": "1.25"}},
            )
        if request.url.path == "/orders/ord-1":
            return httpx.Response(200, json={"order": {"id": "ord-1", "state": "ok"}})
        if request.url.path == "/numbers/services":
            return httpx.Response(200, json={"services": [{"code": "telegram", "name": "Telegram"}]})
        if request.url.path == "/numbers/countries":
            return httpx.Response(
                200,
                json={"countries": [{"code": "US", "name": "United States", "dial": "+1"}]},
            )
        if request.url.path == "/numbers/offers":
            return httpx.Response(
                200,
                json={
                    "offers": [
                        {
                            "id": "offer-1",
                            "service": "telegram",
                            "country": "US",
                            "price": "0.80",
                            "currency": "USD",
                            "stock": 12,
                        }
                    ]
                },
            )
        if request.url.path == "/numbers" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "activation": {
                        "id": "act-1",
                        "state": "waiting",
                        "number": "+15550001111",
                        "cost": "0.80",
                        "currency": "USD",
                    }
                },
            )
        if request.url.path == "/numbers/act-1":
            return httpx.Response(
                200,
                json={
                    "activation": {
                        "id": "act-1",
                        "state": "sms",
                        "number": "+15550001111",
                        "messages": [
                            {
                                "code": "654321",
                                "text": "Code: 654321",
                                "sender": "Telegram",
                            }
                        ],
                    }
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    async def no_dns_check(host: str) -> None:
        assert host == "supplier.example"

    monkeypatch.setattr("packages.providers.http_generic.httpx.AsyncClient", client_factory)
    monkeypatch.setattr("packages.providers.http_generic.assert_resolved_host_safe", no_dns_check)

    client = GenericHttpProvider(
        "Mapped Supplier",
        config={**_generic_metadata(), "credentials": {"API_KEY": "vault-secret"}},
    )
    balance = await client.get_balance()
    assert balance.balance == Decimal("42.50")
    products = await client.list_products()
    assert products[0].external_id == "prod-1" and products[0].stock == 7

    created = await client.create_order(
        ProviderOrderRequest(
            external_product_id="prod-1",
            quantity=1,
            recipient="buyer",
            idempotency_key="idem-1",
        )
    )
    assert created.external_order_id == "ord-1"
    assert created.canonical_state.value == "COMPLETED"
    checked = await client.get_order("ord-1")
    assert checked.is_completed is True

    services = await client.list_number_services()
    countries = await client.list_number_countries("telegram")
    offers = await client.list_number_offers(service="telegram", country="US")
    assert services[0].code == "telegram"
    assert countries[0].code == "US"
    assert offers[0].cost == Decimal("0.80")

    activation = await client.reserve_number(
        NumberReservationRequest(
            service="telegram",
            country="US",
            idempotency_key="number-1",
        )
    )
    assert activation.state == NumberActivationState.WAITING_SMS
    status = await client.get_number_activation("act-1")
    assert status.state == NumberActivationState.SMS_RECEIVED
    assert status.messages[0].code == "654321"

    assert seen
    assert all(secret == "vault-secret" for _, _, secret in seen)


@pytest.mark.asyncio
async def test_generic_http_redacts_credentials_from_persistable_raw_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _generic_metadata()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orders":
            return httpx.Response(
                200,
                json={
                    "order": {
                        "id": "ord-secret",
                        "state": "ok",
                        "cost": "1.25",
                        "echo": "credential=vault-secret",
                        "nested": {"token": "vault-secret"},
                    }
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    async def no_dns_check(host: str) -> None:
        assert host == "supplier.example"

    monkeypatch.setattr("packages.providers.http_generic.httpx.AsyncClient", client_factory)
    monkeypatch.setattr("packages.providers.http_generic.assert_resolved_host_safe", no_dns_check)
    client = GenericHttpProvider(
        "Mapped Supplier",
        config={**metadata, "credentials": {"API_KEY": "vault-secret"}},
    )
    created = await client.create_order(
        ProviderOrderRequest(
            external_product_id="prod-1",
            quantity=1,
            recipient="buyer",
            idempotency_key="idem-secret",
        )
    )
    serialized = json.dumps(created.raw_data)
    assert "vault-secret" not in serialized
    assert "[REDACTED]" in serialized


@pytest.mark.asyncio
async def test_generic_http_blocks_redirects_oversized_bodies_and_private_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packages.providers.exceptions import ProviderError
    from packages.providers.http_generic import assert_resolved_host_safe

    metadata = _generic_metadata()
    metadata["http_adapter"]["max_response_bytes"] = 1024  # type: ignore[index]
    transport_mode = {"value": "redirect"}

    def handler(request: httpx.Request) -> httpx.Response:
        if transport_mode["value"] == "redirect":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})
        return httpx.Response(200, content=b"x" * 2048, headers={"content-type": "application/json"})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    async def no_dns_check(host: str) -> None:
        assert host == "supplier.example"

    monkeypatch.setattr("packages.providers.http_generic.httpx.AsyncClient", client_factory)
    monkeypatch.setattr("packages.providers.http_generic.assert_resolved_host_safe", no_dns_check)
    client = GenericHttpProvider(
        "Mapped Supplier",
        config={**metadata, "credentials": {"API_KEY": "vault-secret"}},
    )
    with pytest.raises(ProviderConfigurationError):
        await client.get_balance()

    transport_mode["value"] = "large"
    with pytest.raises(ProviderError, match="response-size limit"):
        await client.get_balance()

    monkeypatch.setattr("packages.providers.http_generic.settings.provider_http_allow_private_networks", False)
    monkeypatch.setattr(
        "packages.providers.http_generic.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )
    with pytest.raises(ProviderConfigurationError, match="private"):
        await assert_resolved_host_safe("rebinding.example")


def test_generic_http_production_requires_https_and_host_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packages.providers.http_generic import validate_http_base_url

    monkeypatch.setattr("packages.providers.http_generic.settings.app_env", "production")
    monkeypatch.setattr(
        "packages.providers.http_generic.settings.provider_http_allowed_hosts",
        "api.good.example,*.trusted.example",
    )
    assert validate_http_base_url("https://api.good.example/v1") == "https://api.good.example/v1"
    assert validate_http_base_url("https://eu.trusted.example") == "https://eu.trusted.example"
    with pytest.raises(ValueError, match="HTTPS"):
        validate_http_base_url("http://api.good.example")
    with pytest.raises(ValueError, match="allowlisted"):
        validate_http_base_url("https://not-allowed.example")
