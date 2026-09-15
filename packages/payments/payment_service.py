import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStatus
from packages.core.exceptions import (
    InvalidStateTransitionError,
    TenantAccessViolationError,
)
from packages.payments.exceptions import (
    PaymentError,
    PaymentIntegrityError,
    PaymentIntentNotFoundError,
    PaymentProviderError,
    UnsupportedProviderCapabilityError,
    WebhookVerificationError,
)
from packages.payments.models import (
    LedgerTransaction,
    PaymentIntent,
    PaymentProviderConfig,
    PaymentTransaction,
    PaymentTransactionType,
    PaymentWebhookEvent,
)
from packages.payments.providers.interface import (
    PaymentCreateRequest,
    PaymentRefundRequest,
)
from packages.payments.providers.registry import (
    PaymentProviderRegistry,
    default_payment_provider_registry,
)
from packages.payments.service import (
    CANONICAL_PAYMENT_REFUND_TYPE,
    CANONICAL_SETTLEMENT_TYPE,
    LedgerService,
)
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.secrets import EnvSecretStorage, SecretStorage

logger = logging.getLogger("payments.service")


class PaymentService:
    """Core payment infrastructure coordinating durable PaymentIntents, gateways, webhooks, and ledger settlements."""

    def __init__(
        self,
        registry: PaymentProviderRegistry | None = None,
        secret_storage: SecretStorage | None = None,
    ) -> None:
        self.registry = registry or default_payment_provider_registry
        self.secret_storage = secret_storage or EnvSecretStorage()

    async def create_payment_intent(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        order_id: uuid.UUID,
        provider_name: str,
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
        return_url: str | None = None,
    ) -> PaymentIntent:
        """Creates a durable payment intent representing an authoritative payment attempt for an Order.

        The amount is strictly server-authoritative and derived directly from the Order record.
        """
        if not idempotency_key:
            raise ValueError("idempotency_key is required for payment intent creation.")

        # 1. Fetch Order scoped strictly to tenant
        stmt = select(Order).where(
            Order.id == order_id,
            Order.tenant_id == tenant_id,
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if order is None:
            raise PaymentError(f"Order {order_id} not found for tenant {tenant_id}.")

        if order.status not in (OrderStatus.PENDING, OrderStatus.PAYMENT_PENDING):
            raise PaymentError(
                f"Cannot create payment intent for order {order.order_number} in status {order.status.value}."
            )

        if order.user_id != user_id:
            raise TenantAccessViolationError(
                f"User {user_id} does not have access to order {order_id}."
            )

        # Authoritative amounts derived from Order
        authoritative_amount = order.total_amount
        authoritative_currency = order.currency

        # 2. Check for existing intent with same idempotency_key for this tenant
        existing_stmt = select(PaymentIntent).where(
            PaymentIntent.tenant_id == tenant_id,
            PaymentIntent.idempotency_key == idempotency_key,
        )
        existing_intent = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing_intent is not None:
            if existing_intent.order_id != order_id:
                raise PaymentIntegrityError(
                    f"Idempotency key {idempotency_key} is already bound to order {existing_intent.order_id}."
                )
            if existing_intent.amount != authoritative_amount:
                raise PaymentIntegrityError(
                    f"Idempotency key {idempotency_key} amount mismatch: expected {existing_intent.amount}, got {authoritative_amount}."
                )
            return existing_intent

        # 3. Create intent record and enforce DB constraints via savepoint
        intent = PaymentIntent(
            tenant_id=tenant_id,
            order_id=order_id,
            user_id=user_id,
            provider=provider_name.lower(),
            currency=authoritative_currency,
            amount=authoritative_amount,
            status=PaymentIntentStatus.CREATED,
            idempotency_key=idempotency_key,
            metadata_json=metadata or {},
        )

        try:
            async with session.begin_nested():
                session.add(intent)
                await session.flush()
        except IntegrityError as exc:
            # Check if existing intent with this idempotency key was committed by concurrent worker
            existing = (await session.execute(existing_stmt)).scalar_one_or_none()
            if existing is not None:
                if existing.order_id != order_id or existing.amount != authoritative_amount:
                    raise PaymentIntegrityError(
                        f"Concurrent idempotency conflict for key {idempotency_key}."
                    ) from exc
                return existing
            # If active intent index violated, raise domain error
            raise PaymentError(
                f"An active payment intent already exists for order {order_id}."
            ) from exc

        # 4. Initialize payment with provider adapter
        provider = await self.registry.get_provider(
            session=session,
            tenant_id=tenant_id,
            provider_name=provider_name,
            secret_storage=self.secret_storage,
        )

        create_request = PaymentCreateRequest(
            order_id=order_id,
            amount=authoritative_amount,
            currency=authoritative_currency,
            idempotency_key=idempotency_key,
            metadata=metadata or {},
            return_url=return_url,
        )

        provider_result = await provider.create_payment(create_request)
        intent.provider_payment_id = provider_result.provider_payment_id

        if provider_result.status != intent.status:
            intent.transition_to(provider_result.status)

        # Transition order to PAYMENT_PENDING if not already
        if order.status == OrderStatus.PENDING:
            order.transition_to(OrderStatus.PAYMENT_PENDING)

        await session.flush()
        return intent

    async def get_payment_intent(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        intent_id: uuid.UUID,
    ) -> PaymentIntent:
        """Loads a PaymentIntent, strictly enforcing tenant isolation."""
        stmt = select(PaymentIntent).where(PaymentIntent.id == intent_id)
        intent = (await session.execute(stmt)).scalar_one_or_none()

        if intent is None:
            raise PaymentIntentNotFoundError(f"Payment intent {intent_id} not found.")

        if intent.tenant_id != tenant_id:
            raise TenantAccessViolationError(
                f"Tenant {tenant_id} cannot access payment intent {intent_id} belonging to {intent.tenant_id}."
            )

        return intent

    async def cancel_payment_intent(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        intent_id: uuid.UUID,
    ) -> PaymentIntent:
        """Cancels an existing non-terminal payment intent."""
        intent = await self.get_payment_intent(session, tenant_id, intent_id)
        if intent.status in (
            PaymentIntentStatus.SUCCEEDED,
            PaymentIntentStatus.FAILED,
            PaymentIntentStatus.EXPIRED,
            PaymentIntentStatus.CANCELLED,
        ):
            raise InvalidStateTransitionError(
                f"Cannot cancel payment intent {intent_id} in terminal status {intent.status.value}."
            )
        intent.transition_to(PaymentIntentStatus.CANCELLED)
        order = await session.get(Order, intent.order_id)
        if order is not None and order.status == OrderStatus.PAYMENT_PENDING:
            order.transition_to(OrderStatus.CANCELLED)
        await session.flush()
        return intent

    async def settle_payment_intent(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        intent_id: uuid.UUID,
        verified_amount: Decimal | None = None,
        verified_currency: str | None = None,
    ) -> tuple[PaymentIntent, Any]:
        """Settles a payment intent, transitions state to SUCCEEDED, and credits the wallet ledger exactly once.

        Database-enforced idempotency on uq_settlement_idempotency prevents double-crediting
        under webhook-first races, user return races, or concurrent retries.
        """
        intent = await self.get_payment_intent(session, tenant_id, intent_id)

        # Fetch Order scoped to tenant
        order = await session.get(Order, intent.order_id)
        if order is None or order.tenant_id != tenant_id:
            raise TenantAccessViolationError("Order does not belong to tenant.")

        # Section 12: Verify amount and currency integrity
        if verified_amount is not None and verified_amount != intent.amount:
            raise PaymentIntegrityError(
                f"Settlement amount mismatch: verified={verified_amount}, intent={intent.amount}."
            )
        if verified_currency is not None and verified_currency != intent.currency:
            raise PaymentIntegrityError(
                f"Settlement currency mismatch: verified={verified_currency}, intent={intent.currency}."
            )
        if intent.amount != order.total_amount:
            raise PaymentIntegrityError(
                f"Order amount mismatch: order={order.total_amount}, intent={intent.amount}."
            )
        if intent.currency != order.currency:
            raise PaymentIntegrityError(
                f"Order currency mismatch: order={order.currency}, intent={intent.currency}."
            )

        # Check if already settled
        wallet = await LedgerService.get_or_create_wallet(
            session=session,
            tenant_id=tenant_id,
            user_id=intent.user_id,
            currency=intent.currency,
        )

        # Check if already settled in ledger
        ledger_stmt = select(LedgerTransaction).where(
            LedgerTransaction.wallet_id == wallet.id,
            LedgerTransaction.reference_type == CANONICAL_SETTLEMENT_TYPE,
            LedgerTransaction.reference_id == str(intent.id),
        )
        existing_tx = (await session.execute(ledger_stmt)).scalars().first()
        if existing_tx is not None:
            if intent.status != PaymentIntentStatus.SUCCEEDED:
                intent.transition_to(PaymentIntentStatus.SUCCEEDED)
            if order.status in (OrderStatus.PENDING, OrderStatus.PAYMENT_PENDING):
                order.transition_to(OrderStatus.PAID)
            await session.flush()
            return intent, existing_tx

        # Execute database-enforced settlement credit to ledger
        ledger_tx = await LedgerService.settle_payment(
            session=session,
            wallet=wallet,
            amount=intent.amount,
            payment_intent_id=intent.id,
            description=f"Payment settlement for order {order.order_number}",
        )

        # Transition intent state if not already
        if intent.status != PaymentIntentStatus.SUCCEEDED:
            intent.transition_to(PaymentIntentStatus.SUCCEEDED)

        # Transition order to PAID if not already
        if order.status in (OrderStatus.PENDING, OrderStatus.PAYMENT_PENDING):
            order.transition_to(OrderStatus.PAID)

        # Record payment transaction
        pay_tx = PaymentTransaction(
            tenant_id=tenant_id,
            payment_intent_id=intent.id,
            transaction_type=PaymentTransactionType.SETTLEMENT,
            amount=intent.amount,
            currency=intent.currency,
            status="SUCCESS",
            provider_tx_id=intent.provider_payment_id,
            reference_id=str(intent.id),
        )
        session.add(pay_tx)

        await session.flush()
        return intent, ledger_tx

    async def process_webhook(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        provider_name: str,
        payload_bytes: bytes,
        headers: dict[str, str],
    ) -> PaymentWebhookEvent:
        """Processes an incoming payment webhook idempotently and securely.

        Sequence:
        1. Resolve tenant provider configuration
        2. Verify cryptographic signature
        3. Deduplicate provider_event_id at database layer
        4. Load PaymentIntent and verify amount/currency/tenant integrity
        5. Apply state machine transition
        6. Credit ledger exactly once upon settlement
        7. Mark webhook processed
        """
        normalized_name = provider_name.lower()

        # Step 1: Verify tenant config exists & is enabled
        cfg_stmt = select(PaymentProviderConfig).where(
            PaymentProviderConfig.tenant_id == tenant_id,
            PaymentProviderConfig.provider_name == normalized_name,
        )
        config = (await session.execute(cfg_stmt)).scalar_one_or_none()
        if config is None or not config.is_enabled:
            raise PaymentProviderError(f"Payment provider '{provider_name}' not configured or disabled.")

        # Step 2: Resolve webhook secret
        webhook_secret = (
            await self.secret_storage.get_secret(config.webhook_secret_ref)
            if config.webhook_secret_ref
            else ""
        )

        provider = await self.registry.get_provider(
            session=session,
            tenant_id=tenant_id,
            provider_name=provider_name,
            secret_storage=self.secret_storage,
        )

        # Verify signature
        verification = await provider.verify_webhook(payload_bytes, headers, webhook_secret)
        if not verification.is_valid:
            raise WebhookVerificationError("Webhook signature or payload verification failed.")

        payload_hash = hashlib.sha256(payload_bytes).hexdigest()

        # Step 3: Check database-enforced deduplication
        dup_stmt = select(PaymentWebhookEvent).where(
            PaymentWebhookEvent.tenant_id == tenant_id,
            PaymentWebhookEvent.provider == normalized_name,
            PaymentWebhookEvent.provider_event_id == verification.provider_event_id,
        )
        existing_event = (await session.execute(dup_stmt)).scalar_one_or_none()
        if existing_event is not None:
            logger.info(
                "Duplicate webhook event %s received for tenant %s. Returning existing event.",
                verification.provider_event_id,
                tenant_id,
            )
            return existing_event

        # Step 4: Resolve PaymentIntent
        intent: PaymentIntent | None = None
        if verification.provider_payment_id:
            intent_stmt = select(PaymentIntent).where(
                PaymentIntent.tenant_id == tenant_id,
                PaymentIntent.provider_payment_id == verification.provider_payment_id,
            )
            intent = (await session.execute(intent_stmt)).scalar_one_or_none()

        try:
            payload_dict = json.loads(payload_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload_dict = verification.raw_data

        webhook_event = PaymentWebhookEvent(
            tenant_id=tenant_id,
            provider=normalized_name,
            provider_event_id=verification.provider_event_id,
            payment_intent_id=intent.id if intent else None,
            event_type=verification.event_type,
            signature_verified=True,
            processed=False,
            payload_hash=payload_hash,
            payload_json=payload_dict,
        )

        try:
            async with session.begin_nested():
                session.add(webhook_event)
                await session.flush()
        except IntegrityError:
            # Concurrent delivery of same event id
            existing_event = (await session.execute(dup_stmt)).scalar_one_or_none()
            if existing_event:
                return existing_event
            raise

        # Step 5: Process intent state & settlement
        if intent is not None:
            # Verify amount and currency integrity if supplied by webhook
            if verification.amount is not None and verification.amount != intent.amount:
                raise PaymentIntegrityError(
                    f"Webhook amount {verification.amount} does not match intent amount {intent.amount}."
                )
            if verification.currency is not None and verification.currency != intent.currency:
                raise PaymentIntegrityError(
                    f"Webhook currency {verification.currency} does not match intent currency {intent.currency}."
                )

            if verification.status == PaymentIntentStatus.SUCCEEDED:
                await self.settle_payment_intent(
                    session=session,
                    tenant_id=tenant_id,
                    intent_id=intent.id,
                    verified_amount=verification.amount,
                    verified_currency=verification.currency,
                )
            elif (
                verification.status in (
                    PaymentIntentStatus.FAILED,
                    PaymentIntentStatus.CANCELLED,
                    PaymentIntentStatus.EXPIRED,
                )
                and intent.status != verification.status
            ):
                intent.transition_to(verification.status)

        webhook_event.processed = True
        webhook_event.processed_at = datetime.now(UTC)
        await session.flush()
        return webhook_event

    async def refund_payment(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        intent_id: uuid.UUID,
        amount: Decimal | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> PaymentTransaction:
        """Executes a gateway payment refund distinct from fulfillment failure refunds.

        Uses CANONICAL_PAYMENT_REFUND_TYPE ('PAYMENT_REFUND').
        """
        intent = await self.get_payment_intent(session, tenant_id, intent_id)

        if intent.status != PaymentIntentStatus.SUCCEEDED:
            raise PaymentError(f"Cannot refund payment intent in status {intent.status.value}.")

        refund_amount = amount if amount is not None else intent.amount
        if refund_amount <= Decimal("0.00") or refund_amount > intent.amount:
            raise ValueError(f"Invalid refund amount {refund_amount}. Must be between 0 and {intent.amount}.")

        provider = await self.registry.get_provider(
            session=session,
            tenant_id=tenant_id,
            provider_name=intent.provider,
            secret_storage=self.secret_storage,
        )

        if not provider.supports_refunds:
            raise UnsupportedProviderCapabilityError(
                f"Provider '{intent.provider}' does not support refunds."
            )

        if not provider.supports_partial_refunds and refund_amount < intent.amount:
            raise UnsupportedProviderCapabilityError(
                f"Provider '{intent.provider}' does not support partial refunds."
            )

        refund_key = idempotency_key or f"refund_{intent.id}_{refund_amount}"

        # Call provider gateway refund
        refund_req = PaymentRefundRequest(
            provider_payment_id=intent.provider_payment_id or "",
            amount=refund_amount,
            currency=intent.currency,
            idempotency_key=refund_key,
            reason=reason,
        )
        refund_res = await provider.refund(refund_req)

        # Record payment transaction
        pay_tx = PaymentTransaction(
            tenant_id=tenant_id,
            payment_intent_id=intent.id,
            transaction_type=PaymentTransactionType.REFUND,
            amount=refund_amount,
            currency=intent.currency,
            status="SUCCESS",
            provider_tx_id=refund_res.provider_refund_id,
            reference_id=refund_key,
            metadata_json={"reason": reason},
        )
        session.add(pay_tx)

        # Distinct ledger refund under CANONICAL_PAYMENT_REFUND_TYPE
        wallet = await LedgerService.get_or_create_wallet(
            session=session,
            tenant_id=tenant_id,
            user_id=intent.user_id,
            currency=intent.currency,
        )

        await LedgerService.refund(
            session=session,
            wallet=wallet,
            amount=refund_amount,
            reference_id=str(intent.id),
            reference_type=CANONICAL_PAYMENT_REFUND_TYPE,
            description=reason or f"Payment refund for intent {intent.id}",
        )

        # Transition order to REFUNDED if fully refunded and in PAID status
        order = await session.get(Order, intent.order_id)
        if order is not None and refund_amount == intent.amount and order.status == OrderStatus.PAID:
            order.transition_to(OrderStatus.REFUNDED)

        await session.flush()
        return pay_tx
