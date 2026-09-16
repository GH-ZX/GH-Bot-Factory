import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.commerce.economics import PricingService
from packages.commerce.economics_models import OrderItemEconomics
from packages.commerce.models import Order, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.factory.business_profiles import (
    allowed_provider_categories,
    business_profile_from_config,
)
from packages.fulfillment.models import FulfillmentAttempt, FulfillmentStatus
from packages.notifications.service import (
    NotificationEventType,
    NotificationPayload,
    NotificationService,
)
from packages.payments.service import CANONICAL_REFUND_TYPE, LedgerService
from packages.providers.contracts import ProviderOrderState
from packages.providers.exceptions import (
    ProviderError,
    ProviderOrderFailedError,
    ProviderTimeoutError,
)
from packages.providers.router import ProviderRouter
from packages.telegram.models import Bot

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


def _provider_response_payload(response: Any) -> dict[str, Any]:
    return _to_json_safe(
        {
            "external_order_id": response.external_order_id,
            "canonical_state": response.canonical_state.value,
            "raw_data": response.raw_data,
            "delivery": [
                {
                    "kind": artifact.kind.value,
                    "value": artifact.value,
                    "fields": artifact.fields,
                }
                for artifact in response.delivery
            ],
        }
    )


class FulfillmentService:
    """Orchestrates resilient order dispatch, provider routing, idempotency, and financial reconciliation."""

    def __init__(
        self,
        router: ProviderRouter | None = None,
        notification_service: NotificationService | None = None,
        pricing_service: PricingService | None = None,
    ) -> None:
        self.router = router or ProviderRouter()
        self.notifications = notification_service or NotificationService()
        self.pricing = pricing_service or PricingService()

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

        bot_profile = business_profile_from_config(None)
        bot_id = await session.scalar(
            select(OrderItemEconomics.bot_id)
            .where(OrderItemEconomics.order_id == order.id, OrderItemEconomics.bot_id.is_not(None))
            .limit(1)
        )
        if bot_id is not None:
            bot = await session.get(Bot, bot_id)
            if bot is not None and bot.tenant_id == order.tenant_id and bot.deleted_at is None:
                bot_profile = business_profile_from_config(bot.config)

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
            cost_currencies: set[str] = set()
            total_provider_cost = Decimal("0.00")

            # Provider mappings are product-scoped with an optional variant refinement.
            # Resolve canonical product ids once instead of incorrectly using variant ids as
            # product ids (an old SQLite-friendly bug that violates the PostgreSQL FK contract).
            variant_rows = (
                await session.execute(
                    select(ProductVariant.id, ProductVariant.product_id).where(
                        ProductVariant.id.in_([item.product_variant_id for item in order.items])
                    )
                )
            ).all()
            product_id_by_variant = {variant_id: product_id for variant_id, product_id in variant_rows}
            if len(product_id_by_variant) != len({item.product_variant_id for item in order.items}):
                raise ProviderError("One or more order item variants no longer exist.")

            for item in order.items:
                item_key = (
                    idempotency_key
                    if len(order.items) == 1
                    else f"{idempotency_key}:item:{item.id}"
                )
                provider, mapping, response = await self.router.route_and_execute_order(
                    session=session,
                    tenant_id=order.tenant_id,
                    product_id=product_id_by_variant[item.product_variant_id],
                    quantity=item.quantity,
                    recipient=recipient,
                    idempotency_key=item_key,
                    variant_id=item.product_variant_id,
                    allowed_provider_ids=set(bot_profile.provider_ids) or None,
                    allowed_categories=set(allowed_provider_categories(bot_profile.business_type)),
                    strategy_override=bot_profile.routing_strategy,
                    preferred_provider_id=bot_profile.preferred_provider_id,
                    failover_enabled=True,
                )
                responses.append(response)
                last_provider = provider
                cost_currency = mapping.cost_currency.upper()
                cost_currencies.add(cost_currency)
                # Never add costs expressed in different currencies into a fake total.
                if len(cost_currencies) == 1:
                    total_provider_cost += response.cost

                if response.canonical_state == ProviderOrderState.COMPLETED:
                    await self.pricing.attribute_actual_cost(
                        session,
                        order_item_id=item.id,
                        provider_id=provider.id,
                        actual_cost=response.cost,
                        actual_currency=cost_currency,
                    )

                if response.canonical_state in {
                    ProviderOrderState.FAILED,
                    ProviderOrderState.CANCELLED,
                    ProviderOrderState.EXPIRED,
                    ProviderOrderState.REFUNDED,
                }:
                    attempt.provider_id = provider.id
                    attempt.external_order_id = response.external_order_id
                    attempt.response_payload = _provider_response_payload(response)
                    raise ProviderOrderFailedError(
                        f"Provider returned terminal state {response.canonical_state.value}."
                    )

            attempt.provider_id = last_provider.id if last_provider else None
            attempt.external_order_id = responses[0].external_order_id
            if len(cost_currencies) == 1:
                attempt.cost_amount = total_provider_cost
                attempt.cost_currency = next(iter(cost_currencies))
            else:
                # Per-item economics remains authoritative for mixed-currency carts.
                attempt.cost_amount = Decimal("0.00")
                attempt.cost_currency = "MIX"
            attempt.response_payload = (
                _provider_response_payload(responses[0])
                if len(responses) == 1
                else [_provider_response_payload(response) for response in responses]
            )

            nonterminal = [
                response
                for response in responses
                if response.canonical_state != ProviderOrderState.COMPLETED
            ]
            if nonterminal:
                # A dispatched upstream order is authoritative. Do not replay it at another provider,
                # mark the commerce order fulfilled, or refund while the provider state is pending/unknown.
                if len(responses) > 1:
                    attempt.status = FulfillmentStatus.UNKNOWN
                    attempt.error_classification = "MULTI_ITEM_NONTERMINAL_PROVIDER_STATE"
                elif nonterminal[0].canonical_state == ProviderOrderState.UNKNOWN:
                    attempt.status = FulfillmentStatus.UNKNOWN
                    attempt.error_classification = "UPSTREAM_STATE_UNKNOWN"
                else:
                    attempt.status = FulfillmentStatus.PROCESSING
                    attempt.error_classification = None
                attempt.completed_at = None
                await session.commit()
                await self.notifications.notify(
                    NotificationPayload(
                        event_type=NotificationEventType.FULFILLMENT_STARTED,
                        tenant_id=order.tenant_id,
                        recipient=recipient,
                        order_id=order.id,
                        order_number=order.order_number,
                        message=f"Order #{order.order_number} was accepted and is still processing upstream.",
                        metadata={
                            "external_order_id": attempt.external_order_id,
                            "provider_state": nonterminal[0].canonical_state.value,
                        },
                    )
                )
                return attempt

            # 5. Success Flow: only canonical COMPLETED is fulfillment success.
            attempt.status = FulfillmentStatus.SUCCEEDED
            attempt.completed_at = datetime.now(UTC)

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
                        reference_type=CANONICAL_REFUND_TYPE,
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
