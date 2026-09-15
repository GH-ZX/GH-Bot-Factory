import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStatus
from packages.fulfillment.models import FulfillmentAttempt, FulfillmentStatus
from packages.notifications.service import (
    NotificationEventType,
    NotificationPayload,
    NotificationService,
)
from packages.payments.service import LedgerService
from packages.providers.exceptions import ProviderError, ProviderTimeoutError
from packages.providers.router import ProviderRouter

logger = logging.getLogger("fulfillment.service")


def _to_json_safe(val: Any) -> Any:
    """Recursively convert Decimals, UUIDs, and datetimes into JSON-serializable primitives."""
    if isinstance(val, dict):
        return {str(k): _to_json_safe(v) for k, v in val.items()}
    if isinstance(val, (list, tuple, set)):
        return [_to_json_safe(v) for v in val]
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, datetime):
        return val.isoformat()
    return val


class FulfillmentService:
    """Orchestrates resilient order dispatch, provider routing, idempotency, and financial reconciliation."""

    def __init__(
        self,
        router: ProviderRouter | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self.router = router or ProviderRouter()
        self.notifications = notification_service or NotificationService()

    async def execute_order_fulfillment(
        self,
        session: AsyncSession,
        order_id: uuid.UUID,
        recipient: str,
        attempt_number: int = 1,
    ) -> FulfillmentAttempt:
        """Executes a single, fully idempotent fulfillment attempt for an order."""
        # 1. Eager load Order with its items
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
        )
        res = await session.execute(stmt)
        order = res.scalar_one_or_none()

        if order is None:
            raise ValueError(f"Order {order_id} not found.")

        # 2. Check and enforce idempotency key
        idempotency_key = f"order:{order.id}:attempt:{attempt_number}"

        stmt_attempt = select(FulfillmentAttempt).where(
            FulfillmentAttempt.idempotency_key == idempotency_key
        )
        existing_attempt = (await session.execute(stmt_attempt)).scalar_one_or_none()

        # If already succeeded or in terminal state, return existing record
        if existing_attempt and existing_attempt.status == FulfillmentStatus.SUCCEEDED:
            logger.info("Idempotent replay: Fulfillment attempt %s already SUCCEEDED", idempotency_key)
            return existing_attempt

        # 3. Create or reuse active attempt
        if existing_attempt is None:
            attempt = FulfillmentAttempt(
                tenant_id=order.tenant_id,
                order_id=order.id,
                attempt_number=attempt_number,
                idempotency_key=idempotency_key,
                status=FulfillmentStatus.PROCESSING,
                cost_currency=order.currency,
                started_at=datetime.now(UTC),
            )
            session.add(attempt)
            await session.flush()
        else:
            attempt = existing_attempt
            attempt.status = FulfillmentStatus.PROCESSING
            await session.flush()

        # Update order status to PROCESSING if currently PAID
        if order.status == OrderStatus.PAID:
            order.transition_to(OrderStatus.PROCESSING)
            await session.flush()

        # Notify fulfillment started
        await self.notifications.notify(
            NotificationPayload(
                event_type=NotificationEventType.FULFILLMENT_STARTED,
                tenant_id=order.tenant_id,
                recipient=recipient,
                order_id=order.id,
                order_number=order.order_number,
                message=f"Processing fulfillment for order #{order.order_number}...",
            )
        )

        # 4. Route through provider router (handling multi-item orders)
        if not order.items:
            attempt.status = FulfillmentStatus.FAILED
            attempt.error_classification = "EMPTY_ORDER_ITEMS"
            attempt.completed_at = datetime.now(UTC)
            await session.commit()
            return attempt

        primary_item = order.items[0]
        attempt.order_item_id = primary_item.id

        try:
            responses = []
            last_provider = None
            total_provider_cost = Decimal("0.00")

            for item in order.items:
                item_key = (
                    idempotency_key
                    if len(order.items) == 1
                    else f"{idempotency_key}:item:{item.id}"
                )
                provider, _mapping, response = await self.router.route_and_execute_order(
                    session=session,
                    tenant_id=order.tenant_id,
                    product_id=item.product_variant_id,
                    quantity=item.quantity,
                    recipient=recipient,
                    idempotency_key=item_key,
                    variant_id=item.product_variant_id,
                )
                responses.append(response)
                last_provider = provider
                total_provider_cost += response.cost

            # 5. Success Flow
            attempt.provider_id = last_provider.id if last_provider else None
            attempt.external_order_id = responses[0].external_order_id
            attempt.cost_amount = total_provider_cost
            attempt.response_payload = _to_json_safe(
                responses[0].raw_data if len(responses) == 1 else [r.raw_data for r in responses]
            )
            attempt.status = FulfillmentStatus.SUCCEEDED
            attempt.completed_at = datetime.now(UTC)

            # Mark order as fulfilled
            order.transition_to(OrderStatus.FULFILLED)
            await session.commit()

            logger.info(
                "Fulfillment SUCCEEDED for order #%s via provider '%s' (ext_id=%s)",
                order.order_number,
                last_provider.name if last_provider else "N/A",
                attempt.external_order_id,
            )

            await self.notifications.notify(
                NotificationPayload(
                    event_type=NotificationEventType.FULFILLMENT_SUCCEEDED,
                    tenant_id=order.tenant_id,
                    recipient=recipient,
                    order_id=order.id,
                    order_number=order.order_number,
                    message=f"🎉 Order #{order.order_number} fulfilled successfully!",
                    metadata={"external_order_id": attempt.external_order_id},
                )
            )
            return attempt

        except Exception as exc:
            # 6. Failure Flow & Financial Compensation Invariant
            error_type = type(exc).__name__
            is_retryable = getattr(exc, "is_retryable", False)
            if hasattr(exc, "__cause__") and getattr(exc.__cause__, "is_retryable", False):
                is_retryable = True
            is_timeout = isinstance(exc, (ProviderTimeoutError, TimeoutError)) or (
                hasattr(exc, "__cause__") and isinstance(exc.__cause__, (ProviderTimeoutError, TimeoutError))
            )

            attempt.error_classification = error_type
            attempt.response_payload = _to_json_safe({"error": str(exc), "retryable": is_retryable})
            attempt.completed_at = datetime.now(UTC)

            logger.error(
                "Fulfillment attempt %d failed for order #%s: %s (type=%s, retryable=%s)",
                attempt_number,
                order.order_number,
                exc,
                error_type,
                is_retryable,
            )

            if is_retryable and attempt_number < 3:
                # Transient error: mark RETRYING (or UNKNOWN for network timeout)
                attempt.status = FulfillmentStatus.UNKNOWN if is_timeout else FulfillmentStatus.RETRYING
                await session.commit()

                # Notify progress, NEVER premature refund
                await self.notifications.notify(
                    NotificationPayload(
                        event_type=NotificationEventType.FULFILLMENT_STARTED,
                        tenant_id=order.tenant_id,
                        recipient=recipient,
                        order_id=order.id,
                        order_number=order.order_number,
                        message=f"Order #{order.order_number} fulfillment is temporarily delayed. Our system is retrying automatically.",
                        metadata={"attempt": attempt_number},
                    )
                )
            else:
                # Permanent failure: trigger automatic financial refund through LedgerService
                attempt.status = FulfillmentStatus.FAILED
                refund_successful = False

                logger.warning(
                    "Executing automated ledger refund for failed order #%s",
                    order.order_number,
                )
                try:
                    wallet = await LedgerService.get_or_create_wallet(
                        session=session,
                        tenant_id=order.tenant_id,
                        user_id=order.user_id,
                        currency=order.currency,
                    )
                    await LedgerService.refund(
                        session=session,
                        wallet=wallet,
                        amount=order.total_amount,
                        reference_id=str(order.id),
                        reference_type="FULFILLMENT_FAILURE_REFUND",
                        description=f"Automated refund for unfulfilled order #{order.order_number}",
                    )
                    order.transition_to(OrderStatus.FAILED)
                    refund_successful = True
                except Exception as refund_err:  # noqa: BLE001
                    logger.critical("Failed to process ledger refund for order #%s: %s", order.id, refund_err)

                await session.commit()

                # ONLY send refund notification if refund was ACTUALLY executed!
                # Do NOT leak raw str(exc) to user!
                if refund_successful:
                    await self.notifications.notify(
                        NotificationPayload(
                            event_type=NotificationEventType.ORDER_REFUNDED,
                            tenant_id=order.tenant_id,
                            recipient=recipient,
                            order_id=order.id,
                            order_number=order.order_number,
                            message=f"⚠️ Order #{order.order_number} could not be fulfilled. Your balance has been refunded.",
                            metadata={"order_number": order.order_number},
                        )
                    )
                else:
                    await self.notifications.notify(
                        NotificationPayload(
                            event_type=NotificationEventType.FULFILLMENT_FAILED,
                            tenant_id=order.tenant_id,
                            recipient=recipient,
                            order_id=order.id,
                            order_number=order.order_number,
                            message=f"⚠️ Order #{order.order_number} fulfillment could not be completed. Please contact support.",
                            metadata={"order_number": order.order_number},
                        )
                    )

            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(f"Fulfillment failed: {exc}") from exc
