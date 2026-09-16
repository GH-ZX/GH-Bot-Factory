from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx

from packages.payments.economics_models import FlexibleDepositStatus
from packages.payments.exceptions import (
    PaymentProviderError,
    PaymentProviderTransportError,
    UnsupportedProviderCapabilityError,
)
from packages.payments.providers.interface import (
    FlexibleDepositCreateRequest,
    FlexibleDepositResult,
    PaymentCreateRequest,
    PaymentCreateResult,
    PaymentDetailsResult,
    PaymentProvider,
    PaymentRefundRequest,
    PaymentRefundResult,
    WebhookVerificationResult,
)
from packages.payments.state_machine import PaymentIntentStatus


class GoZaPayProvider(PaymentProvider):
    """Conservative GoZaPay stablecoin gateway adapter.

    GoZaPay supports open/flexible invoices upstream, but this adapter intentionally does
    not auto-credit those invoices yet. GH Bot Factory's current wallet ledger is fiat-
    denominated, while a flexible GoZaPay invoice is denominated by the stablecoin the
    payer chooses. Auto-crediting it as USD would silently assume stablecoin/USD parity.
    That conversion requires an explicit multi-asset/FX contract and is therefore kept
    fail-closed for now.

    Fixed invoices are supported in an explicit nominal stablecoin-parity mode. The
    merchant must acknowledge that policy in provider settings. Even then, settlement is
    delayed until GoZaPay reports ``settled`` rather than merely ``paid`` so the provider's
    clearing window can catch chain reorganisations before wallet credit is released.
    """

    provider_name = "gozapay"
    api_base = "https://gozapay.com/api/v1"
    supports_idempotency_keys = True
    supports_payment_lookup = True
    supports_webhooks = True
    supports_refunds = False
    supports_partial_refunds = False
    supports_safe_refund_retries = False
    supports_flexible_deposits = True

    _MAX_RESPONSE_BYTES = 256 * 1024
    _DEFAULT_TIMEOUT_SECONDS = 15.0
    _DEFAULT_WEBHOOK_TOLERANCE_SECONDS = 300
    _ALLOWED_CHAINS = frozenset({"ethereum", "bsc", "polygon", "tron"})
    _ALLOWED_COINS = frozenset({"USDT", "USDC"})
    _SAFE_RAW_FIELDS = frozenset(
        {
            "id",
            "order_id",
            "chain",
            "coin",
            "expected_amount",
            "unique_amount",
            "amount_received",
            "fee_amount",
            "decimals",
            "deposit_address",
            "status",
            "tx_hash",
            "payment_url",
            "expires_at",
            "created_at",
            "flexible",
            "settled",
            "clearing_at",
            "review_reason",
        }
    )

    def __init__(
        self,
        *,
        settings: dict[str, Any],
        api_key: str,
        webhook_secret: str | None,
        http_client: httpx.AsyncClient | None = None,
        time_fn: Any | None = None,
    ) -> None:
        self._settings = dict(settings or {})
        self._api_key = api_key.strip()
        self._webhook_secret = (webhook_secret or "").strip()
        self._http_client = http_client
        self._time_fn = time_fn or time.time
        if not self._api_key:
            raise PaymentProviderError("GoZaPay API key is not configured.")

        self._chain = str(self._settings.get("chain") or "tron").strip().lower()
        self._coin = str(self._settings.get("coin") or "USDT").strip().upper()
        if self._chain not in self._ALLOWED_CHAINS:
            raise PaymentProviderError("GoZaPay chain must be one of ethereum, bsc, polygon, or tron.")
        if self._coin not in self._ALLOWED_COINS:
            raise PaymentProviderError("GoZaPay coin must be USDT or USDC.")

        self._experimental_risk_acknowledged = bool(
            self._settings.get("experimental_risk_acknowledged", False)
        )
        if not self._experimental_risk_acknowledged:
            raise PaymentProviderError(
                "GoZaPay is treated as an experimental gateway and requires experimental_risk_acknowledged=true before use."
            )

        self._parity_acknowledged = bool(
            self._settings.get("nominal_stablecoin_parity_acknowledged", False)
        )

        timeout_raw = self._settings.get("timeout_seconds", self._DEFAULT_TIMEOUT_SECONDS)
        tolerance_raw = self._settings.get(
            "webhook_tolerance_seconds", self._DEFAULT_WEBHOOK_TOLERANCE_SECONDS
        )
        try:
            self._timeout_seconds = float(timeout_raw)
            self._webhook_tolerance_seconds = int(tolerance_raw)
        except (TypeError, ValueError) as exc:
            raise PaymentProviderError("GoZaPay timeout/webhook tolerance settings are invalid.") from exc
        if not 2 <= self._timeout_seconds <= 30:
            raise PaymentProviderError("GoZaPay timeout_seconds must be between 2 and 30 seconds.")
        if not 30 <= self._webhook_tolerance_seconds <= 3600:
            raise PaymentProviderError(
                "GoZaPay webhook_tolerance_seconds must be between 30 and 3600 seconds."
            )

        self._callback_url = self._validated_optional_https_url(
            self._settings.get("callback_url"), "callback_url"
        )
        self._redirect_url = self._validated_optional_https_url(
            self._settings.get("redirect_url"), "redirect_url"
        )

    @staticmethod
    def _validated_optional_https_url(value: Any, field: str) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = httpx.URL(raw)
        if parsed.scheme != "https" or not parsed.host or parsed.username or parsed.password:
            raise PaymentProviderError(f"GoZaPay {field} must be an absolute HTTPS URL without credentials.")
        return raw

    @staticmethod
    def _strict_json_loads(payload: bytes | str) -> dict[str, Any]:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload

        def reject_constant(value: str) -> None:
            raise ValueError(f"Non-standard JSON constant {value!r} is not allowed.")

        value = json.loads(text, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise ValueError("GoZaPay payload must be a JSON object.")  # noqa: TRY004 - public configuration validation uses ValueError
        return value

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
        except (InvalidOperation, TypeError, ValueError):
            return None
        return result if result.is_finite() else None

    @classmethod
    def _safe_raw(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {key: data[key] for key in cls._SAFE_RAW_FIELDS if key in data}

    def _assert_identity(self, data: dict[str, Any]) -> None:
        chain = str(data.get("chain") or "").strip().lower()
        coin = str(data.get("coin") or "").strip().upper()
        # Fiat-selection invoices can be temporarily unassigned, but this adapter creates
        # pinned coin/network invoices only, so missing or changed values are corruption.
        if chain != self._chain or coin != self._coin:
            raise PaymentProviderError("GoZaPay payment changed the configured chain/coin identity.")
        if bool(data.get("flexible", False)):
            raise PaymentProviderError("Flexible GoZaPay invoices are not eligible for automatic wallet credit.")

    def _status(self, data: dict[str, Any]) -> PaymentIntentStatus:
        raw = str(data.get("status") or "").strip().lower()
        if raw in {"awaiting_selection", "pending"}:
            return PaymentIntentStatus.PENDING
        if raw in {"unconfirmed", "paid"}:
            # Deliberately wait through GoZaPay's clearing window before settlement.
            return PaymentIntentStatus.PROCESSING
        if raw == "settled":
            expected = self._decimal(data, "unique_amount") or self._decimal(data, "expected_amount")
            received = self._decimal(data, "amount_received")
            if expected is None or received is None or expected <= 0 or received != expected:
                return PaymentIntentStatus.UNKNOWN
            return PaymentIntentStatus.SUCCEEDED
        if raw == "expired":
            return PaymentIntentStatus.EXPIRED
        if raw == "cancelled":
            return PaymentIntentStatus.CANCELLED
        # Under/over-payments, reviews, reversals, and unknown states require explicit
        # financial resolution. Never auto-credit them.
        if raw in {"underpaid", "overpaid", "under_review", "reversed"}:
            return PaymentIntentStatus.UNKNOWN
        return PaymentIntentStatus.UNKNOWN

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json", "X-API-Key": self._api_key}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key[:100]

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
                if response.is_redirect:
                    raise PaymentProviderError("GoZaPay API redirects are not accepted.")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._MAX_RESPONSE_BYTES:
                        raise PaymentProviderError("GoZaPay response exceeded the size limit.")
                    chunks.append(chunk)
                body = b"".join(chunks)
                if response.status_code >= 500:
                    raise PaymentProviderTransportError(
                        f"GoZaPay API returned HTTP {response.status_code}."
                    )
                if response.status_code >= 400:
                    raise PaymentProviderError(f"GoZaPay API returned HTTP {response.status_code}.")
        except httpx.TimeoutException as exc:
            raise TimeoutError("GoZaPay API request timed out.") from exc
        except httpx.RequestError as exc:
            raise PaymentProviderTransportError("GoZaPay API network request failed.") from exc
        finally:
            if owns_client:
                await client.aclose()

        try:
            envelope = self._strict_json_loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise PaymentProviderError("GoZaPay API returned invalid JSON.") from exc
        if envelope.get("success") is not True:
            raise PaymentProviderError("GoZaPay API returned an unsuccessful response.")
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise PaymentProviderError("GoZaPay API response is missing data.")
        return data

    @staticmethod
    def _order_reference(request: PaymentCreateRequest) -> str:
        intent_id = request.metadata.get("payment_intent_id")
        if intent_id:
            return str(intent_id)
        if request.order_id is not None:
            return str(request.order_id)
        return request.idempotency_key[:100]

    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult:
        if not self._parity_acknowledged:
            raise PaymentProviderError(
                "GoZaPay fixed wallet top-ups require explicit nominal_stablecoin_parity_acknowledged=true."
            )
        # Current GHBF wallets are fiat-denominated. This explicit guard prevents a
        # silent USDT/USDC -> USD conversion policy from appearing by accident.
        if request.currency.upper() != "USD":
            raise PaymentProviderError("GoZaPay nominal stablecoin top-ups currently support USD ledger credit only.")
        if request.metadata.get("flexible") is True or request.metadata.get("provider_flexible") is True:
            raise UnsupportedProviderCapabilityError(
                "GoZaPay flexible invoices are intentionally not auto-credited until GHBF has explicit multi-asset/FX ledger semantics."
            )

        payload: dict[str, Any] = {
            "amount": format(request.amount, "f"),
            "chain": self._chain,
            "coin": self._coin,
            "order_id": self._order_reference(request),
            "product_name": str(request.metadata.get("product_name") or "Wallet top-up")[:120],
        }
        redirect = request.return_url or self._redirect_url
        if redirect:
            payload["redirect_url"] = self._validated_optional_https_url(redirect, "redirect_url")
        if self._callback_url:
            payload["callback_url"] = self._callback_url

        data = await self._request_json(
            "POST",
            "/invoices",
            json_body=payload,
            idempotency_key=request.idempotency_key,
        )
        invoice_id = str(data.get("id") or "").strip()
        if not invoice_id:
            raise PaymentProviderError("GoZaPay create-invoice response is missing invoice id.")
        self._assert_identity(data)
        expected = self._decimal(data, "expected_amount")
        unique = self._decimal(data, "unique_amount") or expected
        if expected != request.amount or unique != request.amount:
            raise PaymentProviderError("GoZaPay create-invoice response changed the authoritative amount.")
        payment_url = str(data.get("payment_url") or "").strip() or None
        return PaymentCreateResult(
            provider_payment_id=invoice_id,
            status=self._status(data),
            checkout_url=payment_url,
            raw_data=self._safe_raw(data),
            verification_attributes={
                "chain": self._chain,
                "coin": self._coin.lower(),
                "order_id": self._order_reference(request).lower(),
                "parity_mode": "nominal_usd",
            },
        )

    async def get_payment(self, provider_payment_id: str) -> PaymentDetailsResult:
        invoice_id = str(provider_payment_id).strip()
        if not invoice_id or len(invoice_id) > 128:
            raise PaymentProviderError("GoZaPay invoice id is invalid.")
        data = await self._request_json("GET", f"/invoices/{quote(invoice_id, safe='')}")
        returned_id = str(data.get("id") or "").strip()
        if returned_id != invoice_id:
            raise PaymentProviderError("GoZaPay lookup returned a different invoice id.")
        self._assert_identity(data)
        amount = self._decimal(data, "unique_amount") or self._decimal(data, "expected_amount")
        if amount is None or amount <= 0:
            raise PaymentProviderError("GoZaPay lookup is missing the authoritative invoice amount.")
        order_id = str(data.get("order_id") or "").strip().lower()
        return PaymentDetailsResult(
            provider_payment_id=invoice_id,
            status=self._status(data),
            amount=amount,
            currency="USD",
            raw_data=self._safe_raw(data),
            verification_attributes={
                "chain": self._chain,
                "coin": self._coin.lower(),
                **({"order_id": order_id} if order_id else {}),
                "parity_mode": "nominal_usd",
            },
        )

    async def verify_webhook(
        self,
        payload: bytes | str,
        headers: dict[str, str],
        secret: str,
    ) -> WebhookVerificationResult:
        body = payload.encode("utf-8") if isinstance(payload, str) else payload
        webhook_secret = (secret or self._webhook_secret).strip()
        signature = (self._header(headers, "X-Signature") or "").strip().lower()
        timestamp = (self._header(headers, "X-Timestamp") or "").strip()
        webhook_id = (self._header(headers, "X-Webhook-Id") or "").strip()
        invalid = lambda message: WebhookVerificationResult(
            is_valid=False,
            provider_event_id="",
            event_type="gozapay.invoice.event",
            raw_data={"error": message},
        )
        if not webhook_secret or not signature.startswith("sha256=") or not timestamp or not webhook_id:
            return invalid("Missing GoZaPay webhook authentication headers.")
        if len(webhook_id) > 128:
            return invalid("GoZaPay webhook delivery id is invalid.")
        try:
            timestamp_int = int(timestamp)
        except ValueError:
            return invalid("GoZaPay webhook timestamp is invalid.")
        if abs(int(self._time_fn()) - timestamp_int) > self._webhook_tolerance_seconds:
            return invalid("GoZaPay webhook timestamp is outside the replay window.")
        supplied_hex = signature[7:]
        if len(supplied_hex) != 64:
            return invalid("GoZaPay webhook signature is malformed.")
        try:
            int(supplied_hex, 16)
            data = self._strict_json_loads(body)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return invalid("GoZaPay webhook payload is malformed.")
        expected = hmac.new(
            webhook_secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied_hex, expected):
            return invalid("GoZaPay webhook signature is invalid.")

        invoice_id = str(data.get("invoice_id") or "").strip()
        event_type = str(data.get("event") or "gozapay.invoice.event").strip()
        if not invoice_id:
            return invalid("GoZaPay webhook is missing invoice_id.")
        chain = str(data.get("chain") or "").strip().lower()
        coin = str(data.get("coin") or "").strip().upper()
        if chain != self._chain or coin != self._coin:
            return invalid("GoZaPay webhook chain/coin identity does not match configuration.")
        if bool(data.get("flexible", False)):
            return invalid("Flexible GoZaPay webhook cannot auto-credit a fiat wallet.")
        amount = self._decimal(data, "unique_amount") or self._decimal(data, "expected_amount")
        if amount is None or amount <= 0:
            return invalid("GoZaPay webhook is missing the authoritative invoice amount.")
        # The documented webhook stream reports paid/underpaid/overpaid/review/reversed,
        # not the later clearing-settled transition. A signed paid webhook therefore
        # advances only to PROCESSING; the pull worker observes settled before credit.
        status_data = dict(data)
        status_data["status"] = str(data.get("status") or "").strip().lower()
        order_id = str(data.get("order_id") or "").strip().lower()
        return WebhookVerificationResult(
            is_valid=True,
            provider_event_id=webhook_id,
            event_type=event_type,
            provider_payment_id=invoice_id,
            status=self._status(status_data),
            amount=amount,
            currency="USD",
            raw_data=self._safe_raw(status_data),
            verification_attributes={
                "chain": self._chain,
                "coin": self._coin.lower(),
                **({"order_id": order_id} if order_id else {}),
                "parity_mode": "nominal_usd",
            },
        )

    @staticmethod
    def _flexible_status(data: dict[str, Any]) -> FlexibleDepositStatus:
        raw = str(data.get("status") or "").strip().lower()
        if raw in {"awaiting_selection", "pending"}:
            return FlexibleDepositStatus.PENDING
        if raw in {"unconfirmed", "paid", "under_review"}:
            return FlexibleDepositStatus.PROCESSING
        if raw == "settled":
            received = GoZaPayProvider._decimal(data, "amount_received")
            return (
                FlexibleDepositStatus.SETTLED_REVIEW
                if received is not None and received > 0
                else FlexibleDepositStatus.UNKNOWN
            )
        if raw == "expired":
            return FlexibleDepositStatus.EXPIRED
        if raw == "cancelled":
            return FlexibleDepositStatus.CANCELLED
        if raw == "reversed":
            return FlexibleDepositStatus.REVERSED
        # Open-amount deposits do not have under/overpayment semantics. If upstream emits
        # either anyway, refuse automated settlement and require explicit review.
        if raw in {"underpaid", "overpaid"}:
            return FlexibleDepositStatus.UNKNOWN
        return FlexibleDepositStatus.UNKNOWN

    def _flexible_result(self, data: dict[str, Any], *, expected_id: str | None = None) -> FlexibleDepositResult:
        invoice_id = str(data.get("id") or "").strip()
        if not invoice_id:
            raise PaymentProviderError("GoZaPay flexible invoice response is missing invoice id.")
        if expected_id is not None and invoice_id != expected_id:
            raise PaymentProviderError("GoZaPay flexible lookup returned a different invoice id.")
        if data.get("flexible") is not True:
            raise PaymentProviderError("GoZaPay invoice is not a flexible/open-amount deposit.")
        chain = str(data.get("chain") or "").strip().lower() or None
        coin = str(data.get("coin") or "").strip().upper() or None
        if chain is not None and chain not in self._ALLOWED_CHAINS:
            raise PaymentProviderError("GoZaPay flexible invoice selected an unsupported network.")
        if coin is not None and coin not in self._ALLOWED_COINS:
            raise PaymentProviderError("GoZaPay flexible invoice selected an unsupported asset.")
        received = self._decimal(data, "amount_received")
        fee = self._decimal(data, "fee_amount")
        if received is not None and received < 0:
            raise PaymentProviderError("GoZaPay flexible invoice returned a negative received amount.")
        if fee is not None and fee < 0:
            raise PaymentProviderError("GoZaPay flexible invoice returned a negative fee amount.")
        return FlexibleDepositResult(
            provider_deposit_id=invoice_id,
            status=self._flexible_status(data),
            checkout_url=str(data.get("payment_url") or "").strip() or None,
            asset=coin,
            network=chain.upper() if chain else None,
            amount_received=received,
            fee_amount=fee,
            raw_data=self._safe_raw(data),
        )

    async def create_flexible_deposit(
        self, request: FlexibleDepositCreateRequest
    ) -> FlexibleDepositResult:
        payload: dict[str, Any] = {
            "flexible": True,
            "order_id": request.order_reference[:100],
            "product_name": str(request.metadata.get("product_name") or "Wallet deposit")[:120],
        }
        redirect = request.return_url or self._redirect_url
        if redirect:
            payload["redirect_url"] = self._validated_optional_https_url(redirect, "redirect_url")
        if self._callback_url:
            payload["callback_url"] = self._callback_url
        data = await self._request_json(
            "POST",
            "/invoices",
            json_body=payload,
            idempotency_key=request.idempotency_key,
        )
        return self._flexible_result(data)

    async def get_flexible_deposit(self, provider_deposit_id: str) -> FlexibleDepositResult:
        invoice_id = str(provider_deposit_id).strip()
        if not invoice_id or len(invoice_id) > 128:
            raise PaymentProviderError("GoZaPay flexible invoice id is invalid.")
        data = await self._request_json("GET", f"/invoices/{quote(invoice_id, safe='')}")
        return self._flexible_result(data, expected_id=invoice_id)

    async def refund(self, request: PaymentRefundRequest) -> PaymentRefundResult:
        raise UnsupportedProviderCapabilityError(
            "GoZaPay refunds are not enabled by this adapter; use explicit financial resolution."
        )
