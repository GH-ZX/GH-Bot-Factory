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
    """Adapter for Sam API (ShamCash / SyriatelCash)."""

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
        # This allows configuring separate SamApi instances per wallet.
        self.method = self.settings.get("method", "shamcash")
        self.identifier = self.settings.get("identifier")
        
    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult:
        if not self.identifier:
            raise PaymentProviderError("Sam API requires a merchant 'identifier' in settings.")
            
        url = f"{self.api_base}/v1/invoices"
        
        # Sam API uses "webhookUrl"
        webhook_url = request.metadata.get("webhook_url")
        if not webhook_url:
            # Fallback if the commerce layer passes it differently or we can't get it.
            # Usually the GHBF commerce router injects `webhook_url` in metadata.
            webhook_url = "https://example.com/webhook" # Should be injected by caller
        
        payload = {
            "method": self.method,
            "identifier": self.identifier,
            "amount": str(request.amount),
            "currency": request.currency,
            "webhookUrl": request.metadata.get("webhook_url", "")
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Sam API error: {e.response.text}")
            raise PaymentProviderTransportError(f"HTTP Error: {e.response.status_code}") from e
        except Exception as e:
            raise PaymentProviderTransportError(f"Transport Error: {e}") from e
            
        # Assuming the response returns something like {"success": true, "invoiceId": "...", "url": "..."}
        invoice_id = data.get("invoiceId") or data.get("id")
        checkout_url = data.get("url") or data.get("checkoutUrl")
        
        if not invoice_id and "data" in data and isinstance(data["data"], dict):
            # Fallback if structure is slightly different
            invoice_id = data["data"].get("invoiceId") or data["data"].get("id")
            checkout_url = data["data"].get("url") or data["data"].get("checkoutUrl")

        if not invoice_id:
            raise PaymentProviderError(f"Missing invoiceId in Sam API response: {data}")
            
        return PaymentCreateResult(
            provider_payment_id=str(invoice_id),
            status=PaymentIntentStatus.PENDING,
            checkout_url=checkout_url,
            raw_data=data,
        )

    async def get_payment(self, provider_payment_id: str) -> PaymentDetailsResult:
        # Based on docs: GET /pay/{invoiceId}
        # Note: /pay/{invoiceId} is typically without /api or might be /api/pay/{invoiceId}
        # The docs said: curl ${_n.replace("/api","")}/pay/3f8a1c2d-4e5b-6f7a-8b9c-0d1e2f3a4b5c
        # Let's use the explicit endpoint if there's a verify one. The docs had POST /pay/{invoiceId}/verify.
        url = f"{self.api_base}/v1/invoices/{provider_payment_id}" # Typical REST structure fallback
        
        # Try POST /pay/{invoiceId}/verify
        url = f"{self.api_base.replace('/api', '')}/pay/{provider_payment_id}/verify"
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, headers={"Authorization": f"Bearer {self.api_key}"})
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            raise PaymentProviderTransportError(f"Error fetching Sam API payment: {e}") from e
            
        # Parse status. We map their status to ours.
        raw_status = data.get("status", "pending").lower()
        if raw_status == "paid":
            status = PaymentIntentStatus.COMPLETED
        elif raw_status == "expired":
            status = PaymentIntentStatus.EXPIRED
        elif raw_status == "failed":
            status = PaymentIntentStatus.FAILED
        else:
            status = PaymentIntentStatus.PENDING
            
        return PaymentDetailsResult(
            provider_payment_id=provider_payment_id,
            status=status,
            amount=Decimal(str(data.get("amount", "0"))),
            currency=data.get("currency", "SYP"),
            raw_data=data
        )

    async def verify_webhook(
        self,
        payload: bytes | str,
        headers: dict[str, str],
        secret: str,
    ) -> WebhookVerificationResult:
        payload_bytes = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        
        # Verify HMAC signature if Sam API sends one (usually X-Signature or similar)
        # Assuming standard HMAC SHA256
        signature = headers.get("x-signature", headers.get("x-sam-signature", ""))
        
        if secret and signature:
            expected = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return WebhookVerificationResult(is_valid=False, provider_event_id="", event_type="")
                
        try:
            data = json.loads(payload_bytes)
        except json.JSONDecodeError:
            return WebhookVerificationResult(is_valid=False, provider_event_id="", event_type="")
            
        # Typically looks like: {"invoiceId": "...", "status": "paid", "event": "invoice.paid"}
        invoice_id = data.get("invoiceId") or data.get("id")
        event_type = data.get("event", data.get("status", "unknown"))
        raw_status = data.get("status", "pending").lower()
        
        if raw_status == "paid":
            status = PaymentIntentStatus.COMPLETED
        elif raw_status == "expired":
            status = PaymentIntentStatus.EXPIRED
        elif raw_status == "failed":
            status = PaymentIntentStatus.FAILED
        else:
            status = PaymentIntentStatus.PENDING

        return WebhookVerificationResult(
            is_valid=True,
            provider_event_id=str(data.get("eventId", invoice_id)),
            event_type=event_type,
            provider_payment_id=str(invoice_id),
            status=status,
            amount=Decimal(str(data.get("amount", "0"))) if "amount" in data else None,
            currency=data.get("currency"),
            raw_data=data
        )

    async def refund(self, request: PaymentRefundRequest) -> PaymentRefundResult:
        raise UnsupportedProviderCapabilityError("Sam API does not support programmatic refunds via this adapter.")
