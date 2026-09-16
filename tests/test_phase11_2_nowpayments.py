import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest

from packages.payments.exceptions import PaymentProviderError
from packages.payments.providers.interface import PaymentCreateRequest
from packages.payments.providers.nowpayments import NowPaymentsProvider
from packages.payments.state_machine import PaymentIntentStatus


@pytest.fixture
def provider_settings() -> dict[str, object]:
    return {
        "pay_currency": "usdttrc20",
        "timeout_seconds": 5,
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.mark.asyncio
async def test_nowpayments_create_direct_payment_is_strict_and_sanitized(provider_settings):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.nowpayments.io/v1/payment"
        assert request.headers["x-api-key"] == "api-key"
        payload = json.loads(request.content)
        seen.update(payload)
        return httpx.Response(
            200,
            json={
                "payment_id": 12345,
                "payment_status": "waiting",
                "pay_address": "TExample",
                "price_amount": 25.50,
                "price_currency": "usd",
                "pay_amount": "25.1",
                "actually_paid": "0",
                "pay_currency": "usdttrc20",
                "order_id": payload["order_id"],
                "unexpected_private_field": "must-not-propagate",
            },
        )

    async with _client(handler) as client:
        provider = NowPaymentsProvider(
            settings=provider_settings,
            api_key="api-key",
            webhook_secret="ipn-secret",
            http_client=client,
        )
        result = await provider.create_payment(
            PaymentCreateRequest(
                order_id=None,
                amount=Decimal("25.50"),
                currency="USD",
                idempotency_key="idem-12345678",
                metadata={"payment_intent_id": "intent-123"},
            )
        )

    assert seen["price_amount"] == 25.5
    assert seen["price_currency"] == "usd"
    assert seen["pay_currency"] == "usdttrc20"
    assert seen["order_id"] == "intent-123"
    assert result.provider_payment_id == "12345"
    assert result.status == PaymentIntentStatus.PENDING
    assert result.checkout_url is None
    assert result.raw_data["pay_address"] == "TExample"
    assert "unexpected_private_field" not in result.raw_data


@pytest.mark.asyncio
async def test_nowpayments_finished_requires_full_crypto_amount(provider_settings):
    responses = [
        {
            "payment_id": 77,
            "payment_status": "finished",
            "price_amount": 10,
            "price_currency": "usd",
            "pay_amount": "10",
            "actually_paid": "9.999",
            "pay_currency": "usdttrc20",
        },
        {
            "payment_id": 77,
            "payment_status": "finished",
            "price_amount": 10,
            "price_currency": "usd",
            "pay_amount": "10",
            "actually_paid": "10",
            "pay_currency": "usdttrc20",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    async with _client(handler) as client:
        provider = NowPaymentsProvider(
            settings=provider_settings,
            api_key="api-key",
            webhook_secret="ipn-secret",
            http_client=client,
        )
        partial = await provider.get_payment("77")
        complete = await provider.get_payment("77")

    assert partial.status == PaymentIntentStatus.UNKNOWN
    assert complete.status == PaymentIntentStatus.SUCCEEDED
    assert complete.amount == Decimal(10)
    assert complete.currency == "USD"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("waiting", PaymentIntentStatus.PENDING),
        ("confirming", PaymentIntentStatus.PROCESSING),
        ("confirmed", PaymentIntentStatus.PROCESSING),
        ("sending", PaymentIntentStatus.PROCESSING),
        ("partially_paid", PaymentIntentStatus.UNKNOWN),
        ("refunded", PaymentIntentStatus.UNKNOWN),
        ("failed", PaymentIntentStatus.FAILED),
        ("expired", PaymentIntentStatus.EXPIRED),
        ("cancelled", PaymentIntentStatus.CANCELLED),
        ("something_new", PaymentIntentStatus.UNKNOWN),
    ],
)
async def test_nowpayments_status_mapping_is_fail_closed(provider_settings, raw_status, expected):
    body = {
        "payment_id": 88,
        "payment_status": raw_status,
        "price_amount": 10,
        "price_currency": "usd",
        "pay_amount": "10",
        "actually_paid": "10",
        "pay_currency": "usdttrc20",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _client(handler) as client:
        provider = NowPaymentsProvider(
            settings=provider_settings,
            api_key="api-key",
            webhook_secret="ipn-secret",
            http_client=client,
        )
        result = await provider.get_payment("88")
    assert result.status == expected


@pytest.mark.asyncio
async def test_nowpayments_webhook_hmac_sha512_deep_sorted_vector(provider_settings):
    # Canonical object mirrors the current official SDK contract:
    # JSON.stringify(sortObjectDeep(payload)) + HMAC-SHA512.
    payload = {
        "payment_status": "finished",
        "nested": {"z": 2, "a": 1},
        "payment_id": 123,
        "price_currency": "usd",
        "price_amount": 10,
        "pay_currency": "usdttrc20",
        "actually_paid": 10,
        "pay_amount": 10,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    expected_canonical = (
        b'{"actually_paid":10,"nested":{"a":1,"z":2},"pay_amount":10,'
        b'"pay_currency":"usdttrc20","payment_id":123,"payment_status":"finished",'
        b'"price_amount":10,"price_currency":"usd"}'
    )
    expected_signature = (
        "695ee8e0246e1a37265c631bc351b4d6378584247ce0f040bb01b2f9e227b4a8"
        "9c2d96ba6fa813e7e961d670511751c529d815dc12165a42ebbecce90dc0d881"
    )
    assert NowPaymentsProvider._canonical_signature_payload(payload) == expected_canonical
    assert hmac.new(b"test-ipn-secret", expected_canonical, hashlib.sha512).hexdigest() == expected_signature

    provider = NowPaymentsProvider(
        settings=provider_settings,
        api_key="api-key",
        webhook_secret="test-ipn-secret",
    )
    result = await provider.verify_webhook(
        raw,
        {"X-NowPayments-Sig": expected_signature},
        "test-ipn-secret",
    )
    assert result.is_valid is True
    assert result.provider_payment_id == "123"
    assert result.status == PaymentIntentStatus.SUCCEEDED
    assert result.amount == Decimal(10)
    assert result.currency == "USD"
    assert result.provider_event_id.startswith("np_")


@pytest.mark.asyncio
async def test_nowpayments_invalid_webhook_signature_is_rejected(provider_settings):
    provider = NowPaymentsProvider(
        settings=provider_settings,
        api_key="api-key",
        webhook_secret="test-ipn-secret",
    )
    result = await provider.verify_webhook(
        b'{"payment_id":123}',
        {"x-nowpayments-sig": "0" * 128},
        "test-ipn-secret",
    )
    assert result.is_valid is False
    assert result.provider_payment_id is None


@pytest.mark.asyncio
async def test_nowpayments_create_rejects_authoritative_price_mutation(provider_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "payment_id": 999,
                "payment_status": "waiting",
                "price_amount": 9.99,
                "price_currency": "usd",
                "pay_amount": 9,
                "actually_paid": 0,
                "pay_currency": "usdttrc20",
            },
        )

    async with _client(handler) as client:
        provider = NowPaymentsProvider(
            settings=provider_settings,
            api_key="api-key",
            webhook_secret="ipn-secret",
            http_client=client,
        )
        with pytest.raises(PaymentProviderError, match="changed the authoritative price"):
            await provider.create_payment(
                PaymentCreateRequest(
                    order_id=None,
                    amount=Decimal("10.00"),
                    currency="USD",
                    idempotency_key="idem-abcdefgh",
                )
            )


@pytest.mark.asyncio
async def test_nowpayments_redirect_is_rejected(provider_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.invalid/steal"})

    async with _client(handler) as client:
        provider = NowPaymentsProvider(
            settings=provider_settings,
            api_key="api-key",
            webhook_secret="ipn-secret",
            http_client=client,
        )
        with pytest.raises(PaymentProviderError, match="unexpected redirect"):
            await provider.get_payment("123")
