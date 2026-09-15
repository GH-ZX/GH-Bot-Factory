import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from packages.payments.state_machine import PaymentIntentStatus


@dataclass
class PaymentCreateRequest:
    order_id: uuid.UUID | None
    amount: Decimal
    currency: str
    idempotency_key: str
    metadata: dict[str, Any] = field(default_factory=dict)
    return_url: str | None = None


@dataclass
class PaymentCreateResult:
    provider_payment_id: str
    status: PaymentIntentStatus
    checkout_url: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentDetailsResult:
    provider_payment_id: str
    status: PaymentIntentStatus
    amount: Decimal
    currency: str
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class WebhookVerificationResult:
    is_valid: bool
    provider_event_id: str
    event_type: str
    provider_payment_id: str | None = None
    status: PaymentIntentStatus | None = None
    amount: Decimal | None = None
    currency: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentRefundRequest:
    provider_payment_id: str
    amount: Decimal
    currency: str
    idempotency_key: str
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentRefundResult:
    provider_refund_id: str
    status: str
    amount: Decimal
    is_success: bool = True
    raw_data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PaymentProvider(Protocol):
    """Protocol defining the interface that every external payment provider adapter must implement."""

    provider_name: str
    supports_idempotency_keys: bool
    supports_payment_lookup: bool
    supports_webhooks: bool
    supports_refunds: bool
    supports_partial_refunds: bool
    supports_safe_refund_retries: bool

    async def create_payment(self, request: PaymentCreateRequest) -> PaymentCreateResult:
        """Initiates a payment attempt with the upstream payment gateway."""
        ...

    async def get_payment(self, provider_payment_id: str) -> PaymentDetailsResult:
        """Fetches current payment status from the upstream gateway for reconciliation."""
        ...

    async def verify_webhook(
        self,
        payload: bytes | str,
        headers: dict[str, str],
        secret: str,
    ) -> WebhookVerificationResult:
        """Verifies signature/authenticity and extracts event details from raw webhook."""
        ...

    async def refund(self, request: PaymentRefundRequest) -> PaymentRefundResult:
        """Executes a gateway-level refund."""
        ...
