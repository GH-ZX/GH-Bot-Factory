import logging
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.commerce.models import Order, OrderItem, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.fulfillment.models import FulfillmentAttempt
from packages.fulfillment.service import FulfillmentService
from packages.notifications.service import (
    NotificationEventType,
    NotificationPayload,
    NotificationService,
)
from packages.payments.service import LedgerService

logger = logging.getLogger("commerce.checkout")


class CheckoutService:
    """Coordinates cart checkout, price validation, ledger balance reservation, and fulfillment."""

    def __init__(
        self,
        fulfillment_service: FulfillmentService | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self.notifications = notification_service or NotificationService()
        self.fulfillment = fulfillment_service or FulfillmentService(
            notification_service=self.notifications
        )

    async def checkout(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        variant_id: uuid.UUID,
        quantity: int,
        recipient: str,
        execute_sync: bool = True,
    ) -> tuple[Order, FulfillmentAttempt | None]:
        """Creates order, strictly validates unit price from DB, debits wallet, and executes fulfillment."""
        if quantity <= 0:
            raise ValueError("Order quantity must be greater than zero.")

        # 1. Fetch variant and parent product scoped to tenant
        stmt = (
            select(ProductVariant)
            .join(Product, ProductVariant.product_id == Product.id)
            .where(
                ProductVariant.id == variant_id,
                Product.tenant_id == tenant_id,
                ProductVariant.is_active.is_(True),
                Product.is_active.is_(True),
            )
            .options(selectinload(ProductVariant.product))
        )
        result = await session.execute(stmt)
        variant = result.scalar_one_or_none()

        if variant is None:
            raise ValueError(f"Product variant {variant_id} is not available in tenant {tenant_id}.")

        # Calculate authoritative total
        unit_price = variant.price
        total_amount = unit_price * Decimal(quantity)
        currency = variant.currency

        # 2. Financial Debit via LedgerService (raises InsufficientFundsError if balance < total)
        wallet = await LedgerService.get_or_create_wallet(
            session=session,
            tenant_id=tenant_id,
            user_id=user_id,
            currency=currency,
        )

        order_number = f"ORD-{uuid.uuid4().hex[:8].upper()}"

        # Debit wallet atomically
        await LedgerService.debit(
            session=session,
            wallet=wallet,
            amount=total_amount,
            reference_id=order_number,
            reference_type="ORDER_CHECKOUT",
            description=f"Purchase of {quantity}x {variant.title}",
        )

        # 3. Persist Order in PAID status
        order = Order(
            tenant_id=tenant_id,
            user_id=user_id,
            order_number=order_number,
            status=OrderStatus.PAID,
            total_amount=total_amount,
            currency=currency,
        )
        session.add(order)
        await session.flush()

        order_item = OrderItem(
            order_id=order.id,
            product_variant_id=variant.id,
            quantity=quantity,
            unit_price=unit_price,
            total_price=total_amount,
        )
        session.add(order_item)
        await session.commit()

        logger.info(
            "Order #%s created and debited (%s %s) for user %s",
            order_number,
            total_amount,
            currency,
            user_id,
        )

        # 4. Notify payment confirmed
        await self.notifications.notify(
            NotificationPayload(
                event_type=NotificationEventType.PAYMENT_CONFIRMED,
                tenant_id=tenant_id,
                recipient=recipient,
                order_id=order.id,
                order_number=order.order_number,
                message=f"💳 Payment confirmed for order #{order.order_number}. Amount: {total_amount} {currency}.",
            )
        )

        # 5. Execute fulfillment
        attempt: FulfillmentAttempt | None = None
        if execute_sync:
            attempt = await self.fulfillment.execute_order_fulfillment(
                session=session,
                order_id=order.id,
                recipient=recipient,
            )

        return order, attempt
