from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


class ProviderOrderState(str, enum.Enum):
    """Vendor-neutral state for ordinary upstream orders."""

    CREATED = "CREATED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    WAITING_DELIVERY = "WAITING_DELIVERY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REFUNDED = "REFUNDED"
    UNKNOWN = "UNKNOWN"


def normalize_provider_order_state(value: str | ProviderOrderState | None) -> ProviderOrderState:
    if isinstance(value, ProviderOrderState):
        return value
    normalized = (value or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "NEW": ProviderOrderState.CREATED,
        "CREATED": ProviderOrderState.CREATED,
        "PENDING": ProviderOrderState.PENDING,
        "WAITING": ProviderOrderState.PENDING,
        "IN_PROGRESS": ProviderOrderState.PROCESSING,
        "PROCESSING": ProviderOrderState.PROCESSING,
        "WAITING_DELIVERY": ProviderOrderState.WAITING_DELIVERY,
        "PARTIAL": ProviderOrderState.WAITING_DELIVERY,
        "DONE": ProviderOrderState.COMPLETED,
        "SUCCESS": ProviderOrderState.COMPLETED,
        "SUCCESSFUL": ProviderOrderState.COMPLETED,
        "COMPLETED": ProviderOrderState.COMPLETED,
        "FAILED": ProviderOrderState.FAILED,
        "ERROR": ProviderOrderState.FAILED,
        "REJECTED": ProviderOrderState.FAILED,
        "CANCELED": ProviderOrderState.CANCELLED,
        "CANCELLED": ProviderOrderState.CANCELLED,
        "EXPIRED": ProviderOrderState.EXPIRED,
        "REFUNDED": ProviderOrderState.REFUNDED,
    }
    return aliases.get(normalized, ProviderOrderState.UNKNOWN)


class ProviderDeliveryKind(str, enum.Enum):
    """Canonical delivery shape without imposing vendor-specific field names."""

    TEXT = "TEXT"
    CODE = "CODE"
    ACCOUNT = "ACCOUNT"
    PHONE_NUMBER = "PHONE_NUMBER"
    SMS = "SMS"
    URL = "URL"
    FILE = "FILE"
    STRUCTURED = "STRUCTURED"


@dataclass(frozen=True, slots=True)
class ProviderDeliveryArtifact:
    kind: ProviderDeliveryKind
    value: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class NumberActivationState(str, enum.Enum):
    """Canonical lifecycle for virtual-number/SMS activations."""

    RESERVED = "RESERVED"
    WAITING_SMS = "WAITING_SMS"
    SMS_RECEIVED = "SMS_RECEIVED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class NumberServiceDTO:
    code: str
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NumberCountryDTO:
    code: str
    name: str
    dial_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NumberOfferDTO:
    service: str
    country: str
    cost: Decimal
    currency: str = "USD"
    available_quantity: int | None = None
    operator: str | None = None
    provider_offer_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NumberReservationRequest:
    service: str
    country: str
    operator: str | None = None
    max_price: Decimal | None = None
    idempotency_key: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            self.idempotency_key = str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class SmsMessageDTO:
    code: str | None = None
    text: str | None = None
    sender: str | None = None
    received_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NumberActivationSnapshot:
    external_order_id: str
    state: NumberActivationState
    phone_number: str | None = None
    cost: Decimal | None = None
    currency: str | None = None
    expires_at: datetime | None = None
    messages: tuple[SmsMessageDTO, ...] = ()
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AccountOfferDTO:
    external_id: str
    title: str
    cost: Decimal
    currency: str = "USD"
    stock: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GiftOfferDTO:
    external_id: str
    title: str
    cost: Decimal
    currency: str = "USD"
    stock: int | None = None
    denomination: Decimal | None = None
    denomination_currency: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DigitalServiceOfferDTO:
    external_id: str
    title: str
    cost: Decimal
    currency: str = "USD"
    min_quantity: int = 1
    max_quantity: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AccountOrderRequest:
    external_offer_id: str
    quantity: int = 1
    recipient: str = ""
    idempotency_key: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GiftOrderRequest:
    external_offer_id: str
    quantity: int = 1
    recipient: str = ""
    idempotency_key: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DigitalServiceOrderRequest:
    external_offer_id: str
    quantity: int
    recipient: str
    idempotency_key: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class NumberProviderOperations(Protocol):
    async def list_number_services(self) -> list[NumberServiceDTO]: ...

    async def list_number_countries(self, service: str | None = None) -> list[NumberCountryDTO]: ...

    async def list_number_offers(
        self,
        *,
        service: str,
        country: str,
    ) -> list[NumberOfferDTO]: ...

    async def reserve_number(self, request: NumberReservationRequest) -> NumberActivationSnapshot: ...

    async def get_number_activation(self, external_order_id: str) -> NumberActivationSnapshot: ...

    async def cancel_number_activation(self, external_order_id: str) -> NumberActivationSnapshot: ...

    async def finish_number_activation(self, external_order_id: str) -> NumberActivationSnapshot: ...
