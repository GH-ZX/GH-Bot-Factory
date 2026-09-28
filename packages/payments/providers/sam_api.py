from __future__ import annotations

import hashlib
import hmac
import json
import logging
from decimal import Decimal
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

logger = logging.getLogger(__name__)


class SamApiProvider(PaymentProvider):
    """Adapter for Sam API (ShamCash / SyriatelCash).

    Provides seamless integration with Syrian payment networks (ShamCash and Syriatel Cash)
    via the official https://www.sam-api.pro gateway. Supports automatic active wallet resolution,
    invoicing, verification, and balance checks.
    """

    provider_name = "sam_api"
    api_base = "https://www.sam-api.pro/api"

    supports_idempotency_keys = False
    supports_payment_lookup = True
    supports_webhooks = True
    supports_refunds = False
    supports_partial_refunds = False
    supports_safe_refund_retries = False

    def __init__(
        self,
        settings: dict[str, Any],
        api_key: str,
        webhook_secret: str | None,
    ) -> None:
        self.settings = settings
        self.api_key = api_key
        self.webhook_secret = webhook_secret

        # Determine the wallet method (shamcash or syriatel) and merchant identifier from settings
        raw_method = str(self.settings.get("method", "shamcash")).lower().strip()
        self.method = "syriatel" if raw_method in ("syriatel", "syriatelcash") else "shamcash"
        self.identifier = self.settings.get("identifier")
        self.api_base = self.settings.get("api_base", "https://www.sam-api.pro/api").rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def resolve_wallet_identifier(self, client: httpx.AsyncClient) -> str:
        """Resolve valid wallet identifier from settings or auto-discover from /v1/wallets."""
        candidate = self.identifier
        if candidate and (len(str(candidate)) >= 8 or "-" in str(candidate)):
            return str(candidate).strip()

        # Query /v1/wallets to automatically discover the merchant's active wallet
        try:
            resp = await client.get(f"{self.api_base}/v1/wallets", headers=self._headers())
            if resp.status_code == 200:
                wallets = resp.json()
                if isinstance(wallets, list):
                    for w in wallets:
                        if w.get("provider") == self.method and w.get("status") in (None, "active"):
                            if self.method == "shamcash":
                                resolved = w.get("walletAddress") or w.get("id") or w.get("accountNumber")
                            else:
                                resolved = w.get("phone") or w.get("walletAddress") or w.get("id")
                            if resolved:
                                self.identifier = str(resolved).strip()
                                return self.identifier
                    # Fallback to first available active wallet if provider-specific not matched
                    if wallets:
                        first = wallets[0]
                        resolved = first.get("walletAddress") or first.get("phone") or first.get("id")
                        if resolved:
                            self.identifier = str(resolved).strip()
                            return self.identifier
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not auto-resolve Sam API wallet identifier: %s", exc)

        if candidate:
            return str(candidate).strip()
        raise PaymentProviderError(
            f"Sam API requires an active '{self.method}' wallet identifier. None found in account or settings."
        )

    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult:
        url = f"{self.api_base}/v1/invoices"

        async with httpx.AsyncClient(timeout=15.0) as client:
            wallet_id = await self.resolve_wallet_identifier(client)

            webhook_url = request.metadata.get("webhook_url") or ""
            payload = {
                "method": self.method,
                "identifier": wallet_id,
                "amount": str(request.amount),
                "currency": request.currency,
                "webhookUrl": webhook_url,
            }

            try:
                response = await client.post(url, json=payload, headers=self._headers())
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPStatusError as e:
                logger.error("Sam API error (%d): %s", e.response.status_code, e.response.text[:300])
                raise PaymentProviderTransportError(f"HTTP Error: {e.response.status_code}") from e
            except Exception as e:
                raise PaymentProviderTransportError(f"Transport Error: {e}") from e

        # Official response returns paymentUrl and invoiceId
        invoice_id = data.get("invoiceId") or data.get("id")
        checkout_url = data.get("paymentUrl") or data.get("url") or data.get("checkoutUrl")

        if not invoice_id and "data" in data and isinstance(data["data"], dict):
            invoice_id = data["data"].get("invoiceId") or data["data"].get("id")
            checkout_url = data["data"].get("paymentUrl") or data["data"].get("url") or data["data"].get("checkoutUrl")

        if not invoice_id:
            raise PaymentProviderError(f"Missing invoiceId in Sam API response: {data}")

        if not checkout_url:
            # Fallback to direct hosted pay link
            base_web = self.api_base.replace("/api", "")
            checkout_url = f"{base_web}/pay/{invoice_id}"

        return PaymentCreateResult(
            provider_payment_id=str(invoice_id),
            status=PaymentIntentStatus.PENDING,
            checkout_url=checkout_url,
            raw_data=data,
        )

    async def get_payment(self, provider_payment_id: str) -> PaymentDetailsResult:
        """Fetch invoice state using GET /pay/{invoiceId} or verify."""
        base_web = self.api_base.replace("/api", "")
        url = f"{base_web}/pay/{provider_payment_id}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
                if response.status_code == 404:
                    # Try under /api if base differs
                    response = await client.get(f"{self.api_base}/pay/{provider_payment_id}", headers={"Accept": "application/json"})
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            raise PaymentProviderTransportError(f"Error fetching Sam API payment {provider_payment_id}: {e}") from e

        raw_status = str(data.get("status", "pending")).lower()
        if raw_status in ("paid", "completed", "success"):
            status = PaymentIntentStatus.SUCCEEDED
        elif raw_status in ("expired", "cancelled", "canceled"):
            status = PaymentIntentStatus.EXPIRED
        elif raw_status in ("failed", "rejected", "error"):
            status = PaymentIntentStatus.FAILED
        else:
            status = PaymentIntentStatus.PENDING

        amount_val = data.get("amount") or "0"
        return PaymentDetailsResult(
            provider_payment_id=provider_payment_id,
            status=status,
            amount=Decimal(str(amount_val)),
            currency=data.get("currency", "SYP"),
            raw_data=data,
        )

    async def get_wallet_balances(self) -> dict[str, float]:
        """Fetch real-time balances for all registered active wallets from SAM API."""
        usd_total = 0.0
        syp_total = 0.0
        eur_total = 0.0

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{self.api_base}/v1/wallets", headers=self._headers())
                if resp.status_code != 200:
                    return {"usd": 0.0, "syp": 0.0, "eur": 0.0}
                wallets = resp.json()
                if not isinstance(wallets, list):
                    return {"usd": 0.0, "syp": 0.0, "eur": 0.0}

                for w in wallets:
                    if w.get("status") not in (None, "active"):
                        continue
                    prov = w.get("provider") or "shamcash"
                    identifier = w.get("walletAddress") or w.get("phone") or w.get("id")
                    if not identifier:
                        continue
                    try:
                        b_resp = await client.get(
                            f"{self.api_base}/v1/wallets/{prov}/{identifier}/balance",
                            headers=self._headers(),
                        )
                        if b_resp.status_code == 200:
                            data = b_resp.json()
                            if isinstance(data, list):
                                for item in data:
                                    curr = str(item.get("currency", "")).upper()
                                    amt = float(item.get("amount") or 0.0)
                                    if curr == "USD":
                                        usd_total += amt
                                    elif curr == "SYP":
                                        syp_total += amt
                                    elif curr == "EUR":
                                        eur_total += amt
                            elif isinstance(data, dict):
                                usd_total += float(data.get("usd") or data.get("USD") or 0.0)
                                syp_total += float(data.get("syp") or data.get("SYP") or 0.0)
                                eur_total += float(data.get("eur") or data.get("EUR") or 0.0)
                    except Exception as err:  # noqa: BLE001
                        logger.warning("Failed balance check for %s: %s", identifier, err)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch SAM wallet list: %s", exc)

        return {"usd": round(usd_total, 2), "syp": round(syp_total, 2), "eur": round(eur_total, 2)}

    async def verify_webhook(
        self,
        payload: bytes | str,
        headers: dict[str, str],
        secret: str,
    ) -> WebhookVerificationResult:
        payload_bytes = payload if isinstance(payload, bytes) else payload.encode("utf-8")

        # Verify HMAC signature if Sam API sends one
        signature = headers.get("x-signature", headers.get("x-sam-signature", ""))
        if secret and signature:
            expected = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return WebhookVerificationResult(is_valid=False, provider_event_id="", event_type="")

        try:
            data = json.loads(payload_bytes)
        except json.JSONDecodeError:
            return WebhookVerificationResult(is_valid=False, provider_event_id="", event_type="")

        invoice_id = data.get("invoiceId") or data.get("id")
        event_type = data.get("event", data.get("status", "unknown"))
        raw_status = str(data.get("status", "pending")).lower()

        if raw_status in ("paid", "completed", "success"):
            status = PaymentIntentStatus.SUCCEEDED
        elif raw_status in ("expired", "cancelled", "canceled"):
            status = PaymentIntentStatus.EXPIRED
        elif raw_status in ("failed", "rejected", "error"):
            status = PaymentIntentStatus.FAILED
        else:
            status = PaymentIntentStatus.PENDING

        amount = Decimal(str(data["amount"])) if "amount" in data and data["amount"] is not None else None

        return WebhookVerificationResult(
            is_valid=True,
            provider_event_id=str(data.get("eventId", invoice_id)),
            event_type=event_type,
            provider_payment_id=str(invoice_id) if invoice_id else None,
            status=status,
            amount=amount,
            currency=data.get("currency"),
            raw_data=data,
        )

    async def refund(self, request: PaymentRefundRequest) -> PaymentRefundResult:
        raise UnsupportedProviderCapabilityError("Sam API does not support programmatic refunds via this adapter.")
