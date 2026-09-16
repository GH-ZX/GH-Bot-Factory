from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from packages.payments.exceptions import PaymentProviderError, UnsupportedProviderCapabilityError
from packages.payments.providers.interface import PaymentCreateRequest, PaymentRefundRequest
from packages.payments.providers.triplea import TripleAPaymentProvider
from packages.payments.state_machine import PaymentIntentStatus


def _credentials() -> str:
    return json.dumps(
        {
            "client_id": "oacid-test-client",
            "client_secret": "secret-value",
            "merchant_key": "mkey-test-merchant",
        }
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.mark.asyncio
async def test_triplea_oauth_create_payment_and_token_cache_are_sanitized() -> None:
    token_calls = 0
    payment_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, payment_calls
        if request.url.path == "/api/v2/oauth/token":
            token_calls += 1
            body = request.content.decode()
            assert "client_id=oacid-test-client" in body
            assert "client_secret=secret-value" in body
            assert "grant_type=client_credentials" in body
            return httpx.Response(200, json={"access_token": "access-token", "expires_in": 3600})
        if request.url.path == "/api/v2/payment":
            payment_calls += 1
            assert request.headers["authorization"] == "Bearer access-token"
            body = json.loads(request.content)
            assert body["merchant_key"] == "mkey-test-merchant"
            assert body["type"] == "triplea"
            assert body["order_currency"] == "USD"
            assert Decimal(body["order_amount"]) == Decimal("25.50")
            assert body["order_id"] == "intent-123"
            assert body["sandbox"] is True
            return httpx.Response(
                200,
                json={
                    "payment_reference": f"PAY-{payment_calls}",
                    "order_currency": "USD",
                    "order_amount": "25.5",
                    "expiry_date": "2026-09-16T02:00:00Z",
                    "hosted_url": "https://triple-a.io/pay/example",
                    "access_token": "must-not-leak",
                    "notify_secret": "must-not-leak",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with _client(handler) as client:
        provider = TripleAPaymentProvider(
            settings={"sandbox": True, "timeout_seconds": 5},
            credentials=_credentials(),
            http_client=client,
        )
        first = await provider.create_payment(
            PaymentCreateRequest(
                order_id=None,
                amount=Decimal("25.50"),
                currency="USD",
                idempotency_key="triplea-1",
                metadata={"payment_intent_id": "intent-123"},
            )
        )
        second = await provider.create_payment(
            PaymentCreateRequest(
                order_id=None,
                amount=Decimal("25.50"),
                currency="USD",
                idempotency_key="triplea-2",
                metadata={"payment_intent_id": "intent-123"},
            )
        )

    assert token_calls == 1
    assert payment_calls == 2
    assert first.provider_payment_id == "PAY-1"
    assert second.provider_payment_id == "PAY-2"
    assert first.status == PaymentIntentStatus.PENDING
    assert first.checkout_url == "https://triple-a.io/pay/example"
    assert "access_token" not in first.raw_data
    assert "notify_secret" not in first.raw_data
    assert first.verification_attributes == {
        "order_currency": "usd",
        "order_amount": "25.5",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_status", "expected"),
    [
        ("new", PaymentIntentStatus.PENDING),
        ("paid", PaymentIntentStatus.PROCESSING),
        ("confirmed", PaymentIntentStatus.PROCESSING),
        ("good", PaymentIntentStatus.SUCCEEDED),
        ("short", PaymentIntentStatus.UNKNOWN),
        ("invalid", PaymentIntentStatus.FAILED),
        ("expired", PaymentIntentStatus.EXPIRED),
        ("cancel", PaymentIntentStatus.CANCELLED),
        ("refunded", PaymentIntentStatus.UNKNOWN),
        ("future_status", PaymentIntentStatus.UNKNOWN),
    ],
)
async def test_triplea_status_mapping_is_fail_closed(provider_status, expected) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/token":
            return httpx.Response(200, json={"access_token": "access-token", "expires_in": 3600})
        if request.url.path == "/api/v2/payment/PAY-REF":
            assert request.url.params["verbose"] == "0"
            return httpx.Response(
                200,
                json={
                    "payment_reference": "PAY-REF",
                    "order_currency": "USD",
                    "order_amount": "10.00",
                    "status": provider_status,
                    "access_token": "must-not-leak",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with _client(handler) as client:
        provider = TripleAPaymentProvider(
            settings={}, credentials=_credentials(), http_client=client
        )
        details = await provider.get_payment("PAY-REF")

    assert details.status == expected
    assert details.amount == Decimal("10.00")
    assert details.currency == "USD"
    assert "access_token" not in details.raw_data


@pytest.mark.asyncio
async def test_triplea_create_rejects_authoritative_amount_mutation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/oauth/token":
            return httpx.Response(200, json={"access_token": "access-token", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "payment_reference": "PAY-MUTATED",
                "order_currency": "USD",
                "order_amount": "9.00",
                "hosted_url": "https://triple-a.io/pay/example",
            },
        )

    async with _client(handler) as client:
        provider = TripleAPaymentProvider(settings={}, credentials=_credentials(), http_client=client)
        with pytest.raises(PaymentProviderError, match="order amount"):
            await provider.create_payment(
                PaymentCreateRequest(
                    order_id=None,
                    amount=Decimal("10.00"),
                    currency="USD",
                    idempotency_key="triplea-mutation",
                )
            )


@pytest.mark.asyncio
async def test_triplea_webhooks_and_refunds_fail_closed_until_verified_contract() -> None:
    provider = TripleAPaymentProvider(settings={}, credentials=_credentials())
    assert provider.supports_webhooks is False
    assert provider.supports_refunds is False
    with pytest.raises(UnsupportedProviderCapabilityError, match="webhooks"):
        await provider.verify_webhook(b"{}", {}, "unused")
    with pytest.raises(UnsupportedProviderCapabilityError, match="refunds"):
        await provider.refund(
            PaymentRefundRequest(
                provider_payment_id="PAY-1",
                amount=Decimal("1.00"),
                currency="USD",
                idempotency_key="refund-1",
            )
        )


def test_triplea_credentials_are_structured_and_strict() -> None:
    with pytest.raises(PaymentProviderError, match="encrypted JSON"):
        TripleAPaymentProvider(settings={}, credentials="not-json")
    with pytest.raises(PaymentProviderError, match="Unsupported Triple-A credential fields"):
        TripleAPaymentProvider(
            settings={},
            credentials=json.dumps(
                {
                    "client_id": "a",
                    "client_secret": "b",
                    "merchant_key": "c",
                    "unexpected": "d",
                }
            ),
        )
