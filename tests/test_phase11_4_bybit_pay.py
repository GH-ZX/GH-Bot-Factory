from __future__ import annotations

import base64
import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from packages.payments.exceptions import PaymentProviderError, UnsupportedProviderCapabilityError
from packages.payments.providers.bybit_pay import BybitPayProvider
from packages.payments.providers.interface import PaymentCreateRequest, PaymentRefundRequest
from packages.payments.state_machine import PaymentIntentStatus

NOW = 1_736_233_200.0


def _credentials() -> str:
    return json.dumps(
        {"api_key": "bybit-api-key", "api_secret": "bybit-secret", "merchant_id": "305142568"}
    )


def _settings() -> dict[str, object]:
    return {
        "sandbox": True,
        "timeout_seconds": 5,
        "recv_window_ms": 5000,
        "webhook_tolerance_seconds": 300,
        "success_url": "https://merchant.example/success",
        "failed_url": "https://merchant.example/failed",
        "webhook_url": "https://merchant.example/webhooks/bybit",
        "shopping_name": "GHBF Store",
        "goods_name": "Wallet top-up",
        "goods_detail": "Digital wallet balance",
        "mcc_code": "5816",
        "terminal_type": "WEB",
        "currency_types": {"USD": "fiat"},
    }


def _context() -> dict[str, str]:
    return {
        "device": "ghbf-1234567890abcdef",
        "browser_version": "Mozilla/5.0 Test Browser",
        "ip": "203.0.113.10",
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.mark.asyncio
async def test_bybit_create_uses_current_signed_v5_contract_and_sanitizes_checkout() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api-testnet.bybit.com/v5/bybitpay/create_pay"
        assert request.headers["X-BAPI-API-KEY"] == "bybit-api-key"
        assert request.headers["X-BAPI-TIMESTAMP"] == str(int(NOW * 1000))
        assert request.headers["X-BAPI-RECV-WINDOW"] == "5000"
        raw_body = request.content.decode("utf-8")
        expected = hmac.new(
            b"bybit-secret",
            f"{int(NOW * 1000)}bybit-api-key5000{raw_body}".encode(),
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["X-BAPI-SIGN"] == expected
        body = json.loads(raw_body)
        captured.update(body)
        assert body["merchantId"] == "305142568"
        assert body["paymentType"] == "E_COMMERCE"
        assert body["orderAmount"] == "25.50"
        assert body["currency"] == "USD"
        assert body["currencyType"] == "fiat"
        assert body["env"] == {
            "terminalType": "WEB",
            "device": "ghbf-1234567890abcdef",
            "browserVersion": "Mozilla/5.0 Test Browser",
            "ip": "203.0.113.10",
        }
        return httpx.Response(
            200,
            json={
                "retCode": 100000,
                "retMsg": "success",
                "result": {
                    "payId": "01JY2KM5QNPXR8S4HTJZT9BC12",
                    "terminalType": "WEB",
                    "expireTime": 1_736_236_800,
                    "checkoutLink": "bybitapp://open/route?targetUrl=payment",
                    "qrContent": "data:image/png;base64,AAAA",
                    "order": {
                        "merchantId": "305142568",
                        "paymentType": "E_COMMERCE",
                        "merchantTradeNo": body["merchantTradeNo"],
                        "payId": "01JY2KM5QNPXR8S4HTJZT9BC12",
                        "status": "INIT",
                        "amount": "25.50",
                        "currency": "USD",
                        "currencyType": "fiat",
                    },
                    "customer": {"uid": "must-not-leak"},
                },
            },
        )

    async with _client(handler) as client:
        provider = BybitPayProvider(
            settings=_settings(),
            credentials=_credentials(),
            http_client=client,
            time_fn=lambda: NOW,
        )
        result = await provider.create_payment(
            PaymentCreateRequest(
                order_id=None,
                amount=Decimal("25.50"),
                currency="USD",
                idempotency_key="bybit-create-1",
                metadata={
                    "payment_intent_id": "af8c2d1-5b3e-4a9f-b6c7-8d2e1f3a4b5c",
                    "_provider_context": _context(),
                },
            )
        )

    assert captured["merchantTradeNo"] == "af8c2d1-5b3e-4a9f-b6c7-8d2e1f3a4b5c"
    assert result.provider_payment_id == "01JY2KM5QNPXR8S4HTJZT9BC12"
    assert result.status == PaymentIntentStatus.PENDING
    assert result.checkout_url is None  # custom app scheme is never promoted to web checkout_url
    assert result.raw_data["qr_content"] == "data:image/png;base64,AAAA"
    assert "customer" not in result.raw_data
    assert result.verification_attributes == {
        "merchant_trade_no": "af8c2d1-5b3e-4a9f-b6c7-8d2e1f3a4b5c",
        "merchant_id": "305142568",
        "currency_type": "fiat",
    }


@pytest.mark.asyncio
async def test_bybit_get_payment_signs_sorted_query_and_maps_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/bybitpay/pay_result"
        query = request.url.query.decode()
        assert query == (
            "merchantId=305142568&paymentType=E_COMMERCE&"
            "payId=01JY2KM5QNPXR8S4HTJZT9BC12"
        )
        expected = hmac.new(
            b"bybit-secret",
            f"{int(NOW * 1000)}bybit-api-key5000{query}".encode(),
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["X-BAPI-SIGN"] == expected
        return httpx.Response(
            200,
            json={
                "retCode": 100000,
                "retMsg": "success",
                "result": {
                    "order": {
                        "merchantId": "305142568",
                        "paymentType": "E_COMMERCE",
                        "merchantTradeNo": "trade-1",
                        "payId": "01JY2KM5QNPXR8S4HTJZT9BC12",
                        "status": "PAY_SUCCESS",
                        "amount": "25.50",
                        "currency": "USD",
                        "currencyType": "fiat",
                        "finishTime": 1_736_233_260,
                    },
                    "customer": {"externalUserId": "must-not-leak"},
                },
            },
        )

    async with _client(handler) as client:
        provider = BybitPayProvider(
            settings=_settings(), credentials=_credentials(), http_client=client, time_fn=lambda: NOW
        )
        result = await provider.get_payment("01JY2KM5QNPXR8S4HTJZT9BC12")

    assert result.status == PaymentIntentStatus.SUCCEEDED
    assert result.amount == Decimal("25.50")
    assert result.currency == "USD"
    assert result.verification_attributes["merchant_trade_no"] == "trade-1"
    assert "customer" not in result.raw_data


@pytest.mark.asyncio
async def test_bybit_webhook_verifies_exact_raw_body_with_rsa_sha256() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    payload = json.dumps(
        {
            "paymentType": "E_COMMERCE",
            "merchantId": "305142568",
            "merchantTradeNo": "trade-1",
            "payId": "pay-1",
            "status": "PAY_SUCCESS",
            "amount": "10.00",
            "currency": "USD",
            "currencyType": "fiat",
            "createTime": int(NOW) - 30,
            "paymentTime": int(NOW) - 10,
            "finishTime": int(NOW),
        },
        separators=(",", ":"),
    ).encode()
    timestamp = str(int(NOW))
    signature = private_key.sign(
        timestamp.encode() + payload, padding.PKCS1v15(), hashes.SHA256()
    )

    provider = BybitPayProvider(
        settings=_settings(),
        credentials=_credentials(),
        webhook_secret=public_pem,
        time_fn=lambda: NOW,
    )
    result = await provider.verify_webhook(
        payload,
        {"timestamp": timestamp, "signature": base64.b64encode(signature).decode()},
        public_pem,
    )
    assert result.is_valid is True
    assert result.provider_payment_id == "pay-1"
    assert result.status == PaymentIntentStatus.SUCCEEDED
    assert result.amount == Decimal("10.00")
    assert result.currency == "USD"
    assert result.provider_event_id == f"pay-1:PAY_SUCCESS:{int(NOW)}"

    tampered = await provider.verify_webhook(
        payload + b" ",
        {"timestamp": timestamp, "signature": base64.b64encode(signature).decode()},
        public_pem,
    )
    assert tampered.is_valid is False


@pytest.mark.asyncio
async def test_bybit_webhook_rejects_replay_window_and_refunds_fail_closed() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    provider = BybitPayProvider(
        settings=_settings(), credentials=_credentials(), webhook_secret=public_pem, time_fn=lambda: NOW
    )
    stale = await provider.verify_webhook(
        b"{}", {"timestamp": str(int(NOW) - 301), "signature": "AAAA"}, public_pem
    )
    assert stale.is_valid is False
    assert provider.supports_refunds is False
    with pytest.raises(UnsupportedProviderCapabilityError, match="refunds"):
        await provider.refund(
            PaymentRefundRequest(
                provider_payment_id="pay-1",
                amount=Decimal("1.00"),
                currency="USD",
                idempotency_key="refund-1",
            )
        )


def test_bybit_requires_structured_credentials_urls_currency_types_and_context() -> None:
    with pytest.raises(PaymentProviderError, match="credentials"):
        BybitPayProvider(settings=_settings(), credentials="not-json")
    bad = _settings()
    bad["success_url"] = "http://insecure.example/success"
    with pytest.raises(PaymentProviderError, match="HTTPS"):
        BybitPayProvider(settings=bad, credentials=_credentials())
    bad = _settings()
    bad["currency_types"] = {}
    with pytest.raises(PaymentProviderError, match="currency_types"):
        BybitPayProvider(settings=bad, credentials=_credentials())
