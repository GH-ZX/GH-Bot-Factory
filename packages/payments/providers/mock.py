import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

from packages.payments.exceptions import (
    PaymentProviderError,
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


class MockPaymentProvider(PaymentProvider):
    """Deterministic, configurable mock payment provider for unit & integration testing."""

    def __init__(
        self,
        provider_name: str = "mock",
        supports_idempotency_keys: bool = True,
        supports_payment_lookup: bool = True,
        supports_webhooks: bool = True,
        supports_refunds: bool = True,
        supports_partial_refunds: bool = True,
        simulate_network_error: bool = False,
        simulate_timeout: bool = False,
        simulate_lookup_timeout: bool = False,
        simulate_failure_on_create: bool = False,
        default_create_status: PaymentIntentStatus = PaymentIntentStatus.PENDING,
    ) -> None:
        self.provider_name = provider_name
        self.supports_idempotency_keys = supports_idempotency_keys
        self.supports_payment_lookup = supports_payment_lookup
        self.supports_webhooks = supports_webhooks
        self.supports_refunds = supports_refunds
        self.supports_partial_refunds = supports_partial_refunds

        # Simulation behavior flags
        self.simulate_network_error = simulate_network_error
        self.simulate_timeout = simulate_timeout
        self.simulate_lookup_timeout = simulate_lookup_timeout
        self.simulate_failure_on_create = simulate_failure_on_create
        self.default_create_status = default_create_status

        # Internal state store for deterministic testing
        self.payments: dict[str, dict[str, Any]] = {}
        self.refunds: dict[str, dict[str, Any]] = {}

    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult:
        if self.simulate_network_error:
            raise PaymentProviderError("Upstream gateway network error")
        if self.simulate_timeout:
            raise TimeoutError("Upstream gateway request timed out")
        if self.simulate_failure_on_create:
            raise PaymentProviderError("Payment creation rejected by gateway")

        provider_payment_id = f"mock_pay_{request.idempotency_key}"
        status = self.default_create_status

        self.payments[provider_payment_id] = {
            "order_id": str(request.order_id),
            "amount": request.amount,
            "currency": request.currency,
            "status": status,
            "idempotency_key": request.idempotency_key,
        }

        return PaymentCreateResult(
            provider_payment_id=provider_payment_id,
            status=status,
            checkout_url=f"https://mock-pay.example.com/checkout/{provider_payment_id}",
            raw_data={"provider_payment_id": provider_payment_id, "status": status.value},
        )

    async def get_payment(self, provider_payment_id: str) -> PaymentDetailsResult:
        if not self.supports_payment_lookup:
            raise UnsupportedProviderCapabilityError(f"Provider {self.provider_name} does not support payment lookup")
        if self.simulate_network_error:
            raise PaymentProviderError("Upstream gateway network error during lookup")
        if self.simulate_timeout or self.simulate_lookup_timeout:
            raise TimeoutError("Upstream gateway lookup timed out")

        payment = self.payments.get(provider_payment_id)
        if not payment:
            raise PaymentProviderError(f"Payment {provider_payment_id} not found at provider")

        return PaymentDetailsResult(
            provider_payment_id=provider_payment_id,
            status=payment["status"],
            amount=payment["amount"],
            currency=payment["currency"],
            raw_data=payment,
        )

    async def verify_webhook(
        self,
        payload: bytes | str,
        headers: dict[str, str],
        secret: str,
    ) -> WebhookVerificationResult:
        if not self.supports_webhooks:
            raise UnsupportedProviderCapabilityError(f"Provider {self.provider_name} does not support webhooks")

        payload_bytes = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        signature = headers.get("x-signature") or headers.get("X-Signature")

        expected_sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
        is_valid = bool(signature and hmac.compare_digest(signature, expected_sig))

        if not is_valid:
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="unknown",
                raw_data={"error": "Invalid signature"},
            )

        try:
            data = json.loads(payload_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return WebhookVerificationResult(
                is_valid=False,
                provider_event_id="",
                event_type="unknown",
                raw_data={"error": "Malformed JSON"},
            )

        provider_event_id = data.get("event_id", "")
        event_type = data.get("event_type", "payment.succeeded")
        provider_payment_id = data.get("provider_payment_id")
        status_str = data.get("status", "SUCCEEDED")

        status_mapping = {
            "SUCCEEDED": PaymentIntentStatus.SUCCEEDED,
            "FAILED": PaymentIntentStatus.FAILED,
            "CANCELLED": PaymentIntentStatus.CANCELLED,
            "PROCESSING": PaymentIntentStatus.PROCESSING,
            "EXPIRED": PaymentIntentStatus.EXPIRED,
        }
        status = status_mapping.get(status_str, PaymentIntentStatus.SUCCEEDED)

        amount = Decimal(str(data["amount"])) if "amount" in data else None
        currency = data.get("currency")

        # Update internal store if payment exists
        if provider_payment_id and provider_payment_id in self.payments:
            self.payments[provider_payment_id]["status"] = status

        return WebhookVerificationResult(
            is_valid=True,
            provider_event_id=provider_event_id,
            event_type=event_type,
            provider_payment_id=provider_payment_id,
            status=status,
            amount=amount,
            currency=currency,
            raw_data=data,
        )

    async def refund(self, request: PaymentRefundRequest) -> PaymentRefundResult:
        if not self.supports_refunds:
            raise UnsupportedProviderCapabilityError(f"Provider {self.provider_name} does not support refunds")
        if self.simulate_network_error:
            raise PaymentProviderError("Upstream gateway network error during refund")

        payment = self.payments.get(request.provider_payment_id)
        if not payment:
            raise PaymentProviderError(f"Payment {request.provider_payment_id} not found at provider")

        if not self.supports_partial_refunds and request.amount < payment["amount"]:
            raise UnsupportedProviderCapabilityError(f"Provider {self.provider_name} does not support partial refunds")

        refund_id = f"mock_ref_{request.idempotency_key}"
        self.refunds[refund_id] = {
            "provider_payment_id": request.provider_payment_id,
            "amount": request.amount,
            "currency": request.currency,
            "reason": request.reason,
            "status": "COMPLETED",
        }

        return PaymentRefundResult(
            provider_refund_id=refund_id,
            status="COMPLETED",
            amount=request.amount,
            is_success=True,
            raw_data={"refund_id": refund_id, "amount": str(request.amount)},
        )
