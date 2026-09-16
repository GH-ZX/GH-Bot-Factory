from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

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


class BybitPayProvider(PaymentProvider):
    """Bybit Pay QR adapter with signed REST calls and RSA-signed webhooks.

    Contract basis: Bybit Pay QR Payment v5 documentation. The adapter deliberately
    implements one-time ``E_COMMERCE`` payments only. Refund APIs stay disabled until
    Phase 11.5 reversal accounting is wired to the provider contract.

    Credential secret JSON:
      {"api_key":"...", "api_secret":"...", "merchant_id":"..."}

    ``webhook_secret`` contains Bybit's *platform public key* PEM. Although the generic
    database field is named webhook_secret_ref, this material is a verification key, not
    a signing secret.
    """

    provider_name = "bybit_pay"
    mainnet_base = "https://api.bybit.com"
    testnet_base = "https://api-testnet.bybit.com"
    supports_idempotency_keys = True  # merchantTradeNo is merchant-controlled and unique.
    supports_creation_recovery = True
    supports_payment_lookup = True
    supports_webhooks = True
    supports_refunds = False
    supports_partial_refunds = False
    supports_safe_refund_retries = False

    _MAX_RESPONSE_BYTES = 256 * 1024
    _SAFE_CREATE_FIELDS = frozenset({"payId", "terminalType", "expireTime", "checkoutLink"})
    _SAFE_ORDER_FIELDS = frozenset(
        {
            "merchantId",
            "clientId",
            "paymentType",
            "merchantTradeNo",
            "payId",
            "status",
            "amount",
            "currency",
            "currencyType",
            "createTime",
            "paymentTime",
            "finishTime",
            "remark",
            "chainAddress",
            "chainType",
        }
    )

    def __init__(
        self,
        *,
        settings: dict[str, Any],
        credentials: str,
        webhook_secret: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        time_fn: Any | None = None,
    ) -> None:
        self._settings = dict(settings or {})
        self._api_key, self._api_secret, self._merchant_id = self._parse_credentials(credentials)
        self._webhook_public_key_pem = (webhook_secret or "").strip()
        self._http_client = http_client
        self._time_fn = time_fn or time.time
        self._sandbox = bool(self._settings.get("sandbox", False))
        self._base_url = self.testnet_base if self._sandbox else self.mainnet_base

        timeout = self._number_setting("timeout_seconds", default=15.0, minimum=2.0, maximum=30.0)
        self._timeout_seconds = timeout
        self._recv_window = int(
            self._number_setting("recv_window_ms", default=5000, minimum=1000, maximum=10000)
        )
        self._webhook_tolerance = int(
            self._number_setting(
                "webhook_tolerance_seconds", default=300, minimum=30, maximum=3600
            )
        )

        self._success_url = self._required_https_setting("success_url")
        self._failed_url = self._required_https_setting("failed_url")
        self._webhook_url = self._required_https_setting("webhook_url")
        self._shopping_name = self._bounded_setting("shopping_name", default="GH Bot Factory", limit=120)
        self._goods_name = self._bounded_setting("goods_name", default="Wallet top-up", limit=160)
        self._goods_detail = self._bounded_setting(
            "goods_detail", default="Digital wallet balance top-up", limit=256
        )
        self._mcc_code = self._bounded_setting("mcc_code", default="5816", limit=16)
        self._merchant_name = self._bounded_setting("merchant_name", default="", limit=120) or None
        self._client_id = self._bounded_setting("client_id", default="", limit=128) or None
        terminal = str(self._settings.get("terminal_type") or "WEB").strip().upper()
        if terminal not in {"APP", "WEB", "WAP", "MINIAPP", "OTHERS"}:
            raise PaymentProviderError("Bybit Pay terminal_type is invalid.")
        self._terminal_type = terminal

        currency_types_raw = self._settings.get("currency_types")
        if not isinstance(currency_types_raw, dict) or not currency_types_raw:
            raise PaymentProviderError(
                "Bybit Pay currency_types must explicitly map each settlement currency to fiat or crypto."
            )
        currency_types: dict[str, str] = {}
        for raw_currency, raw_kind in currency_types_raw.items():
            currency = str(raw_currency).strip().upper()
            kind = str(raw_kind).strip().lower()
            if len(currency) != 3 or not currency.isalpha() or kind not in {"fiat", "crypto"}:
                raise PaymentProviderError("Bybit Pay currency_types contains an invalid mapping.")
            currency_types[currency] = kind
        self._currency_types = currency_types

        self._webhook_public_key: rsa.RSAPublicKey | None = None
        if self._webhook_public_key_pem:
            try:
                key = serialization.load_pem_public_key(
                    self._webhook_public_key_pem.encode("utf-8")
                )
            except (TypeError, ValueError) as exc:
                raise PaymentProviderError("Bybit Pay webhook public key PEM is invalid.") from exc
            if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 1024:
                raise PaymentProviderError("Bybit Pay webhook public key must be RSA (>=1024 bits).")
            self._webhook_public_key = key

    @staticmethod
    def _parse_credentials(credentials: str) -> tuple[str, str, str]:
        try:
            payload = json.loads(credentials)
        except json.JSONDecodeError as exc:
            raise PaymentProviderError(
                "Bybit Pay credentials must be encrypted JSON with api_key, api_secret, and merchant_id."
            ) from exc
        if not isinstance(payload, dict):
            raise PaymentProviderError("Bybit Pay credentials JSON must be an object.")
        allowed = {"api_key", "api_secret", "merchant_id"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise PaymentProviderError(f"Unsupported Bybit Pay credential fields: {unknown}.")
        result: list[str] = []
        for key in ("api_key", "api_secret", "merchant_id"):
            value = str(payload.get(key) or "").strip()
            if not value or len(value) > 512:
                raise PaymentProviderError(f"Bybit Pay credential field {key} is missing or invalid.")
            result.append(value)
        return result[0], result[1], result[2]

    def _number_setting(self, key: str, *, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(self._settings.get(key, default))
        except (TypeError, ValueError) as exc:
            raise PaymentProviderError(f"Bybit Pay {key} must be numeric.") from exc
        if not minimum <= value <= maximum:
            raise PaymentProviderError(
                f"Bybit Pay {key} must be between {minimum:g} and {maximum:g}."
            )
        return value

    def _bounded_setting(self, key: str, *, default: str, limit: int) -> str:
        value = str(self._settings.get(key, default) or "").strip()
        if len(value) > limit:
            raise PaymentProviderError(f"Bybit Pay {key} exceeds {limit} characters.")
        return value

    def _required_https_setting(self, key: str) -> str:
        value = str(self._settings.get(key) or "").strip()
        if not value:
            raise PaymentProviderError(f"Bybit Pay {key} is required.")
        try:
            parsed = httpx.URL(value)
        except Exception as exc:
            raise PaymentProviderError(f"Bybit Pay {key} must be a valid HTTPS URL.") from exc
        if parsed.scheme != "https" or not parsed.host or parsed.username or parsed.password:
            raise PaymentProviderError(f"Bybit Pay {key} must be a credential-free HTTPS URL.")
        if len(value) > 256:
            raise PaymentProviderError(f"Bybit Pay {key} exceeds 256 characters.")
        return value

    @staticmethod
    def _strict_json(payload: bytes | str) -> dict[str, Any]:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload

        def reject_constant(value: str) -> None:
            raise ValueError(f"Invalid JSON constant: {value}")

        parsed = json.loads(text, parse_constant=reject_constant)
        if not isinstance(parsed, dict):
            raise ValueError("Bybit Pay payload must be a JSON object.")  # noqa: TRY004 - public configuration validation uses ValueError
        return parsed

    @staticmethod
    def _header(headers: dict[str, str], name: str) -> str | None:
        target = name.lower()
        for key, value in headers.items():
            if key.lower() == target:
                return value
        return None

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return parsed if parsed.is_finite() else None

    @staticmethod
    def _status(raw: Any) -> PaymentIntentStatus:
        status = str(raw or "").strip().upper()
        if status == "INIT":
            return PaymentIntentStatus.PENDING
        if status == "PAY_SUCCESS":
            return PaymentIntentStatus.SUCCEEDED
        if status == "PAY_FAILED":
            return PaymentIntentStatus.FAILED
        if status == "TIMEOUT":
            return PaymentIntentStatus.EXPIRED
        # A refund after wallet settlement requires an explicit reversal workflow.
        if status in {"REFUND_SUCCESS", "REFUND_FAILED"}:
            return PaymentIntentStatus.UNKNOWN
        return PaymentIntentStatus.UNKNOWN

    def _sign(self, timestamp_ms: str, content: str) -> str:
        plain = f"{timestamp_ms}{self._api_key}{self._recv_window}{content}".encode()
        return hmac.new(self._api_secret.encode("utf-8"), plain, hashlib.sha256).hexdigest()

    def _signed_headers(self, timestamp_ms: str, content: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": timestamp_ms,
            "X-BAPI-RECV-WINDOW": str(self._recv_window),
            "X-BAPI-SIGN": self._sign(timestamp_ms, content),
        }

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        body_bytes = b""
        query_string = urlencode(query or [], doseq=False, safe="")
        if body is not None:
            body_bytes = json.dumps(
                body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        signing_content = body_bytes.decode("utf-8") if body is not None else query_string
        timestamp_ms = str(int(self._time_fn() * 1000))
        headers = self._signed_headers(timestamp_ms, signing_content)
        url = f"{self._base_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"

        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds), follow_redirects=False, trust_env=False
        )
        try:
            async with client.stream(
                method,
                url,
                headers=headers,
                content=body_bytes if body is not None else None,
            ) as response:
                if 300 <= response.status_code < 400:
                    raise PaymentProviderError("Bybit Pay returned an unexpected redirect.")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._MAX_RESPONSE_BYTES:
                        raise PaymentProviderError("Bybit Pay response exceeded the allowed size.")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                if response.status_code >= 500:
                    raise PaymentProviderTransportError(
                        f"Bybit Pay API request failed with HTTP {response.status_code}."
                    )
                if response.status_code < 200 or response.status_code >= 300:
                    raise PaymentProviderError(
                        f"Bybit Pay API request failed with HTTP {response.status_code}."
                    )
        except httpx.TimeoutException as exc:
            raise TimeoutError("Bybit Pay API request timed out.") from exc
        except httpx.RequestError as exc:
            raise PaymentProviderTransportError("Bybit Pay API network request failed.") from exc
        finally:
            if owns_client:
                await client.aclose()

        try:
            data = self._strict_json(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise PaymentProviderError("Bybit Pay returned invalid JSON.") from exc
        if data.get("retCode") != 100000:
            raise PaymentProviderError(
                f"Bybit Pay API returned business error {data.get('retCode')!r}."
            )
        result = data.get("result")
        if not isinstance(result, dict):
            raise PaymentProviderError("Bybit Pay response is missing a result object.")
        return result

    @staticmethod
    def _merchant_trade_no(request: PaymentCreateRequest) -> str:
        raw = request.metadata.get("payment_intent_id") or request.order_id
        if not raw:
            raw = request.idempotency_key
        value = str(raw).strip()
        if not value or len(value) > 64:
            raise PaymentProviderError("Bybit Pay merchantTradeNo is invalid.")
        return value

    def _currency_type(self, currency: str) -> str:
        kind = self._currency_types.get(currency)
        if not kind:
            raise PaymentProviderError(
                f"Bybit Pay currency {currency} has no explicit fiat/crypto classification."
            )
        return kind

    @staticmethod
    def _client_context(request: PaymentCreateRequest) -> dict[str, str]:
        raw = request.metadata.get("_provider_context")
        if not isinstance(raw, dict):
            raise PaymentProviderError("Bybit Pay requires trusted client request context.")
        required = ("device", "browser_version", "ip")
        context: dict[str, str] = {}
        for key in required:
            value = str(raw.get(key) or "").strip()
            if not value or len(value) > 512:
                raise PaymentProviderError(f"Bybit Pay client context field {key} is missing or invalid.")
            context[key] = value
        return context

    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult:
        if request.amount <= 0:
            raise PaymentProviderError("Bybit Pay amount must be positive.")
        currency = request.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise PaymentProviderError("Bybit Pay currency must be a three-letter code.")
        merchant_trade_no = self._merchant_trade_no(request)
        currency_type = self._currency_type(currency)
        context = self._client_context(request)

        body: dict[str, Any] = {
            "merchantId": self._merchant_id,
            "paymentType": "E_COMMERCE",
            "merchantTradeNo": merchant_trade_no,
            "goods": [
                {
                    "shoppingName": self._shopping_name,
                    "mccCode": self._mcc_code,
                    "goodsName": self._goods_name,
                    "goodsDetail": self._goods_detail,
                }
            ],
            "orderAmount": format(request.amount, "f"),
            "currency": currency,
            "currencyType": currency_type,
            "successUrl": self._success_url,
            "failedUrl": self._failed_url,
            "webhookUrl": self._webhook_url,
            "env": {
                "terminalType": self._terminal_type,
                "device": context["device"],
                "browserVersion": context["browser_version"],
                "ip": context["ip"],
            },
            "remark": str(request.metadata.get("purpose") or "wallet_topup")[:50],
        }
        if self._merchant_name:
            body["merchantName"] = self._merchant_name
        if self._client_id:
            body["clientId"] = self._client_id

        result = await self._request_json("POST", "/v5/bybitpay/create_pay", body=body)
        pay_id = str(result.get("payId") or "").strip()
        if not pay_id or len(pay_id) > 128:
            raise PaymentProviderError("Bybit Pay create response is missing a valid payId.")
        order = result.get("order")
        if not isinstance(order, dict):
            raise PaymentProviderError("Bybit Pay create response is missing order details.")
        returned_trade_no = str(order.get("merchantTradeNo") or "").strip()
        returned_amount = self._decimal(order.get("amount"))
        returned_currency = str(order.get("currency") or "").strip().upper()
        returned_type = str(order.get("currencyType") or "").strip().lower()
        returned_pay_id = str(order.get("payId") or "").strip()
        if returned_trade_no != merchant_trade_no or returned_pay_id != pay_id:
            raise PaymentProviderError("Bybit Pay create response changed the order identity.")
        if returned_amount != request.amount or returned_currency != currency:
            raise PaymentProviderError("Bybit Pay create response changed the authoritative amount/currency.")
        if returned_type != currency_type:
            raise PaymentProviderError("Bybit Pay create response changed currencyType.")

        checkout = str(result.get("checkoutLink") or "").strip() or None
        if checkout:
            try:
                parsed_checkout = httpx.URL(checkout)
            except Exception:  # noqa: BLE001
                checkout = None
            else:
                if parsed_checkout.scheme != "https" or not parsed_checkout.host:
                    checkout = None

        raw_data = {key: result[key] for key in self._SAFE_CREATE_FIELDS if key in result}
        # Normalize non-web checkout content for storefront rendering without leaking the full
        # provider response. The public storefront allowlist handles these keys explicitly.
        raw_data["provider_checkout_link"] = str(result.get("checkoutLink") or "")[:2048]
        qr_content = str(result.get("qrContent") or "")
        if qr_content and len(qr_content) <= 128 * 1024:
            raw_data["qr_content"] = qr_content

        return PaymentCreateResult(
            provider_payment_id=pay_id,
            status=self._status(order.get("status")),
            checkout_url=checkout,
            raw_data=raw_data,
            verification_attributes={
                "merchant_trade_no": merchant_trade_no,
                "merchant_id": self._merchant_id,
                "currency_type": currency_type,
            },
        )

    async def get_payment(self, provider_payment_id: str) -> PaymentDetailsResult:
        pay_id = provider_payment_id.strip()
        if not pay_id or len(pay_id) > 128:
            raise PaymentProviderError("Bybit Pay payId is invalid.")
        result = await self._request_json(
            "GET",
            "/v5/bybitpay/pay_result",
            query=[
                ("merchantId", self._merchant_id),
                ("paymentType", "E_COMMERCE"),
                ("payId", pay_id),
            ],
        )
        order = result.get("order")
        if not isinstance(order, dict):
            raise PaymentProviderError("Bybit Pay lookup response is missing order details.")
        returned_pay_id = str(order.get("payId") or "").strip()
        merchant_id = str(order.get("merchantId") or "").strip()
        merchant_trade_no = str(order.get("merchantTradeNo") or "").strip()
        amount = self._decimal(order.get("amount"))
        currency = str(order.get("currency") or "").strip().upper()
        currency_type = str(order.get("currencyType") or "").strip().lower()
        if returned_pay_id != pay_id or merchant_id != self._merchant_id:
            raise PaymentProviderError("Bybit Pay lookup returned a different payment identity.")
        if not merchant_trade_no or amount is None or amount <= 0:
            raise PaymentProviderError("Bybit Pay lookup is missing authoritative order fields.")
        if len(currency) != 3 or not currency.isalpha() or currency_type not in {"fiat", "crypto"}:
            raise PaymentProviderError("Bybit Pay lookup contains invalid currency fields.")
        safe_order = {key: order[key] for key in self._SAFE_ORDER_FIELDS if key in order}
        return PaymentDetailsResult(
            provider_payment_id=pay_id,
            status=self._status(order.get("status")),
            amount=amount,
            currency=currency,
            raw_data={"order": safe_order},
            verification_attributes={
                "merchant_trade_no": merchant_trade_no,
                "merchant_id": merchant_id,
                "currency_type": currency_type,
            },
        )

    async def recover_payment_creation(
        self, request: PaymentCreateRequest
    ) -> PaymentDetailsResult:
        """Recover an ambiguous create strictly by merchantTradeNo; never creates a new order."""
        merchant_trade_no = self._merchant_trade_no(request)
        currency = request.currency.strip().upper()
        expected_type = self._currency_type(currency)
        result = await self._request_json(
            "GET",
            "/v5/bybitpay/pay_result",
            query=[
                ("merchantId", self._merchant_id),
                ("paymentType", "E_COMMERCE"),
                ("merchantTradeNo", merchant_trade_no),
            ],
        )
        order = result.get("order")
        if not isinstance(order, dict):
            raise PaymentProviderError("Bybit Pay recovery response is missing order details.")
        pay_id = str(order.get("payId") or "").strip()
        returned_trade_no = str(order.get("merchantTradeNo") or "").strip()
        merchant_id = str(order.get("merchantId") or "").strip()
        amount = self._decimal(order.get("amount"))
        returned_currency = str(order.get("currency") or "").strip().upper()
        currency_type = str(order.get("currencyType") or "").strip().lower()
        if not pay_id or returned_trade_no != merchant_trade_no or merchant_id != self._merchant_id:
            raise PaymentProviderError("Bybit Pay recovery returned a different payment identity.")
        if amount != request.amount or returned_currency != currency or currency_type != expected_type:
            raise PaymentProviderError("Bybit Pay recovery changed the authoritative payment terms.")
        safe_order = {key: order[key] for key in self._SAFE_ORDER_FIELDS if key in order}
        return PaymentDetailsResult(
            provider_payment_id=pay_id,
            status=self._status(order.get("status")),
            amount=amount,
            currency=returned_currency,
            raw_data={"order": safe_order},
            verification_attributes={
                "merchant_trade_no": returned_trade_no,
                "merchant_id": merchant_id,
                "currency_type": currency_type,
            },
        )

    async def verify_webhook(
        self,
        payload: bytes | str,
        headers: dict[str, str],
        secret: str,
    ) -> WebhookVerificationResult:
        public_key_pem = (secret or self._webhook_public_key_pem).strip()
        timestamp = str(self._header(headers, "timestamp") or "").strip()
        signature = str(self._header(headers, "signature") or "").strip()
        if not public_key_pem or not timestamp or not signature:
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="bybit_pay.payment.status",
                raw_data={"error": "Missing Bybit Pay webhook authentication material."},
            )
        try:
            ts = int(timestamp)
        except ValueError:
            ts = 0
        now = int(self._time_fn())
        if ts <= 0 or abs(now - ts) > self._webhook_tolerance:
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="bybit_pay.payment.status",
                raw_data={"error": "Bybit Pay webhook timestamp is outside the allowed window."},
            )
        raw_bytes = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        if len(raw_bytes) > self._MAX_RESPONSE_BYTES:
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="bybit_pay.payment.status",
                raw_data={"error": "Bybit Pay webhook payload exceeds the allowed size."},
            )
        try:
            key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
            if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 1024:
                raise ValueError("invalid RSA key")
            decoded_signature = base64.b64decode(signature, validate=True)
            key.verify(
                decoded_signature,
                timestamp.encode("ascii") + raw_bytes,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            data = self._strict_json(raw_bytes)
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, InvalidSignature):
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="bybit_pay.payment.status",
                raw_data={"error": "Invalid Bybit Pay webhook signature or payload."},
            )

        payment_type = str(data.get("paymentType") or "").strip().upper()
        merchant_id = str(data.get("merchantId") or "").strip()
        pay_id = str(data.get("payId") or "").strip()
        merchant_trade_no = str(data.get("merchantTradeNo") or "").strip()
        raw_status = str(data.get("status") or "").strip().upper()
        amount = self._decimal(data.get("amount"))
        currency = str(data.get("currency") or "").strip().upper()
        currency_type = str(data.get("currencyType") or "").strip().lower()
        if (
            payment_type != "E_COMMERCE"
            or merchant_id != self._merchant_id
            or not pay_id
            or not merchant_trade_no
            or not raw_status
            or amount is None
            or amount <= 0
            or len(currency) != 3
            or not currency.isalpha()
            or currency_type not in {"fiat", "crypto"}
        ):
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="bybit_pay.payment.status",
                raw_data={"error": "Bybit Pay webhook is missing authoritative payment fields."},
            )
        event_time = data.get("finishTime") or data.get("paymentTime") or data.get("createTime") or 0
        event_id = f"{pay_id}:{raw_status}:{event_time}"
        safe = {key: data[key] for key in self._SAFE_ORDER_FIELDS if key in data}
        return WebhookVerificationResult(
            is_valid=True,
            provider_event_id=event_id[:255],
            event_type="bybit_pay.payment.status",
            provider_payment_id=pay_id,
            status=self._status(raw_status),
            amount=amount,
            currency=currency,
            raw_data=safe,
            verification_attributes={
                "merchant_trade_no": merchant_trade_no,
                "merchant_id": merchant_id,
                "currency_type": currency_type,
            },
        )

    async def refund(self, request: PaymentRefundRequest) -> PaymentRefundResult:
        del request
        raise UnsupportedProviderCapabilityError(
            "Bybit Pay refunds remain disabled until Phase 11.5 reversal accounting is provider-wired."
        )
