from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

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


class NowPaymentsProvider(PaymentProvider):
    """Conservative NOWPayments direct-payment adapter.

    The adapter deliberately uses ``POST /v1/payment`` rather than hosted invoices so
    every local PaymentIntent receives the upstream ``payment_id`` required for pull
    reconciliation. The upstream deposit instructions are returned as a *sanitized*
    ``raw_data`` subset for the storefront to render.

    Financial safety rules:
    - Only ``finished`` may become ``SUCCEEDED``.
    - ``finished`` additionally requires a complete-payment proof
      (``actually_paid >= pay_amount > 0``); otherwise it becomes ``UNKNOWN``.
    - ``partially_paid`` and ``refunded`` never auto-settle and map to ``UNKNOWN``.
    - price amount/currency are always the authoritative fields exposed to the core;
      crypto ``pay_amount`` is never treated as wallet settlement value.
    - API host is hard-coded; tenant settings cannot override the network destination.
    """

    provider_name = "nowpayments"
    api_base = "https://api.nowpayments.io"
    supports_idempotency_keys = False
    supports_payment_lookup = True
    supports_webhooks = True
    supports_refunds = False
    supports_partial_refunds = False
    supports_safe_refund_retries = False

    _MAX_RESPONSE_BYTES = 256 * 1024
    _DEFAULT_TIMEOUT_SECONDS = 15.0
    _SAFE_RAW_FIELDS = frozenset(
        {
            "payment_id",
            "payment_status",
            "pay_address",
            "payin_extra_id",
            "price_amount",
            "price_currency",
            "pay_amount",
            "actually_paid",
            "pay_currency",
            "purchase_id",
            "order_id",
            "order_description",
            "created_at",
            "updated_at",
            "expiration_estimate_date",
            "outcome_amount",
            "outcome_currency",
        }
    )

    def __init__(
        self,
        *,
        settings: dict[str, Any],
        api_key: str,
        webhook_secret: str | None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = dict(settings or {})
        self._api_key = api_key.strip()
        self._webhook_secret = (webhook_secret or "").strip()
        self._http_client = http_client
        if not self._api_key:
            raise PaymentProviderError("NOWPayments API key is not configured.")

        raw_currencies = self._settings.get("pay_currencies")
        if raw_currencies is None:
            raw_currencies = [self._settings.get("pay_currency")]
        if not isinstance(raw_currencies, list):
            raise PaymentProviderError("NOWPayments pay_currencies must be a list.")
        currencies: list[str] = []
        for raw_currency in raw_currencies:
            currency = str(raw_currency or "").strip().lower()
            if not currency:
                continue
            if len(currency) > 32 or not all(
                char.isalnum() or char in {"_", "-"} for char in currency
            ):
                raise PaymentProviderError("NOWPayments pay currency code is invalid.")
            if currency not in currencies:
                currencies.append(currency)
        if not currencies:
            raise PaymentProviderError(
                "NOWPayments requires at least one explicit pay currency/network code."
            )
        self._pay_currencies = tuple(currencies)
        default_currency = str(
            self._settings.get("default_pay_currency")
            or self._settings.get("pay_currency")
            or currencies[0]
        ).strip().lower()
        if default_currency not in self._pay_currencies:
            raise PaymentProviderError("NOWPayments default_pay_currency must be included in pay_currencies.")
        self._default_pay_currency = default_currency

        callback = str(self._settings.get("ipn_callback_url") or "").strip()
        if callback:
            parsed = httpx.URL(callback)
            if parsed.scheme != "https" or not parsed.host:
                raise PaymentProviderError("NOWPayments ipn_callback_url must be an absolute HTTPS URL.")
        self._ipn_callback_url = callback or None

        timeout_raw = self._settings.get("timeout_seconds", self._DEFAULT_TIMEOUT_SECONDS)
        try:
            timeout_seconds = float(timeout_raw)
        except (TypeError, ValueError) as exc:
            raise PaymentProviderError("NOWPayments timeout_seconds must be numeric.") from exc
        if not 2.0 <= timeout_seconds <= 30.0:
            raise PaymentProviderError("NOWPayments timeout_seconds must be between 2 and 30 seconds.")
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _strict_json_loads(payload: bytes | str) -> dict[str, Any]:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload

        def reject_constant(value: str) -> None:
            raise ValueError(f"Non-standard JSON constant {value!r} is not allowed.")

        value = json.loads(text, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise ValueError("NOWPayments payload must be a JSON object.")  # noqa: TRY004 - public configuration validation uses ValueError
        return value

    @classmethod
    def _canonicalize_for_signature(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: cls._canonicalize_for_signature(value[key])
                for key in sorted(value)
            }
        if isinstance(value, list):
            return [cls._canonicalize_for_signature(item) for item in value]
        return value

    @classmethod
    def _canonical_signature_payload(cls, data: dict[str, Any]) -> bytes:
        canonical = cls._canonicalize_for_signature(data)
        return json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @staticmethod
    def _header(headers: dict[str, str], name: str) -> str | None:
        target = name.lower()
        for key, value in headers.items():
            if key.lower() == target:
                return value
        return None

    @staticmethod
    def _decimal(data: dict[str, Any], field: str) -> Decimal | None:
        value = data.get(field)
        if value is None or value == "":
            return None
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
        if not result.is_finite():
            return None
        return result

    @classmethod
    def _safe_raw(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {key: data[key] for key in cls._SAFE_RAW_FIELDS if key in data}

    def _status(
        self,
        data: dict[str, Any],
        *,
        expected_pay_currency: str | None = None,
    ) -> PaymentIntentStatus:
        raw = str(data.get("payment_status") or "").strip().lower()
        if raw == "waiting":
            return PaymentIntentStatus.PENDING
        if raw in {"confirming", "confirmed", "sending", "spending"}:
            return PaymentIntentStatus.PROCESSING
        if raw == "finished":
            # Never trust the word "finished" alone. A fixed-price direct payment is
            # safe to settle only when NOWPayments reports the expected crypto amount
            # and an actual paid amount meeting/exceeding it.
            pay_amount = self._decimal(data, "pay_amount")
            actually_paid = self._decimal(data, "actually_paid")
            pay_currency = str(data.get("pay_currency") or "").strip().lower()
            expected_currency = (expected_pay_currency or "").strip().lower() or None
            if (
                pay_amount is None
                or actually_paid is None
                or pay_amount <= 0
                or actually_paid < pay_amount
                or pay_currency not in self._pay_currencies
                or (expected_currency is not None and pay_currency != expected_currency)
            ):
                return PaymentIntentStatus.UNKNOWN
            return PaymentIntentStatus.SUCCEEDED
        if raw == "failed":
            return PaymentIntentStatus.FAILED
        if raw == "expired":
            return PaymentIntentStatus.EXPIRED
        if raw in {"cancelled", "canceled"}:
            return PaymentIntentStatus.CANCELLED
        # Partial payments and provider-side refunds require explicit financial
        # resolution; they must never auto-credit or silently become FAILED.
        if raw in {"partially_paid", "refunded"}:
            return PaymentIntentStatus.UNKNOWN
        return PaymentIntentStatus.UNKNOWN

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "x-api-key": self._api_key,
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )
        try:
            async with client.stream(
                method,
                f"{self.api_base}{path}",
                headers=headers,
                json=json_body,
            ) as response:
                if 300 <= response.status_code < 400:
                    raise PaymentProviderError("NOWPayments returned an unexpected redirect.")
                declared = response.headers.get("content-length")
                if declared:
                    try:
                        if int(declared) > self._MAX_RESPONSE_BYTES:
                            raise PaymentProviderError("NOWPayments response exceeded the size limit.")
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._MAX_RESPONSE_BYTES:
                        raise PaymentProviderError("NOWPayments response exceeded the size limit.")
                    chunks.append(chunk)
                body = b"".join(chunks)
                if response.status_code >= 500:
                    request_id = response.headers.get("cf-ray") or response.headers.get("x-request-id")
                    suffix = f" Request id: {request_id}." if request_id else ""
                    raise PaymentProviderTransportError(
                        f"NOWPayments API returned HTTP {response.status_code}.{suffix}"
                    )
                if response.status_code >= 400:
                    request_id = response.headers.get("cf-ray") or response.headers.get("x-request-id")
                    suffix = f" Request id: {request_id}." if request_id else ""
                    raise PaymentProviderError(
                        f"NOWPayments API returned HTTP {response.status_code}.{suffix}"
                    )
        except httpx.TimeoutException as exc:
            raise TimeoutError("NOWPayments API request timed out.") from exc
        except httpx.RequestError as exc:
            raise PaymentProviderTransportError("NOWPayments API network request failed.") from exc
        finally:
            if owns_client:
                await client.aclose()

        try:
            data = self._strict_json_loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise PaymentProviderError("NOWPayments API returned invalid JSON.") from exc
        return data

    @staticmethod
    def _order_reference(request: PaymentCreateRequest) -> str:
        if request.order_id is not None:
            return str(request.order_id)
        intent_id = request.metadata.get("payment_intent_id")
        if intent_id:
            return str(intent_id)
        # This is an application-generated idempotency token, not a payment secret.
        return request.idempotency_key[:128]

    def _requested_pay_currency(self, request: PaymentCreateRequest) -> str:
        requested = str(
            request.metadata.get("provider_pay_currency")
            or request.metadata.get("pay_currency")
            or self._default_pay_currency
        ).strip().lower()
        if requested not in self._pay_currencies:
            raise PaymentProviderError(
                "Requested NOWPayments pay currency is not enabled for this tenant provider configuration."
            )
        return requested

    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult:
        requested_pay_currency = self._requested_pay_currency(request)
        payload: dict[str, Any] = {
            # JSON has no decimal scalar. Python's shortest float rendering preserves
            # the intended two-decimal server-authoritative fiat amount in transit;
            # the response is re-parsed as Decimal and must match before settlement.
            "price_amount": float(request.amount),
            "price_currency": request.currency.lower(),
            "pay_currency": requested_pay_currency,
            "order_id": self._order_reference(request),
            "order_description": str(
                request.metadata.get("order_description")
                or request.metadata.get("purpose")
                or "GH Bot Factory payment"
            )[:255],
        }
        if self._ipn_callback_url:
            payload["ipn_callback_url"] = self._ipn_callback_url

        data = await self._request_json("POST", "/v1/payment", json_body=payload)
        payment_id = str(data.get("payment_id") or "").strip()
        if not payment_id:
            raise PaymentProviderError("NOWPayments create-payment response is missing payment_id.")

        price_amount = self._decimal(data, "price_amount")
        price_currency = str(data.get("price_currency") or "").strip().upper()
        pay_currency = str(data.get("pay_currency") or "").strip().lower()
        if price_amount != request.amount or price_currency != request.currency.upper():
            raise PaymentProviderError("NOWPayments create-payment response changed the authoritative price.")
        if pay_currency != requested_pay_currency:
            raise PaymentProviderError("NOWPayments create-payment response changed pay_currency.")

        return PaymentCreateResult(
            provider_payment_id=payment_id,
            status=self._status(data, expected_pay_currency=requested_pay_currency),
            checkout_url=None,
            raw_data=self._safe_raw(data),
            verification_attributes={"pay_currency": requested_pay_currency},
        )

    async def get_payment(self, provider_payment_id: str) -> PaymentDetailsResult:
        payment_id = str(provider_payment_id).strip()
        if not payment_id or len(payment_id) > 128:
            raise PaymentProviderError("NOWPayments payment id is invalid.")
        data = await self._request_json("GET", f"/v1/payment/{quote(payment_id, safe='')}")
        returned_id = str(data.get("payment_id") or "").strip()
        if returned_id != payment_id:
            raise PaymentProviderError("NOWPayments lookup returned a different payment id.")
        amount = self._decimal(data, "price_amount")
        currency = str(data.get("price_currency") or "").strip().upper()
        if amount is None or amount <= 0 or len(currency) != 3 or not currency.isalpha():
            raise PaymentProviderError("NOWPayments payment lookup is missing authoritative price fields.")
        pay_currency = str(data.get("pay_currency") or "").strip().lower()
        if pay_currency and pay_currency not in self._pay_currencies:
            raise PaymentProviderError("NOWPayments payment lookup returned an unexpected pay_currency.")
        return PaymentDetailsResult(
            provider_payment_id=payment_id,
            status=self._status(data),
            amount=amount,
            currency=currency,
            raw_data=self._safe_raw(data),
            verification_attributes={"pay_currency": pay_currency} if pay_currency else {},
        )

    async def verify_webhook(
        self,
        payload: bytes | str,
        headers: dict[str, str],
        secret: str,
    ) -> WebhookVerificationResult:
        webhook_secret = (secret or self._webhook_secret).strip()
        signature = (self._header(headers, "x-nowpayments-sig") or "").strip().lower()
        if not webhook_secret or len(signature) != 128:
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="payment.status_changed",
                raw_data={"error": "Missing or malformed NOWPayments signature."},
            )
        try:
            int(signature, 16)
            data = self._strict_json_loads(payload)
            canonical = self._canonical_signature_payload(data)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="payment.status_changed",
                raw_data={"error": "Malformed NOWPayments webhook payload."},
            )
        expected = hmac.new(
            webhook_secret.encode("utf-8"),
            canonical,
            hashlib.sha512,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="payment.status_changed",
                raw_data={"error": "Invalid NOWPayments webhook signature."},
            )

        payment_id = str(data.get("payment_id") or "").strip()
        if not payment_id:
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="payment.status_changed",
                raw_data={"error": "NOWPayments webhook is missing payment_id."},
            )
        amount = self._decimal(data, "price_amount")
        currency = str(data.get("price_currency") or "").strip().upper() or None
        if amount is None or amount <= 0 or currency is None:
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="payment.status_changed",
                raw_data={"error": "NOWPayments webhook is missing authoritative price fields."},
            )
        pay_currency = str(data.get("pay_currency") or "").strip().lower()
        if pay_currency and pay_currency not in self._pay_currencies:
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="payment.status_changed",
                raw_data={"error": "NOWPayments webhook pay_currency mismatch."},
            )

        # NOWPayments IPN has no independent event id in the documented payment
        # payload. Hash the canonical signed object so identical retries deduplicate,
        # while a later status/amount update becomes a distinct immutable event.
        event_id = "np_" + hashlib.sha256(canonical).hexdigest()
        return WebhookVerificationResult(
            is_valid=True,
            provider_event_id=event_id,
            event_type="payment.status_changed",
            provider_payment_id=payment_id,
            status=self._status(data),
            amount=amount,
            currency=currency,
            raw_data=self._safe_raw(data),
            verification_attributes={"pay_currency": pay_currency} if pay_currency else {},
        )

    async def refund(self, request: PaymentRefundRequest) -> PaymentRefundResult:
        raise UnsupportedProviderCapabilityError(
            "NOWPayments refunds are not enabled by this adapter; use an explicit financial-resolution workflow."
        )
