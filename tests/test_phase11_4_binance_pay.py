from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest

from packages.payments.exceptions import PaymentProviderError, UnsupportedProviderCapabilityError
from packages.payments.providers.binance_pay import BinancePayProvider
from packages.payments.providers.interface import PaymentCreateRequest, PaymentRefundRequest
from packages.payments.state_machine import PaymentIntentStatus

NOW = 1_736_233_200.0
NONCE = "AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def _credentials() -> str:
    return json.dumps({"api_key": "certificate-sn", "api_secret": "merchant-secret"})


def _settings() -> dict[str, object]:
    return {
        "timeout_seconds": 5,
        "terminal_type": "WEB",
        "goods_type": "02",
        "goods_category": "6000",
        "goods_name": "Wallet top up",
        "goods_detail": "Digital wallet balance",
        "description": "Digital wallet balance top up",
        "order_expire_seconds": 3600,
        "support_pay_currencies": ["USDT", "USDC"],
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.mark.asyncio
async def test_binance_create_v3_uses_current_hmac_sha512_contract() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://bpay.binanceapi.com/binancepay/openapi/v3/order"
        assert request.headers["BinancePay-Certificate-SN"] == "certificate-sn"
        assert request.headers["BinancePay-Timestamp"] == str(int(NOW * 1000))
        assert request.headers["BinancePay-Nonce"] == NONCE
        raw_body = request.content.decode()
        payload = f"{int(NOW * 1000)}\n{NONCE}\n{raw_body}\n".encode()
        expected = hmac.new(b"merchant-secret", payload, hashlib.sha512).hexdigest().upper()
        assert request.headers["BinancePay-Signature"] == expected
        body = json.loads(raw_body)
        captured.update(body)
        assert body["env"]["terminalType"] == "WEB"
        assert body["env"]["orderClientIp"] == "203.0.113.12"
        assert body["orderAmount"] == 25.17
        assert body["currency"] == "USD"
        assert body["supportPayCurrency"] == "USDT,USDC"
        assert body["goodsDetails"][0]["goodsType"] == "02"
        return httpx.Response(
            200,
            json={
                "status": "SUCCESS",
                "code": "000000",
                "data": {
                    "prepayId": "2938393749303836729",
                    "terminalType": "WEB",
                    "expireTime": 1_736_236_800_000,
                    "qrcodeLink": "https://pay.binance.com/qr/abc",
                    "qrContent": "https://pay.binance.com/qr-content/abc",
                    "checkoutUrl": "https://pay.binance.com/checkout/abc",
                    "deeplink": "bnc://payment/abc",
                    "universalUrl": "https://app.binance.com/payment/abc",
                    "totalFee": "25.17",
                    "currency": "USD",
                    "unexpected_secret": "must-not-propagate",
                },
                "errorMessage": "",
            },
        )

    async with _client(handler) as client:
        provider = BinancePayProvider(
            settings=_settings(),
            credentials=_credentials(),
            http_client=client,
            time_fn=lambda: NOW,
            nonce_fn=lambda: NONCE,
        )
        result = await provider.create_payment(
            PaymentCreateRequest(
                order_id=None,
                amount=Decimal("25.17"),
                currency="USD",
                idempotency_key="binance-create-1",
                metadata={
                    "payment_intent_id": "af8c2d1-5b3e-4a9f-b6c7-8d2e1f3a4b5c",
                    "_provider_context": {"ip": "203.0.113.12"},
                },
            )
        )

    assert captured["merchantTradeNo"] == "af8c2d15b3e4a9fb6c78d2e1f3a4b5c"
    assert result.provider_payment_id == "2938393749303836729"
    assert result.status == PaymentIntentStatus.PENDING
    assert result.checkout_url == "https://pay.binance.com/checkout/abc"
    assert result.raw_data["qr_content"] == "https://pay.binance.com/qr-content/abc"
    assert "unexpected_secret" not in result.raw_data
    assert result.verification_attributes == {
        "merchant_trade_no": "af8c2d15b3e4a9fb6c78d2e1f3a4b5c",
        "settlement_currency": "USD",
        "settlement_amount": "25.17",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("INITIAL", PaymentIntentStatus.PENDING),
        ("PENDING", PaymentIntentStatus.PROCESSING),
        ("PAID", PaymentIntentStatus.SUCCEEDED),
        ("CANCELED", PaymentIntentStatus.CANCELLED),
        ("ERROR", PaymentIntentStatus.FAILED),
        ("EXPIRED", PaymentIntentStatus.EXPIRED),
        ("REFUNDING", PaymentIntentStatus.UNKNOWN),
        ("REFUNDED", PaymentIntentStatus.UNKNOWN),
        ("FUTURE", PaymentIntentStatus.UNKNOWN),
    ],
)
async def test_binance_query_status_mapping_is_fail_closed(raw_status, expected) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/binancepay/openapi/order/query"
        body = json.loads(request.content)
        assert body == {"merchantTradeNo": None, "prepayId": "2938393749303836729"}
        return httpx.Response(
            200,
            json={
                "status": "SUCCESS",
                "code": "000000",
                "data": {
                    "merchantId": 98729382672,
                    "prepayId": "2938393749303836729",
                    "transactionId": "23729202729220282",
                    "merchantTradeNo": "af8c2d15b3e4a9fb6c78d2e1f3a4b5c",
                    "tradeType": "WEB",
                    "status": raw_status,
                    "currency": "USD",
                    "totalFee": "25.17",
                    "createTime": 1_736_233_200_000,
                },
            },
        )

    async with _client(handler) as client:
        provider = BinancePayProvider(
            settings=_settings(),
            credentials=_credentials(),
            http_client=client,
            time_fn=lambda: NOW,
            nonce_fn=lambda: NONCE,
        )
        result = await provider.get_payment("2938393749303836729")

    assert result.status == expected
    assert result.amount == Decimal("25.17")
    assert result.currency == "USD"
    assert result.verification_attributes["settlement_amount"] == "25.17"


