from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest

from packages.payments.exceptions import PaymentProviderError, UnsupportedProviderCapabilityError
from packages.payments.providers.gozapay import GoZaPayProvider
from packages.payments.providers.interface import PaymentCreateRequest, PaymentRefundRequest
from packages.payments.state_machine import PaymentIntentStatus

NOW = 1_757_979_600


def _settings() -> dict[str, object]:
    return {
        "chain": "tron",
        "coin": "USDT",
        "timeout_seconds": 5,
        "webhook_tolerance_seconds": 300,
        "nominal_stablecoin_parity_acknowledged": True,
        "experimental_risk_acknowledged": True,
    }


@pytest.mark.asyncio
async def test_gozapay_create_uses_idempotency_and_waits_for_settlement() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://gozapay.com/api/v1/invoices"
        assert request.headers["X-API-Key"] == "goza-api-key"
        assert request.headers["X-Idempotency-Key"] == "goza-create-1"
        body = json.loads(request.content)
        captured.update(body)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": "9bbdcee8-d6e7-4620-80c9-03b52244e8bd",
                    "order_id": body["order_id"],
                    "chain": "tron",
                    "coin": "USDT",
                    "expected_amount": "25.50",
                    "unique_amount": "25.50",
                    "amount_received": "0",
                    "status": "pending",
                    "payment_url": "https://gozapay.com/pay/test-1",
                    "flexible": False,
                    "settled": False,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoZaPayProvider(
            settings=_settings(),
            api_key="goza-api-key",
            webhook_secret="goza-webhook-secret",
            http_client=client,
            time_fn=lambda: NOW,
        )
        result = await provider.create_payment(
            PaymentCreateRequest(
                order_id=None,
                amount=Decimal("25.50"),
                currency="USD",
                idempotency_key="goza-create-1",
                metadata={"payment_intent_id": "11111111-2222-3333-4444-555555555555"},
            )
        )

    assert captured == {
        "amount": "25.50",
        "chain": "tron",
        "coin": "USDT",
        "order_id": "11111111-2222-3333-4444-555555555555",
        "product_name": "Wallet top-up",
    }
    assert result.status == PaymentIntentStatus.PENDING
    assert result.checkout_url == "https://gozapay.com/pay/test-1"
    assert result.verification_attributes == {
        "chain": "tron",
        "coin": "usdt",
        "order_id": "11111111-2222-3333-4444-555555555555",
        "parity_mode": "nominal_usd",
    }


@pytest.mark.asyncio
async def test_gozapay_polling_only_succeeds_after_settled_exact_amount() -> None:
    states = iter(
        [
            {
                "id": "inv-1",
                "order_id": "intent-1",
                "chain": "tron",
                "coin": "USDT",
                "expected_amount": "10.00",
                "unique_amount": "10.00",
                "amount_received": "10.00",
                "status": "paid",
                "flexible": False,
                "settled": False,
            },
            {
                "id": "inv-1",
                "order_id": "intent-1",
                "chain": "tron",
                "coin": "USDT",
                "expected_amount": "10.00",
                "unique_amount": "10.00",
                "amount_received": "10.00",
                "status": "settled",
                "flexible": False,
                "settled": True,
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": next(states)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoZaPayProvider(
            settings=_settings(), api_key="key", webhook_secret="secret", http_client=client
        )
        paid = await provider.get_payment("inv-1")
        settled = await provider.get_payment("inv-1")

    assert paid.status == PaymentIntentStatus.PROCESSING
    assert settled.status == PaymentIntentStatus.SUCCEEDED
    assert settled.amount == Decimal("10.00")
    assert settled.currency == "USD"


@pytest.mark.asyncio
async def test_gozapay_overpayment_and_flexible_invoices_fail_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": "inv-2",
                    "order_id": "intent-2",
                    "chain": "tron",
                    "coin": "USDT",
                    "expected_amount": "10.00",
                    "unique_amount": "10.00",
                    "amount_received": "12.00",
                    "status": "overpaid",
                    "flexible": False,
                    "settled": False,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoZaPayProvider(
            settings=_settings(), api_key="key", webhook_secret="secret", http_client=client
        )
        details = await provider.get_payment("inv-2")
        with pytest.raises(UnsupportedProviderCapabilityError):
            await provider.create_payment(
                PaymentCreateRequest(
                    order_id=None,
                    amount=Decimal("10.00"),
                    currency="USD",
                    idempotency_key="flex-1",
                    metadata={"flexible": True},
                )
            )

    assert details.status == PaymentIntentStatus.UNKNOWN


@pytest.mark.asyncio
async def test_gozapay_webhook_verifies_raw_body_timestamp_and_stays_processing_on_paid() -> None:
    provider = GoZaPayProvider(
        settings=_settings(),
        api_key="key",
        webhook_secret="webhook-secret",
        time_fn=lambda: NOW,
    )
    body = json.dumps(
        {
            "event": "invoice.paid",
            "invoice_id": "inv-3",
            "order_id": "intent-3",
            "status": "paid",
            "chain": "tron",
            "coin": "USDT",
            "expected_amount": "20.00",
            "unique_amount": "20.00",
            "amount_received": "20.00",
            "difference": "0",
            "flexible": False,
            "timestamp": str(NOW),
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(
        b"webhook-secret", f"{NOW}.".encode() + body, hashlib.sha256
    ).hexdigest()
    headers = {
        "X-Signature": f"sha256={signature}",
        "X-Timestamp": str(NOW),
        "X-Webhook-Id": "delivery-123",
    }
    result = await provider.verify_webhook(body, headers, "webhook-secret")
    replay = await provider.verify_webhook(
        body,
        {**headers, "X-Timestamp": str(NOW - 301)},
        "webhook-secret",
    )

    assert result.is_valid is True
    assert result.provider_event_id == "delivery-123"
    assert result.provider_payment_id == "inv-3"
    assert result.status == PaymentIntentStatus.PROCESSING
    assert result.amount == Decimal("20.00")
    assert result.currency == "USD"
    assert replay.is_valid is False


@pytest.mark.asyncio
async def test_gozapay_requires_risk_ack_and_fixed_payments_require_parity_ack() -> None:
    with pytest.raises(PaymentProviderError):
        GoZaPayProvider(settings={}, api_key="key", webhook_secret=None)

    provider = GoZaPayProvider(
        settings={"experimental_risk_acknowledged": True},
        api_key="key",
        webhook_secret=None,
    )
    assert provider.supports_flexible_deposits is True
    with pytest.raises(PaymentProviderError, match="parity"):
        await provider.create_payment(
            PaymentCreateRequest(
                order_id=None,
                amount=Decimal("10.00"),
                currency="USD",
                idempotency_key="fixed-without-parity",
            )
        )


@pytest.mark.asyncio
async def test_gozapay_refunds_are_fail_closed() -> None:
    provider = GoZaPayProvider(settings=_settings(), api_key="key", webhook_secret=None)
    with pytest.raises(UnsupportedProviderCapabilityError):
        await provider.refund(
            PaymentRefundRequest(
                provider_payment_id="inv-1",
                amount=Decimal("10.00"),
                currency="USD",
                idempotency_key="refund-1",
            )
        )
