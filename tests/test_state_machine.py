import uuid
from decimal import Decimal

import pytest

from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStateMachine, OrderStatus
from packages.core.exceptions import InvalidStateTransitionError


def test_legal_order_lifecycle():
    order = Order(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        order_number="ORD-TEST-001",
        status=OrderStatus.PENDING,
        total_amount=Decimal("49.99"),
    )

    # 1. PENDING -> PAYMENT_PENDING
    order.transition_to(OrderStatus.PAYMENT_PENDING)
    assert order.status == OrderStatus.PAYMENT_PENDING

    # 2. PAYMENT_PENDING -> PAID
    order.transition_to(OrderStatus.PAID)
    assert order.status == OrderStatus.PAID

    # 3. PAID -> PROCESSING
    order.transition_to(OrderStatus.PROCESSING)
    assert order.status == OrderStatus.PROCESSING

    # 4. PROCESSING -> PARTIALLY_FULFILLED
    order.transition_to(OrderStatus.PARTIALLY_FULFILLED)
    assert order.status == OrderStatus.PARTIALLY_FULFILLED

    # 5. PARTIALLY_FULFILLED -> FULFILLED
    order.transition_to(OrderStatus.FULFILLED)
    assert order.status == OrderStatus.FULFILLED

    # 6. FULFILLED -> REFUNDED
    order.transition_to(OrderStatus.REFUNDED)
    assert order.status == OrderStatus.REFUNDED


def test_illegal_order_transitions():
    order = Order(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        order_number="ORD-TEST-002",
        status=OrderStatus.PENDING,
        total_amount=Decimal("10.00"),
    )

    # Cannot skip directly from PENDING to FULFILLED
    with pytest.raises(InvalidStateTransitionError):
        order.transition_to(OrderStatus.FULFILLED)

    # Cannot transition to REFUNDED before payment
    with pytest.raises(InvalidStateTransitionError):
        order.transition_to(OrderStatus.REFUNDED)


def test_terminal_states():
    # Cancelled orders cannot transition to any other state
    order = Order(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        order_number="ORD-TEST-003",
        status=OrderStatus.PENDING,
    )
    order.transition_to(OrderStatus.CANCELLED)
    assert order.status == OrderStatus.CANCELLED

    with pytest.raises(InvalidStateTransitionError):
        order.transition_to(OrderStatus.PAID)

    with pytest.raises(InvalidStateTransitionError):
        order.transition_to(OrderStatus.PROCESSING)


def test_can_transition_helper():
    assert OrderStateMachine.can_transition(OrderStatus.PENDING, OrderStatus.CANCELLED) is True
    assert OrderStateMachine.can_transition(OrderStatus.PENDING, OrderStatus.PROCESSING) is False
    assert OrderStateMachine.can_transition(OrderStatus.PAID, OrderStatus.PAID) is True
