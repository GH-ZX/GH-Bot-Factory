import enum
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class NotificationEventType(str, enum.Enum):
    ORDER_CREATED = "ORDER_CREATED"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    FULFILLMENT_STARTED = "FULFILLMENT_STARTED"
    FULFILLMENT_SUCCEEDED = "FULFILLMENT_SUCCEEDED"
    FULFILLMENT_FAILED = "FULFILLMENT_FAILED"
    ORDER_REFUNDED = "ORDER_REFUNDED"


@dataclass
class NotificationPayload:
    event_type: NotificationEventType
    tenant_id: uuid.UUID
    recipient: str  # Telegram username, chat_id, or handle
    order_id: uuid.UUID
    order_number: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class NotificationTransport(Protocol):
    """Protocol for sending asynchronous customer and admin notifications."""

    async def send(self, payload: NotificationPayload) -> bool: ...


class MockNotificationTransport(NotificationTransport):
    """In-memory notification transport for test inspection."""

    def __init__(self) -> None:
        self.sent_notifications: list[NotificationPayload] = []

    async def send(self, payload: NotificationPayload) -> bool:
        self.sent_notifications.append(payload)
        return True

    def get_events_for_order(self, order_id: uuid.UUID) -> list[NotificationPayload]:
        return [n for n in self.sent_notifications if n.order_id == order_id]


class NotificationService:
    """Dispatches application notifications to configured transports."""

    def __init__(self, transports: Sequence[NotificationTransport] | None = None) -> None:
        self.transports: list[NotificationTransport] = list(transports) if transports else [MockNotificationTransport()]

    async def notify(self, payload: NotificationPayload) -> None:
        for transport in self.transports:
            try:
                await transport.send(payload)
            except Exception:  # noqa: BLE001, S110
                pass  # Notifications should never break core checkout/fulfillment flow
