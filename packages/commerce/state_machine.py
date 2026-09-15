import enum
from typing import TYPE_CHECKING

from packages.core.exceptions import InvalidStateTransitionError

if TYPE_CHECKING:
    from packages.commerce.models import Order


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAID = "PAID"
    PROCESSING = "PROCESSING"
    PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


LEGAL_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {
        OrderStatus.PAYMENT_PENDING,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    },
    OrderStatus.PAYMENT_PENDING: {
        OrderStatus.PAID,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    },
    OrderStatus.PAID: {
        OrderStatus.PROCESSING,
        OrderStatus.REFUNDED,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    },
    OrderStatus.PROCESSING: {
        OrderStatus.PARTIALLY_FULFILLED,
        OrderStatus.FULFILLED,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
        OrderStatus.REFUNDED,
    },
    OrderStatus.PARTIALLY_FULFILLED: {
        OrderStatus.FULFILLED,
        OrderStatus.FAILED,
    },
    OrderStatus.FULFILLED: {
        OrderStatus.REFUNDED,
    },
    OrderStatus.CANCELLED: set(),
    OrderStatus.FAILED: set(),
    OrderStatus.REFUNDED: set(),
}


class OrderStateMachine:
    """Enforces valid order lifecycle state transitions."""

    @staticmethod
    def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
        if current == target:
            return True
        allowed = LEGAL_TRANSITIONS.get(current, set())
        return target in allowed

    @classmethod
    def transition(cls, order: "Order", target: OrderStatus) -> OrderStatus:
        current_status = order.status
        if not cls.can_transition(current_status, target):
            raise InvalidStateTransitionError(
                f"Cannot transition order {order.order_number} from {current_status.value} to {target.value}. "
                f"Allowed transitions from {current_status.value} are: "
                f"{[s.value for s in LEGAL_TRANSITIONS.get(current_status, set())]}."
            )
        order.status = target
        return target
