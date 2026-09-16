import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.commerce.economics import PricingService
from packages.commerce.economics_models import OrderItemEconomics
from packages.commerce.models import Order, OrderItem, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.fulfillment.models import (
    FulfillmentAttempt,
    FulfillmentJobRecord,
    FulfillmentJobStatus,
)
from packages.fulfillment.service import FulfillmentService
from packages.notifications.service import (
    NotificationEventType,
    NotificationPayload,
    NotificationService,
)
from packages.payments.service import LedgerService

logger = logging.getLogger("commerce.checkout")


@dataclass(frozen=True)
class CheckoutLine:
    variant_id: uuid.UUID
    quantity: int


class CheckoutService:
    """Coordinates idempotent cart checkout, ledger debit, and fulfillment."""

    def __init__(
        self,
        fulfillment_service: FulfillmentService | None = None,
        notification_service: NotificationService | None = None,
        pricing_service: PricingService | None = None,
    ) -> None:
        self.notifications = notification_service or NotificationService()
        self.fulfillment = fulfillment_service or FulfillmentService(
            notification_service=self.notifications
        )
        self.pricing = pricing_service or PricingService()

    async def checkout(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        variant_id: uuid.UUID,
        quantity: int,
        recipient: str,
        execute_sync: bool = True,
        idempotency_key: str | None = None,
        enqueue_durable: bool = False,
        bot_id: uuid.UUID | None = None,
    ) -> tuple[Order, FulfillmentAttempt | None]:
        """Backward-compatible single-line checkout wrapper."""
        return await self.checkout_cart(
            session=session,
            tenant_id=tenant_id,
            user_id=user_id,
            lines=[CheckoutLine(variant_id=variant_id, quantity=quantity)],
            recipient=recipient,
            execute_sync=execute_sync,
            idempotency_key=idempotency_key,
            enqueue_durable=enqueue_durable,
            bot_id=bot_id,
        )

    async def checkout_cart(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        lines: list[CheckoutLine],
        recipient: str,
        execute_sync: bool = True,
        idempotency_key: str | None = None,
        enqueue_durable: bool = False,
        bot_id: uuid.UUID | None = None,
    ) -> tuple[Order, FulfillmentAttempt | None]:
        """Checkout one or more variants using server-authoritative prices.

        When ``idempotency_key`` is supplied, the database-enforced unique constraint on
        ``(tenant_id, user_id, checkout_idempotency_key)`` prevents duplicate orders and
        duplicate wallet debits from retries or double-submits.
        """
        if execute_sync and enqueue_durable:
            raise ValueError("Checkout cannot use synchronous and durable fulfillment together.")

        normalized_recipient = recipient.strip()
        if not normalized_recipient:
            raise ValueError("Checkout recipient cannot be empty.")
        if not lines:
            raise ValueError("Checkout cart cannot be empty.")
        if len(lines) > 50:
            raise ValueError("Checkout cart cannot contain more than 50 line items.")

        quantity_by_variant: dict[uuid.UUID, int] = {}
        for line in lines:
            if line.quantity <= 0:
                raise ValueError("Order quantity must be greater than zero.")
            quantity_by_variant[line.variant_id] = (
                quantity_by_variant.get(line.variant_id, 0) + line.quantity
            )

        if idempotency_key is not None:
            idempotency_key = idempotency_key.strip()
            if not idempotency_key:
                raise ValueError("Idempotency key cannot be empty.")
            if len(idempotency_key) > 100:
                raise ValueError("Idempotency key cannot exceed 100 characters.")

        request_hash = self._request_hash(quantity_by_variant, normalized_recipient)
        if idempotency_key:
            existing = await self._find_idempotent_order(
                session=session,
                tenant_id=tenant_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                self._assert_idempotent_request_matches(existing, request_hash)
                return await self._resume_or_return(
                    session=session,
                    order=existing,
                    recipient=normalized_recipient,
                    execute_sync=execute_sync,
                )

        variants = await self._load_authoritative_variants(
            session=session,
            tenant_id=tenant_id,
            variant_ids=list(quantity_by_variant),
        )
        if len(variants) != len(quantity_by_variant):
            found_ids = {variant.id for variant in variants}
            missing = [
                str(variant_id)
                for variant_id in quantity_by_variant
                if variant_id not in found_ids
            ]
            raise ValueError(f"One or more product variants are unavailable: {', '.join(missing)}.")

        # Phase 12 pricing is server-authoritative and user-tier aware. Catalog price remains
        # the fallback when no explicit pricing rule/fresh same-currency supplier offer exists.
        quoted: dict[uuid.UUID, tuple[object, object]] = {}
        for variant in variants:
            quote, decision = await self.pricing.quote(
                session, tenant_id=tenant_id, user_id=user_id, variant=variant, bot_id=bot_id
            )
            quoted[variant.id] = (quote, decision)

        currencies = {decision.currency for _quote, decision in quoted.values()}
        if len(currencies) != 1:
            raise ValueError("A single checkout cannot contain variants with different currencies.")
        currency = next(iter(currencies))

        total_amount = sum(
            (
                decision.sell_price * Decimal(quantity_by_variant[variant.id])
                for variant in variants
                for _quote, decision in [quoted[variant.id]]
            ),
            start=Decimal("0.00"),
        )
        order_number = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        order = Order(
            tenant_id=tenant_id,
            user_id=user_id,
            order_number=order_number,
            status=OrderStatus.PENDING,
            total_amount=total_amount,
            currency=currency,
            checkout_idempotency_key=idempotency_key,
            checkout_request_hash=request_hash if idempotency_key else None,
        )

        session.add(order)
        try:
            await session.flush()
        except IntegrityError:
            # The order insert happens before any financial mutation. Roll back the request
            # transaction so the session can safely resolve the winning idempotent order.
            await session.rollback()
            if not idempotency_key:
                raise
            existing = await self._find_idempotent_order(
                session=session,
                tenant_id=tenant_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            if existing is None:
                raise
            self._assert_idempotent_request_matches(existing, request_hash)
            logger.info("Concurrent checkout retry resolved by database idempotency constraint.")
            return await self._resume_or_return(
                session=session,
                order=existing,
                recipient=normalized_recipient,
                execute_sync=execute_sync,
            )

        for variant in variants:
            quantity = quantity_by_variant[variant.id]
            quote, decision = quoted[variant.id]
            item = OrderItem(
                order_id=order.id,
                product_variant_id=variant.id,
                quantity=quantity,
                unit_price=decision.sell_price,
                total_price=decision.sell_price * Decimal(quantity),
            )
            session.add(item)
            await session.flush()
            session.add(
                OrderItemEconomics(
                    tenant_id=tenant_id,
                    order_id=order.id,
                    order_item_id=item.id,
                    bot_id=bot_id,
                    price_quote_id=quote.id,
                    pricing_tier_id=decision.pricing_tier_id,
                    pricing_rule_id=decision.pricing_rule_id,
                    sale_amount=item.total_price,
                    sale_currency=decision.currency,
                    estimated_supplier_cost=(
                        decision.supplier_cost * Decimal(quantity)
                        if decision.supplier_cost is not None
                        else None
                    ),
                    estimated_cost_currency=decision.supplier_currency,
                )
            )

        order.transition_to(OrderStatus.PAYMENT_PENDING)
        wallet = await LedgerService.get_or_create_wallet(
            session=session,
            tenant_id=tenant_id,
            user_id=user_id,
            currency=currency,
        )
        await LedgerService.debit(
            session=session,
            wallet=wallet,
            amount=total_amount,
            reference_id=order_number,
            reference_type="ORDER_CHECKOUT",
            description=f"Purchase of {sum(quantity_by_variant.values())} item(s)",
        )
        order.transition_to(OrderStatus.PAID)

        if enqueue_durable:
            session.add(
                FulfillmentJobRecord(
                    tenant_id=tenant_id,
                    order_id=order.id,
                    recipient=normalized_recipient,
                    attempt_number=1,
                    status=FulfillmentJobStatus.QUEUED,
                    payload={"source": "checkout"},
                )
            )
            # Fail closed before the financial transaction commits if durable job persistence fails.
            await session.flush()

        await session.commit()

        logger.info(
            "Order #%s created and debited (%s %s) for user %s",
            order_number,
            total_amount,
            currency,
            user_id,
        )

        await self.notifications.notify(
            NotificationPayload(
                event_type=NotificationEventType.PAYMENT_CONFIRMED,
                tenant_id=tenant_id,
                recipient=normalized_recipient,
                order_id=order.id,
                order_number=order.order_number,
                message=(
                    f"💳 Payment confirmed for order #{order.order_number}. "
                    f"Amount: {total_amount} {currency}."
                ),
            )
        )

        attempt: FulfillmentAttempt | None = None
        if execute_sync:
            attempt = await self.fulfillment.execute_order_fulfillment(
                session=session,
                order_id=order.id,
                recipient=normalized_recipient,
            )

        return order, attempt

    @staticmethod
    def _request_hash(quantity_by_variant: dict[uuid.UUID, int], recipient: str) -> str:
        payload = {
            "items": sorted(
                (str(variant_id), quantity) for variant_id, quantity in quantity_by_variant.items()
            ),
            "recipient": recipient,
        }
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    async def _load_authoritative_variants(
        session: AsyncSession,
        tenant_id: uuid.UUID,
        variant_ids: list[uuid.UUID],
    ) -> list[ProductVariant]:
        stmt = (
            select(ProductVariant)
            .join(Product, ProductVariant.product_id == Product.id)
            .where(
                ProductVariant.id.in_(variant_ids),
                Product.tenant_id == tenant_id,
                ProductVariant.is_active.is_(True),
                Product.is_active.is_(True),
                Product.deleted_at.is_(None),
                ProductVariant.deleted_at.is_(None),
            )
            .options(selectinload(ProductVariant.product))
        )
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def _find_idempotent_order(
        session: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        idempotency_key: str,
    ) -> Order | None:
        stmt = (
            select(Order)
            .where(
                Order.tenant_id == tenant_id,
                Order.user_id == user_id,
                Order.checkout_idempotency_key == idempotency_key,
            )
            .options(selectinload(Order.items))
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def _assert_idempotent_request_matches(order: Order, request_hash: str) -> None:
        if order.checkout_request_hash != request_hash:
            raise ValueError("Idempotency key was already used for a different checkout request.")

    async def _resume_or_return(
        self,
        session: AsyncSession,
        order: Order,
        recipient: str,
        execute_sync: bool,
    ) -> tuple[Order, FulfillmentAttempt | None]:
        attempt: FulfillmentAttempt | None = None
        if execute_sync and order.status in {OrderStatus.PAID, OrderStatus.PROCESSING}:
            attempt = await self.fulfillment.execute_order_fulfillment(
                session=session,
                order_id=order.id,
                recipient=recipient,
            )
        return order, attempt
