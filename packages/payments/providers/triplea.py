from __future__ import annotations

import json
import time
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


class TripleAPaymentProvider(PaymentProvider):
    """Conservative poll-first Triple-A Stablecoin Payments adapter.

    Triple-A documents hosted payment creation and authoritative payment-detail lookup.
    Webhooks are intentionally disabled here until the installation has a current,
    independently verified webhook-authentication contract. Pull reconciliation remains
    fully supported and works on laptop-first deployments without public ingress.

    Credential material is supplied as one encrypted JSON secret containing:
      {"client_id": "...", "client_secret": "...", "merchant_key": "..."}

    Safety rules:
    - API destination is fixed to Triple-A; tenant settings cannot redirect requests.
    - OAuth access tokens are never persisted in database metadata or raw responses.
    - Only the provider status ``good`` may become SUCCEEDED.
    - ``short`` and refund-like states remain UNKNOWN for financial review/reconciliation.
    - Payment lookup must preserve the original order amount/currency before settlement.
    """

    provider_name = "triplea"
    api_base = "https://api.triple-a.io/api/v2"
    supports_idempotency_keys = False
    supports_payment_lookup = True
    supports_webhooks = False
    supports_refunds = False
    supports_partial_refunds = False
    supports_safe_refund_retries = False

    _MAX_RESPONSE_BYTES = 256 * 1024
    _DEFAULT_TIMEOUT_SECONDS = 15.0
    _SAFE_CREATE_FIELDS = frozenset(
        {
            "payment_reference",
            "order_currency",
            "order_amount",
            "expiry_date",
            "hosted_url",
        }
    )
    _SAFE_DETAIL_FIELDS = frozenset(
        {
            "payment_reference",
            "order_currency",
            "order_amount",
            "status",
            "status_date",
            "receive_amount",
            "payment_tier",
            "payment_tier_date",
            "payment_currency",
            "payment_amount",
            "crypto_currency",
            "crypto_amount",
            "display_crypto_currency",
        }
    )

    def __init__(
        self,
        *,
        settings: dict[str, Any],
        credentials: str,
        webhook_secret: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        del webhook_secret
        self._settings = dict(settings or {})
        self._http_client = http_client
        client_id, client_secret, merchant_key = self._parse_credentials(credentials)
        self._client_id = client_id
        self._client_secret = client_secret
        self._merchant_key = merchant_key

        timeout_raw = self._settings.get("timeout_seconds", self._DEFAULT_TIMEOUT_SECONDS)
        try:
            timeout_seconds = float(timeout_raw)
        except (TypeError, ValueError) as exc:
            raise PaymentProviderError("Triple-A timeout_seconds must be numeric.") from exc
        if not 2.0 <= timeout_seconds <= 30.0:
            raise PaymentProviderError("Triple-A timeout_seconds must be between 2 and 30 seconds.")
        self._timeout_seconds = timeout_seconds
        self._sandbox = bool(self._settings.get("sandbox", False))
        self._access_token: str | None = None
        self._token_expires_monotonic = 0.0

    @staticmethod
    def _parse_credentials(credentials: str) -> tuple[str, str, str]:
        try:
            payload = json.loads(credentials)
        except json.JSONDecodeError as exc:
            raise PaymentProviderError(
                "Triple-A credentials must be encrypted JSON with client_id, client_secret, and merchant_key."
            ) from exc
        if not isinstance(payload, dict):
            raise PaymentProviderError("Triple-A credentials JSON must be an object.")
        allowed = {"client_id", "client_secret", "merchant_key"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise PaymentProviderError(f"Unsupported Triple-A credential fields: {unknown}.")
        values: list[str] = []
        for key in ("client_id", "client_secret", "merchant_key"):
            value = str(payload.get(key) or "").strip()
            if not value or len(value) > 512:
                raise PaymentProviderError(f"Triple-A credential field {key} is missing or invalid.")
            values.append(value)
        return values[0], values[1], values[2]

    @staticmethod
    def _canonical_amount(value: Decimal) -> str:
        """Canonical decimal identity for provider verification attributes.

        Provider JSON can legally round-trip ``50.00`` as ``50``. Verification attributes
        are string-compared by the core, so normalize equivalent decimal spellings without
        weakening the authoritative Decimal amount check performed during settlement.
        """
        normalized = value.normalize()
        if normalized == normalized.to_integral():
            return format(normalized.quantize(Decimal(1)), "f")
        return format(normalized, "f")

    @staticmethod
    def _decimal(data: dict[str, Any], field: str) -> Decimal | None:
        value = data.get(field)
        if value is None or value == "":
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return parsed if parsed.is_finite() else None

    @classmethod
    def _safe_raw(cls, data: dict[str, Any], *, create: bool) -> dict[str, Any]:
        allowed = cls._SAFE_CREATE_FIELDS if create else cls._SAFE_DETAIL_FIELDS
        return {key: data[key] for key in allowed if key in data}

    @staticmethod
    def _status(data: dict[str, Any]) -> PaymentIntentStatus:
        raw = str(data.get("status") or "").strip().lower()
        if raw in {"new", "pending", "waiting", "unpaid"}:
            return PaymentIntentStatus.PENDING
        if raw in {"paid", "confirmed", "confirming", "processing"}:
            return PaymentIntentStatus.PROCESSING
        # Triple-A's current merchant guidance states that a transaction marked
        # "Good" is the state at which services may be delivered.
        if raw == "good":
            return PaymentIntentStatus.SUCCEEDED
        if raw in {"expired"}:
            return PaymentIntentStatus.EXPIRED
        if raw in {"cancel", "cancelled", "canceled"}:
            return PaymentIntentStatus.CANCELLED
        if raw in {"invalid", "failed"}:
            return PaymentIntentStatus.FAILED
        # Short payments are documented as not completing and may be refunded;
        # refund-related/unknown states therefore require financial reconciliation.
        if raw in {"short", "refunded", "refund", "refund_pending"}:
            return PaymentIntentStatus.UNKNOWN
        return PaymentIntentStatus.UNKNOWN

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        form_body: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {"Accept": "application/json", **(headers or {})}
        content = None
        data = None
        if json_body is not None:
            request_headers["Content-Type"] = "application/json"
            content = json.dumps(json_body, separators=(",", ":"), allow_nan=False).encode("utf-8")
        elif form_body is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = form_body

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
                headers=request_headers,
                content=content,
                data=data,
            ) as response:
                if 300 <= response.status_code < 400:
                    raise PaymentProviderError("Triple-A returned an unexpected redirect.")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._MAX_RESPONSE_BYTES:
                        raise PaymentProviderError("Triple-A response exceeded the allowed size.")
                if response.status_code >= 500:
                    raise PaymentProviderTransportError(
                        f"Triple-A API request failed with HTTP {response.status_code}."
                    )
                if response.status_code < 200 or response.status_code >= 300:
                    raise PaymentProviderError(
                        f"Triple-A API request failed with HTTP {response.status_code}."
                    )
                try:
                    parsed = json.loads(bytes(body) or b"{}")
                except json.JSONDecodeError as exc:
                    raise PaymentProviderError("Triple-A returned invalid JSON.") from exc
                if not isinstance(parsed, dict):
                    raise PaymentProviderError("Triple-A response must be a JSON object.")
                return parsed
        except httpx.TimeoutException as exc:
            raise TimeoutError("Triple-A API request timed out.") from exc
        except httpx.HTTPError as exc:
            raise PaymentProviderTransportError("Triple-A API network request failed.") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def _bearer_token(self) -> str:
        now = time.monotonic()
        if self._access_token and now < self._token_expires_monotonic:
            return self._access_token
        data = await self._request_json(
            "POST",
            "/oauth/token",
            form_body={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
        )
        token = str(data.get("access_token") or "").strip()
        if not token or len(token) > 8192:
            raise PaymentProviderError("Triple-A OAuth response did not contain a valid access token.")
        try:
            expires_in = int(data.get("expires_in", 3600))
        except (TypeError, ValueError) as exc:
            raise PaymentProviderError("Triple-A OAuth response contains invalid expires_in.") from exc
        if expires_in <= 0:
            raise PaymentProviderError("Triple-A OAuth token expiry must be positive.")
        # Refresh with a one-minute safety margin; short-lived test tokens retain at least 1s.
        ttl = max(1, expires_in - min(60, max(1, expires_in // 10)))
        self._access_token = token
        self._token_expires_monotonic = now + ttl
        return token

    async def _authorized_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = await self._bearer_token()
        return await self._request_json(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"},
            json_body=json_body,
        )

    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult:
        if request.amount <= 0:
            raise PaymentProviderError("Triple-A payment amount must be positive.")
        currency = request.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise PaymentProviderError("Triple-A order currency must be a three-letter code.")

        order_reference = str(
            request.metadata.get("payment_intent_id")
            or request.order_id
            or request.idempotency_key
        ).strip()
        if not order_reference or len(order_reference) > 100:
            raise PaymentProviderError("Triple-A order reference is invalid.")

        body: dict[str, Any] = {
            "type": "triplea",
            "merchant_key": self._merchant_key,
            "order_currency": currency,
            "order_amount": str(request.amount),
            "order_id": order_reference,
        }
        if self._sandbox:
            body["sandbox"] = True
        if request.return_url:
            parsed = httpx.URL(request.return_url)
            if parsed.scheme != "https" or not parsed.host or parsed.username or parsed.password:
                raise PaymentProviderError("Triple-A return_url must be a credential-free HTTPS URL.")
            body["success_url"] = request.return_url
            body["cancel_url"] = request.return_url

        data = await self._authorized_json("POST", "/payment", json_body=body)
        reference = str(data.get("payment_reference") or "").strip()
        if not reference or len(reference) > 100:
            raise PaymentProviderError("Triple-A create response did not contain a valid payment_reference.")
        hosted_url = str(data.get("hosted_url") or "").strip().strip("`") or None
        returned_amount = self._decimal(data, "order_amount")
        returned_currency = str(data.get("order_currency") or "").strip().upper()
        if returned_amount is not None and returned_amount != request.amount:
            raise PaymentProviderError("Triple-A create response order amount does not match the request.")
        if returned_currency and returned_currency != currency:
            raise PaymentProviderError("Triple-A create response order currency does not match the request.")

        return PaymentCreateResult(
            provider_payment_id=reference,
            status=PaymentIntentStatus.PENDING,
            checkout_url=hosted_url,
            raw_data=self._safe_raw(data, create=True),
            verification_attributes={
                "order_currency": currency.lower(),
                "order_amount": self._canonical_amount(request.amount),
            },
        )

    async def get_payment(self, provider_payment_id: str) -> PaymentDetailsResult:
        reference = provider_payment_id.strip()
        if not reference or len(reference) > 100:
            raise PaymentProviderError("Triple-A payment reference is invalid.")
        data = await self._authorized_json(
            "GET", f"/payment/{quote(reference, safe='')}?verbose=0"
        )
        returned_reference = str(data.get("payment_reference") or "").strip()
        if returned_reference and returned_reference != reference:
            raise PaymentProviderError("Triple-A payment lookup returned a different payment reference.")
        amount = self._decimal(data, "order_amount")
        currency = str(data.get("order_currency") or "").strip().upper()
        if amount is None or amount <= 0:
            raise PaymentProviderError("Triple-A payment details contain an invalid order amount.")
        if len(currency) != 3 or not currency.isalpha():
            raise PaymentProviderError("Triple-A payment details contain an invalid order currency.")
        return PaymentDetailsResult(
            provider_payment_id=reference,
            status=self._status(data),
            amount=amount,
            currency=currency,
            raw_data=self._safe_raw(data, create=False),
            verification_attributes={
                "order_currency": currency.lower(),
                "order_amount": self._canonical_amount(amount),
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
            "Triple-A webhooks are intentionally disabled until a current signature contract is verified."
        )

    async def refund(self, request: PaymentRefundRequest) -> PaymentRefundResult:
        del request
        raise UnsupportedProviderCapabilityError(
            "Triple-A refunds are not enabled by this adapter; use the provider dashboard/manual financial resolution."
        )
