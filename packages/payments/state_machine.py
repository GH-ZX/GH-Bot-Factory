import enum
from typing import TYPE_CHECKING

from packages.core.exceptions import InvalidStateTransitionError

if TYPE_CHECKING:
    from packages.payments.models import PaymentIntent


class PaymentIntentStatus(str, enum.Enum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


LEGAL_PAYMENT_TRANSITIONS: dict[PaymentIntentStatus, set[PaymentIntentStatus]] = {
    PaymentIntentStatus.CREATED: {
        PaymentIntentStatus.PENDING,
        PaymentIntentStatus.PROCESSING,
        PaymentIntentStatus.SUCCEEDED,
        PaymentIntentStatus.CANCELLED,
        PaymentIntentStatus.EXPIRED,
        PaymentIntentStatus.FAILED,
        PaymentIntentStatus.UNKNOWN,
    },
    PaymentIntentStatus.PENDING: {
        PaymentIntentStatus.PROCESSING,
        PaymentIntentStatus.SUCCEEDED,
        PaymentIntentStatus.FAILED,
        PaymentIntentStatus.EXPIRED,
        PaymentIntentStatus.CANCELLED,
        PaymentIntentStatus.UNKNOWN,
    },
    PaymentIntentStatus.PROCESSING: {
        PaymentIntentStatus.SUCCEEDED,
        PaymentIntentStatus.FAILED,
        PaymentIntentStatus.UNKNOWN,
        PaymentIntentStatus.CANCELLED,
        PaymentIntentStatus.EXPIRED,
    },
    PaymentIntentStatus.UNKNOWN: {
        PaymentIntentStatus.SUCCEEDED,
        PaymentIntentStatus.FAILED,
        PaymentIntentStatus.PROCESSING,
        PaymentIntentStatus.EXPIRED,
        PaymentIntentStatus.CANCELLED,
    },
    # Terminal states: immutable settlement / resolution
    PaymentIntentStatus.SUCCEEDED: set(),
    PaymentIntentStatus.FAILED: set(),
    PaymentIntentStatus.EXPIRED: set(),
    PaymentIntentStatus.CANCELLED: set(),
}


class PaymentStateMachine:
    """Enforces valid payment lifecycle state transitions."""

    @staticmethod
    def can_transition(current: PaymentIntentStatus, target: PaymentIntentStatus) -> bool:
        if current == target:
            return True
        allowed = LEGAL_PAYMENT_TRANSITIONS.get(current, set())
        return target in allowed

    @classmethod
    def transition(cls, intent: "PaymentIntent", target: PaymentIntentStatus) -> PaymentIntentStatus:
        current_status = intent.status
        if not cls.can_transition(current_status, target):
            raise InvalidStateTransitionError(
                f"Cannot transition payment intent {intent.id} from {current_status.value} to {target.value}. "
                f"Allowed transitions from {current_status.value} are: "
                f"{sorted([s.value for s in LEGAL_PAYMENT_TRANSITIONS.get(current_status, set())])}."
            )
        intent.status = target
        return target