@pytest.mark.asyncio
async def test_binance_rejects_authoritative_create_mutation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "SUCCESS",
                "code": "000000",
                "data": {
                    "prepayId": "2938393749303836729",
                    "currency": "USD",
                    "totalFee": "24.99",
                    "checkoutUrl": "https://pay.binance.com/checkout/abc",
                },
            },
        )

    async with _client(handler) as client:
        provider = BinancePayProvider(
            settings=_settings(), credentials=_credentials(), http_client=client, nonce_fn=lambda: NONCE
        )
        with pytest.raises(PaymentProviderError, match="authoritative amount/currency"):
            await provider.create_payment(
                PaymentCreateRequest(
                    order_id=None,
                    amount=Decimal("25.00"),
                    currency="USD",
                    idempotency_key="binance-mutation",
                    metadata={"payment_intent_id": "abc123"},
                )
            )


@pytest.mark.asyncio
async def test_binance_webhooks_and_refunds_remain_fail_closed() -> None:
    provider = BinancePayProvider(settings=_settings(), credentials=_credentials())
    assert provider.supports_webhooks is False
    assert provider.supports_refunds is False
    with pytest.raises(UnsupportedProviderCapabilityError, match="webhooks"):
        await provider.verify_webhook(b"{}", {}, "unused")
    with pytest.raises(UnsupportedProviderCapabilityError, match="refunds"):
        await provider.refund(
            PaymentRefundRequest(
                provider_payment_id="2938393749303836729",
                amount=Decimal("1.00"),
                currency="USD",
                idempotency_key="refund-1",
            )
        )


def test_binance_credentials_and_settings_are_strict() -> None:
    with pytest.raises(PaymentProviderError, match="credentials"):
        BinancePayProvider(settings=_settings(), credentials="not-json")
    bad = _settings()
    bad["goods_category"] = "BAD1"
    with pytest.raises(PaymentProviderError, match="goods_category"):
        BinancePayProvider(settings=bad, credentials=_credentials())
    bad = _settings()
    bad["support_pay_currencies"] = ["USDT;DROP"]
    with pytest.raises(PaymentProviderError, match="support_pay_currencies"):
        BinancePayProvider(settings=bad, credentials=_credentials())
