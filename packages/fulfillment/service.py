import logging
import uuid
from datetime import UTC, datetime

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
from packages.providers.exceptions import ProviderError
from packages.providers.router import ProviderRouter

logger = logging.getLogger("fulfillment.service")


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

        # 4. Route through provider router (handling primary order item)
        primary_item = order.items[0] if order.items else None
        if not primary_item:
            attempt.status = FulfillmentStatus.FAILED
            attempt.error_classification = "EMPTY_ORDER_ITEMS"
            attempt.completed_at = datetime.now(UTC)
            await session.commit()
            return attempt

        try:
            provider, _mapping, response = await self.router.route_and_execute_order(
                session=session,
                tenant_id=order.tenant_id,
                product_id=primary_item.product_variant_id,  # Product/Variant ID
                quantity=primary_item.quantity,
                recipient=recipient,
                idempotency_key=idempotency_key,
                variant_id=primary_item.product_variant_id,
            )

            # 5. Success Flow
            attempt.provider_id = provider.id
            attempt.external_order_id = response.external_order_id
            attempt.cost_amount = response.cost
            attempt.response_payload = response.raw_data
            attempt.status = FulfillmentStatus.SUCCEEDED
            attempt.completed_at = datetime.now(UTC)

            # Mark order as fulfilled
            order.transition_to(OrderStatus.FULFILLED)
            await session.commit()

            logger.info(
                "Fulfillment SUCCEEDED for order #%s via provider '%s' (ext_id=%s)",
                order.order_number,
                provider.name,
                response.external_order_id,
            )

            await self.notifications.notify(
                NotificationPayload(
                    event_type=NotificationEventType.FULFILLMENT_SUCCEEDED,
                    tenant_id=order.tenant_id,
                    recipient=recipient,
                    order_id=order.id,
                    order_number=order.order_number,
                    message=f"🎉 Order #{order.order_number} fulfilled successfully!",
                    metadata={"external_order_id": response.external_order_id},
                )
            )
            return attempt

        except Exception as exc:
            # 6. Failure Flow & Financial Compensation Invariant
            error_type = type(exc).__name__
            is_retryable = getattr(exc, "is_retryable", False)

            attempt.status = FulfillmentStatus.FAILED
            attempt.error_classification = error_type
            attempt.response_payload = {"error": str(exc), "retryable": is_retryable}
            attempt.completed_at = datetime.now(UTC)

            logger.error(
                "Fulfillment attempt failed for order #%s: %s (type=%s, retryable=%s)",
                order.order_number,
                exc,
                error_type,
                is_retryable,
            )

            # Permanent failure: trigger automatic financial refund through LedgerService
            if not is_retryable or attempt_number >= 3:
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
                except Exception as refund_err:  # noqa: BLE001
                    logger.critical("Failed to process ledger refund for order #%s: %s", order.id, refund_err)

            await session.commit()

            await self.notifications.notify(
                NotificationPayload(
                    event_type=NotificationEventType.FULFILLMENT_FAILED,
                    tenant_id=order.tenant_id,
                    recipient=recipient,
                    order_id=order.id,
                    order_number=order.order_number,
                    message=f"⚠️ Order #{order.order_number} fulfillment could not be completed. Your wallet has been refunded.",
                    metadata={"error": str(exc)},
                )
            )

            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(f"Fulfillment failed: {exc}") from exc
