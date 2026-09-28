from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest

from packages.notifications.delivery import (
    build_order_goods_txt,
    extract_clean_goods,
    format_delivery_text,
    is_bulk_delivery,
)
from packages.payments.exceptions import UnsupportedProviderCapabilityError
from packages.payments.providers.interface import PaymentCreateRequest, PaymentRefundRequest
from packages.payments.providers.sam_api import SamApiProvider
from packages.payments.state_machine import PaymentIntentStatus


@pytest.mark.asyncio
async def test_sam_api_create_payment_with_explicit_identifier(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://www.sam-api.pro/api/v1/invoices"
        assert request.headers["Authorization"] == "Bearer test-sk-12345"
        body = json.loads(request.content)
        captured.update(body)
        return httpx.Response(
            200,
            json={
                "success": True,
                "invoiceId": "sam-inv-999",
                "paymentUrl": "https://www.sam-api.pro/pay/sam-inv-999",
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: mock_client)

    provider = SamApiProvider(
        settings={"method": "shamcash", "identifier": "sham-wallet-100"},
        api_key="test-sk-12345",
        webhook_secret=None,
    )

    req = PaymentCreateRequest(
        order_id=None,
        amount=Decimal(15000),
        currency="SYP",
        idempotency_key="idemp-1",
        metadata={"webhook_url": "https://example.com/api/webhook"},
    )
    result = await provider.create_payment(req)

    assert result.provider_payment_id == "sam-inv-999"
    assert result.status == PaymentIntentStatus.PENDING
    assert result.checkout_url == "https://www.sam-api.pro/pay/sam-inv-999"
    assert captured["method"] == "shamcash"
    assert captured["identifier"] == "sham-wallet-100"
    assert captured["amount"] == "15000"


@pytest.mark.asyncio
async def test_sam_api_auto_resolves_wallet_from_api(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url).endswith("/v1/wallets"):
            return httpx.Response(
                200,
                json=[
                    {"provider": "syriatel", "phone": "0933112233", "status": "active"},
                    {"provider": "shamcash", "walletAddress": "sham-auto-addr-42", "status": "active"},
                ],
            )
        if str(request.url).endswith("/v1/invoices"):
            body = json.loads(request.content)
            assert body["identifier"] == "sham-auto-addr-42"
            return httpx.Response(
                200,
                json={"success": True, "invoiceId": "auto-inv-1", "paymentUrl": "https://pay.sam/1"},
            )
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: mock_client)

    provider = SamApiProvider(
        settings={"method": "shamcash"},  # No identifier specified
        api_key="test-sk-12345",
        webhook_secret=None,
    )

    req = PaymentCreateRequest(
        order_id=None,
        amount=Decimal(50000),
        currency="SYP",
        idempotency_key="idemp-2",
        metadata={},
    )
    result = await provider.create_payment(req)

    assert result.provider_payment_id == "auto-inv-1"
    assert provider.identifier == "sham-auto-addr-42"
    assert any("/v1/wallets" in c for c in calls)


@pytest.mark.asyncio
async def test_sam_api_get_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/pay/sam-inv-123")
        return httpx.Response(
            200,
            json={
                "invoiceId": "sam-inv-123",
                "status": "paid",
                "amount": "25000",
                "currency": "SYP",
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: mock_client)

    provider = SamApiProvider(settings={}, api_key="sk-test", webhook_secret=None)
    details = await provider.get_payment("sam-inv-123")

    assert details.status == PaymentIntentStatus.SUCCEEDED
    assert details.amount == Decimal(25000)
    assert details.currency == "SYP"


@pytest.mark.asyncio
async def test_sam_api_verify_webhook() -> None:
    provider = SamApiProvider(settings={}, api_key="sk-test", webhook_secret="secret-key")
    payload = json.dumps({"invoiceId": "inv-hook-1", "status": "paid", "amount": "10000", "currency": "SYP"})
    sig = hmac.new(b"secret-key", payload.encode("utf-8"), hashlib.sha256).hexdigest()

    result = await provider.verify_webhook(
        payload=payload,
        headers={"x-signature": sig},
        secret="secret-key",
    )
    assert result.is_valid is True
    assert result.provider_payment_id == "inv-hook-1"
    assert result.status == PaymentIntentStatus.SUCCEEDED
    assert result.amount == Decimal(10000)

    # Invalid signature rejects
    bad_result = await provider.verify_webhook(
        payload=payload,
        headers={"x-signature": "bad-signature"},
        secret="secret-key",
    )
    assert bad_result.is_valid is False


@pytest.mark.asyncio
async def test_sam_api_refund_unsupported() -> None:
    provider = SamApiProvider(settings={}, api_key="sk-test", webhook_secret=None)
    with pytest.raises(UnsupportedProviderCapabilityError):
        await provider.refund(PaymentRefundRequest(
            provider_payment_id="inv-1",
            amount=Decimal(10),
            currency="SYP",
            idempotency_key="idemp-ref",
        ))


def test_delivery_formatting_and_bulk_txt() -> None:
    # 1. Single voucher code formatting
    goods = ["PUBG-UC-12345678"]
    assert not is_bulk_delivery(goods)
    text_ar = format_delivery_text(
        order_identifier="ORD-100",
        product_name="PUBG Mobile 60 UC",
        total_paid="1.25",
        currency="USD",
        goods=goods,
        is_ar=True,
    )
    assert "PUBG-UC-12345678" in text_ar
    assert "<code>" in text_ar
    assert "تم تأكيد وتسليم طلبك" in text_ar

    # 2. Bulk credentials (>= 2 items)
    bulk_goods = ["ACC:pass1", "ACC:pass2", "ACC:pass3", "ACC:pass4"]
    assert is_bulk_delivery(bulk_goods)
    text_bulk = format_delivery_text(
        order_identifier="ORD-200",
        product_name="Gemini Pro Accounts",
        total_paid="20.00",
        currency="USD",
        goods=bulk_goods,
        is_ar=False,
    )
    assert "<b>Total Delivered Items:</b> <code>4</code>" in text_bulk
    assert "All credentials attached as a .txt file below" in text_bulk

    # 3. .txt file building
    fname, content = build_order_goods_txt("ORD-200", "Gemini Pro Accounts", bulk_goods)
    assert fname.startswith("order_ORD-200_gemini_pro_account")
    assert fname.endswith(".txt")
    lines = content.decode("utf-8").strip().split("\n")
    assert len(lines) == 4
    assert lines[0] == "ACC:pass1"
    assert lines[3] == "ACC:pass4"

    # 4. Clean extraction from complex dict items
    raw_mixed = [
        {"pin": "PIN-999"},
        {"account_data": "user:pass"},
        "DIRECT-KEY-777",
    ]
    cleaned = extract_clean_goods(raw_mixed)
    assert cleaned == ["PIN-999", "user:pass", "DIRECT-KEY-777"]
