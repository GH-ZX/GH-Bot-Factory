from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import string
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from packages.payments.exceptions import (
    PaymentProviderError,
    PaymentProviderTransportError,
    UnsupportedProviderCapabilityError,
)
from packages.payments.providers.interface import (
    PaymentCreateRequest,
    PaymentCreateResult,
    PaymentDetailsResult,
    PaymentProvider,
    PaymentRefundRequest,
    PaymentRefundResult,
    WebhookVerificationResult,
)
from packages.payments.state_machine import PaymentIntentStatus


class BinancePayProvider(PaymentProvider):
    """Binance Pay Merchant adapter using current v3 create-order + query-order APIs.

    The adapter is deliberately polling-first. The current create/query/signing contracts
    are wired from Binance Pay Merchant documentation; payment webhooks remain disabled
    until the exact notification body/event contract is pinned and regression-vectored.

    Credential secret JSON:
      {"api_key":"<Certificate-SN>", "api_secret":"<secret-key>"}
    """

    provider_name = "binance_pay"
    api_base = "https://bpay.binanceapi.com"
    supports_idempotency_keys = True  # merchantTradeNo is merchant-controlled and unique.
    supports_creation_recovery = True
    supports_payment_lookup = True
    supports_webhooks = False
    supports_refunds = False
    supports_partial_refunds = False
    supports_safe_refund_retries = False

    _MAX_RESPONSE_BYTES = 256 * 1024
    _ALNUM = string.ascii_letters + string.digits

    def __init__(
        self,
        *,
        settings: dict[str, Any],
        credentials: str,
        webhook_secret: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        time_fn: Any | None = None,
        nonce_fn: Any | None = None,
    ) -> None:
        del webhook_secret
        self._settings = dict(settings or {})
        self._api_key, self._api_secret = self._parse_credentials(credentials)
        self._http_client = http_client
        self._time_fn = time_fn or time.time
        self._nonce_fn = nonce_fn or self._random_nonce
        self._timeout_seconds = self._number_setting(
            "timeout_seconds", default=15.0, minimum=2.0, maximum=30.0
        )
        terminal = str(self._settings.get("terminal_type") or "WEB").strip().upper()
        if terminal not in {"APP", "WEB", "WAP", "MINI_PROGRAM", "OTHERS"}:
            raise PaymentProviderError("Binance Pay terminal_type is invalid.")
        self._terminal_type = terminal
        self._goods_type = self._bounded_setting("goods_type", default="02", limit=2)
        if self._goods_type not in {"01", "02"}:
            raise PaymentProviderError("Binance Pay goods_type must be 01 or 02.")
        self._goods_category = self._bounded_setting("goods_category", default="6000", limit=4)
        allowed_categories = {
            "0000", "1000", "2000", "3000", "4000", "5000", "6000", "7000",
            "8000", "9000", "A000", "B000", "C000", "D000", "E000", "F000", "Z000",
        }
        if self._goods_category not in allowed_categories:
            raise PaymentProviderError("Binance Pay goods_category is invalid.")
        self._goods_name = self._clean_goods_text(
            self._settings.get("goods_name") or "Wallet top up", field="goods_name", required=True
        )
        self._goods_detail = self._clean_goods_text(
            self._settings.get("goods_detail") or "Digital wallet balance top up",
            field="goods_detail",
            required=False,
        )
        self._description = self._clean_goods_text(
            self._settings.get("description") or "Digital wallet balance top up",
            field="description",
            required=True,
        )
        expire = int(self._number_setting(
            "order_expire_seconds", default=3600, minimum=60, maximum=15 * 24 * 60 * 60
        ))
        self._order_expire_seconds = expire
        raw_support = self._settings.get("support_pay_currencies", [])
        if raw_support is None:
            raw_support = []
        if not isinstance(raw_support, list):
            raise PaymentProviderError("Binance Pay support_pay_currencies must be a list.")
        support: list[str] = []
        for item in raw_support:
            value = str(item).strip().upper()
            if not value or len(value) > 16 or not value.isalnum():
                raise PaymentProviderError("Binance Pay support_pay_currencies contains an invalid code.")
            if value not in support:
                support.append(value)
        joined = ",".join(support)
        if len(joined) > 1024:
            raise PaymentProviderError("Binance Pay support_pay_currencies exceeds 1024 characters.")
        self._support_pay_currencies = support

    @staticmethod
    def _parse_credentials(credentials: str) -> tuple[str, str]:
        try:
            payload = json.loads(credentials)
        except json.JSONDecodeError as exc:
            raise PaymentProviderError(
                "Binance Pay credentials must be encrypted JSON with api_key and api_secret."
            ) from exc
        if not isinstance(payload, dict):
            raise PaymentProviderError("Binance Pay credentials JSON must be an object.")
        unknown = sorted(set(payload) - {"api_key", "api_secret"})
        if unknown:
            raise PaymentProviderError(f"Unsupported Binance Pay credential fields: {unknown}.")
        api_key = str(payload.get("api_key") or "").strip()
        api_secret = str(payload.get("api_secret") or "").strip()
        if not api_key or len(api_key) > 512 or not api_secret or len(api_secret) > 2048:
            raise PaymentProviderError("Binance Pay api_key/api_secret credentials are missing or invalid.")
        return api_key, api_secret

    def _number_setting(self, key: str, *, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(self._settings.get(key, default))
        except (TypeError, ValueError) as exc:
            raise PaymentProviderError(f"Binance Pay {key} must be numeric.") from exc
        if not minimum <= value <= maximum:
            raise PaymentProviderError(
                f"Binance Pay {key} must be between {minimum:g} and {maximum:g}."
            )
        return value

    def _bounded_setting(self, key: str, *, default: str, limit: int) -> str:
        value = str(self._settings.get(key, default) or "").strip().upper()
        if not value or len(value) > limit or any(ord(char) < 32 for char in value):
            raise PaymentProviderError(f"Binance Pay {key} is missing or invalid.")
        return value

    @staticmethod
    def _clean_goods_text(value: Any, *, field: str, required: bool) -> str:
        text = str(value or "").strip()
        if not text and required:
            raise PaymentProviderError(f"Binance Pay {field} is required.")
        if len(text) > 256 or any(ord(char) < 32 for char in text):
            raise PaymentProviderError(f"Binance Pay {field} is invalid or exceeds 256 characters.")
        # Current Binance Pay goodsName documentation prohibits backslash, quotes, and emoji.
        # Apply the same conservative text policy to user-visible configured goods strings.
        if any(char in text for char in ('\\', '"')) or any(ord(char) > 0xFFFF for char in text):
            raise PaymentProviderError(f"Binance Pay {field} contains unsupported characters.")
        return text

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return parsed if parsed.is_finite() else None

    @staticmethod
    def _canonical_amount(value: Decimal) -> str:
        return format(value.normalize(), "f")

    @classmethod
    def _random_nonce(cls) -> str:
        return "".join(secrets.choice(cls._ALNUM) for _ in range(32))

    @staticmethod
    def _merchant_trade_no(request: PaymentCreateRequest) -> str:
        raw = request.metadata.get("payment_intent_id") or request.idempotency_key
        compact = "".join(char for char in str(raw) if char.isalnum())
        if not compact:
            raise PaymentProviderError("Binance Pay merchantTradeNo is invalid.")
        # UUIDs become their exact 32-character hex form. Longer caller keys are hashed
        # rather than truncated to avoid prefix collisions.
        if len(compact) > 32:
            compact = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:32]
        return compact

    @staticmethod
    def _status(raw: Any) -> PaymentIntentStatus:
        status = str(raw or "").strip().upper()
        if status == "INITIAL":
            return PaymentIntentStatus.PENDING
        if status == "PENDING":
            return PaymentIntentStatus.PROCESSING
        if status == "PAID":
            return PaymentIntentStatus.SUCCEEDED
        if status == "CANCELED":
            return PaymentIntentStatus.CANCELLED
        if status == "ERROR":
            return PaymentIntentStatus.FAILED
        if status == "EXPIRED":
            return PaymentIntentStatus.EXPIRED
        if status in {"REFUNDING", "REFUNDED"}:
            return PaymentIntentStatus.UNKNOWN
        return PaymentIntentStatus.UNKNOWN

    def _signed_headers(self, timestamp_ms: str, nonce: str, raw_body: str) -> dict[str, str]:
        payload = f"{timestamp_ms}\n{nonce}\n{raw_body}\n".encode()
        signature = hmac.new(
            self._api_secret.encode("utf-8"), payload, hashlib.sha512
        ).hexdigest().upper()
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "BinancePay-Timestamp": timestamp_ms,
            "BinancePay-Nonce": nonce,
            "BinancePay-Certificate-SN": self._api_key,
            "BinancePay-Signature": signature,
        }

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        timestamp_ms = str(int(self._time_fn() * 1000))
        nonce = str(self._nonce_fn())
        if len(nonce) != 32 or not nonce.isalnum():
            raise PaymentProviderError("Binance Pay nonce generator returned an invalid 32-character nonce.")
        headers = self._signed_headers(timestamp_ms, nonce, raw_body)
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds), follow_redirects=False, trust_env=False
        )
        try:
            async with client.stream(
                "POST", f"{self.api_base}{path}", headers=headers, content=raw_body.encode("utf-8")
            ) as response:
                if 300 <= response.status_code < 400:
                    raise PaymentProviderError("Binance Pay returned an unexpected redirect.")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._MAX_RESPONSE_BYTES:
                        raise PaymentProviderError("Binance Pay response exceeded the allowed size.")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                if response.status_code >= 500:
                    raise PaymentProviderTransportError(
                        f"Binance Pay API request failed with HTTP {response.status_code}."
                    )
                if response.status_code < 200 or response.status_code >= 300:
                    raise PaymentProviderError(
                        f"Binance Pay API request failed with HTTP {response.status_code}."
                    )
        except httpx.TimeoutException as exc:
            raise TimeoutError("Binance Pay API request timed out.") from exc
        except httpx.RequestError as exc:
            raise PaymentProviderTransportError("Binance Pay API network request failed.") from exc
        finally:
            if owns_client:
                await client.aclose()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaymentProviderError("Binance Pay returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise PaymentProviderError("Binance Pay response must be a JSON object.")
        if str(payload.get("status") or "").upper() != "SUCCESS" or str(payload.get("code") or "") != "000000":
            raise PaymentProviderError(
                f"Binance Pay API returned business error {payload.get('code')!r}."
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PaymentProviderError("Binance Pay response is missing a data object.")
        return data

    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult:
        if request.amount <= 0:
            raise PaymentProviderError("Binance Pay amount must be positive.")
        currency = request.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise PaymentProviderError("Binance Pay settlement currency must be a three-letter code.")
        trade_no = self._merchant_trade_no(request)
        body: dict[str, Any] = {
            "env": {"terminalType": self._terminal_type},
            "merchantTradeNo": trade_no,
            "orderAmount": float(request.amount),
            "currency": currency,
            "description": self._description,
            "goodsDetails": [
                {
                    "goodsType": self._goods_type,
                    "goodsCategory": self._goods_category,
                    "referenceGoodsId": trade_no,
                    "goodsName": self._goods_name,
                    **({"goodsDetail": self._goods_detail} if self._goods_detail else {}),
                }
            ],
            "orderExpireTime": int((self._time_fn() + self._order_expire_seconds) * 1000),
        }
        context = request.metadata.get("_provider_context")
        if isinstance(context, dict):
            peer = str(context.get("ip") or "").strip()
            if peer and len(peer) <= 128:
                body["env"]["orderClientIp"] = peer
        if request.return_url:
            try:
                parsed = httpx.URL(request.return_url)
            except Exception as exc:
                raise PaymentProviderError("Binance Pay return_url is invalid.") from exc
            if parsed.scheme != "https" or not parsed.host or parsed.username or parsed.password:
                raise PaymentProviderError("Binance Pay return_url must be a credential-free HTTPS URL.")
            body["returnUrl"] = request.return_url
        if self._support_pay_currencies:
            body["supportPayCurrency"] = ",".join(self._support_pay_currencies)

        data = await self._post("/binancepay/openapi/v3/order", body)
        prepay_id = str(data.get("prepayId") or "").strip()
        returned_currency = str(data.get("currency") or "").strip().upper()
        returned_amount = self._decimal(data.get("totalFee"))
        if not prepay_id or len(prepay_id) > 64:
            raise PaymentProviderError("Binance Pay create response is missing a valid prepayId.")
        if returned_currency != currency or returned_amount != request.amount:
            raise PaymentProviderError("Binance Pay create response changed the authoritative amount/currency.")
        checkout = str(data.get("checkoutUrl") or "").strip() or None
        if checkout:
            try:
                parsed_checkout = httpx.URL(checkout)
            except Exception:  # noqa: BLE001
                checkout = None
            else:
                if parsed_checkout.scheme != "https" or not parsed_checkout.host:
                    checkout = None
        safe = {}
        for key in ("prepayId", "terminalType", "expireTime", "qrcodeLink", "qrContent", "checkoutUrl", "universalUrl", "currency", "totalFee"):
            if key in data:
                safe[key] = data[key]
        if "qrContent" in data:
            safe["qr_content"] = str(data.get("qrContent") or "")[:2048]
        return PaymentCreateResult(
            provider_payment_id=prepay_id,
            status=PaymentIntentStatus.PENDING,
            checkout_url=checkout,
            raw_data=safe,
            verification_attributes={
                "merchant_trade_no": trade_no,
                "settlement_currency": currency,
                "settlement_amount": self._canonical_amount(request.amount),
            },
        )

    async def get_payment(self, provider_payment_id: str) -> PaymentDetailsResult:
        prepay_id = provider_payment_id.strip()
        if not prepay_id or len(prepay_id) > 64 or not prepay_id.isalnum():
            raise PaymentProviderError("Binance Pay prepayId is invalid.")
        data = await self._post(
            "/binancepay/openapi/order/query",
            {"merchantTradeNo": None, "prepayId": prepay_id},
        )
        returned_id = str(data.get("prepayId") or "").strip()
        trade_no = str(data.get("merchantTradeNo") or "").strip()
        currency = str(data.get("currency") or "").strip().upper()
        amount = self._decimal(data.get("totalFee"))
        if returned_id != prepay_id or not trade_no:
            raise PaymentProviderError("Binance Pay lookup returned a different payment identity.")
        if amount is None or amount <= 0 or len(currency) != 3 or not currency.isalpha():
            raise PaymentProviderError("Binance Pay lookup is missing authoritative amount/currency.")
        safe = {
            key: data[key]
            for key in (
                "merchantId", "prepayId", "transactionId", "merchantTradeNo", "tradeType",
                "status", "currency", "totalFee", "transactTime", "createTime"
            )
            if key in data
        }
        return PaymentDetailsResult(
            provider_payment_id=prepay_id,
            status=self._status(data.get("status")),
            amount=amount,
            currency=currency,
            raw_data=safe,
            verification_attributes={
                "merchant_trade_no": trade_no,
                "settlement_currency": currency,
                "settlement_amount": self._canonical_amount(amount),
            },
        )

    async def recover_payment_creation(
        self, request: PaymentCreateRequest
    ) -> PaymentDetailsResult:
        """Recover an ambiguous create strictly by merchantTradeNo; never creates a new order."""
        trade_no = self._merchant_trade_no(request)
        data = await self._post(
            "/binancepay/openapi/order/query",
            {"merchantTradeNo": trade_no, "prepayId": None},
        )
        prepay_id = str(data.get("prepayId") or "").strip()
        returned_trade_no = str(data.get("merchantTradeNo") or "").strip()
        currency = str(data.get("currency") or "").strip().upper()
        amount = self._decimal(data.get("totalFee"))
        expected_currency = request.currency.strip().upper()
        if not prepay_id or returned_trade_no != trade_no:
            raise PaymentProviderError("Binance Pay recovery returned a different payment identity.")
        if amount != request.amount or currency != expected_currency:
            raise PaymentProviderError("Binance Pay recovery changed the authoritative payment terms.")
        safe = {
            key: data[key]
            for key in (
                "merchantId", "prepayId", "transactionId", "merchantTradeNo", "tradeType",
                "status", "currency", "totalFee", "transactTime", "createTime"
            )
            if key in data
        }
        return PaymentDetailsResult(
            provider_payment_id=prepay_id,
            status=self._status(data.get("status")),
            amount=amount,
            currency=currency,
            raw_data=safe,
            verification_attributes={
                "merchant_trade_no": returned_trade_no,
                "settlement_currency": currency,
                "settlement_amount": self._canonical_amount(amount),
            },
        )

    async def verify_webhook(
        self,
        payload: bytes | str,
        headers: dict[str, str],
        secret: str,
    ) -> WebhookVerificationResult:
        del payload, headers, secret
        raise UnsupportedProviderCapabilityError(
            "Binance Pay payment webhooks remain disabled until the current notification contract is pinned and regression-vectored."
        )

    async def refund(self, request: PaymentRefundRequest) -> PaymentRefundResult:
        del request
        raise UnsupportedProviderCapabilityError(
            "Binance Pay refunds remain disabled until Phase 11.5 reversal accounting is provider-wired."
        )
