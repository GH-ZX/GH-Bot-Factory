import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStatus
from packages.core.exceptions import (
    InsufficientFundsError,
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
    PaymentIntentPurpose,
    PaymentProviderConfig,
    PaymentReconciliationEvent,
    PaymentReconciliationEventStatus,
    PaymentTransaction,
    PaymentTransactionType,
    PaymentWebhookEvent,
    Wallet,
    WalletTopUpReversal,
    WalletTopUpReversalStatus,
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
    CANONICAL_TOPUP_REVERSAL_TYPE,
    LedgerService,
)
from packages.payments.state_machine import PaymentIntentStatus
from packages.telegram.secrets import SecretStorage, get_default_secret_storage
from packages.tenants.models import User

logger = logging.getLogger("payments.service")


class PaymentService:
    """Core payment infrastructure coordinating durable PaymentIntents, gateways, webhooks, and ledger settlements."""

    def __init__(
        self,
        registry: PaymentProviderRegistry | None = None,
        secret_storage: SecretStorage | None = None,
    ) -> None:
        self.registry = registry or default_payment_provider_registry
        self.secret_storage = secret_storage or get_default_secret_storage()

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
        stmt = select(Order).where(Order.id == order_id)
        order = (await session.execute(stmt)).scalar_one_or_none()
        if order is None:
            raise PaymentError(f"Order {order_id} not found.")

        if order.tenant_id != tenant_id:
            raise TenantAccessViolationError(
                f"Tenant {tenant_id} cannot access order {order_id} belonging to {order.tenant_id}."
            )

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
            purpose=PaymentIntentPurpose.ORDER_PAYMENT,
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
        intent.checkout_url = self._validated_checkout_url(provider_result.checkout_url)

        if provider_result.status == PaymentIntentStatus.SUCCEEDED:
            await self.settle_payment_intent(
                session=session,
                tenant_id=tenant_id,
                intent_id=intent.id,
                verified_amount=authoritative_amount,
                verified_currency=authoritative_currency,
            )
        elif provider_result.status != intent.status:
            intent.transition_to(provider_result.status)

        # Transition order to PAYMENT_PENDING only while payment is still open.
        if order.status == OrderStatus.PENDING:
            order.transition_to(OrderStatus.PAYMENT_PENDING)

        await session.flush()
        return intent

    @staticmethod
    def _validated_checkout_url(checkout_url: str | None) -> str | None:
        if checkout_url is None:
            return None
        parsed = urlsplit(checkout_url.strip())
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise PaymentProviderError("Payment provider returned an invalid checkout URL.")
        if parsed.username or parsed.password:
            raise PaymentProviderError("Payment provider checkout URL must not contain credentials.")
        return checkout_url.strip()

    async def create_wallet_topup_intent(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        amount: Decimal,
        currency: str,
        provider_name: str,
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
        return_url: str | None = None,
    ) -> PaymentIntent:
        """Create a provider-backed intent whose successful settlement funds a wallet.

        Unlike order payments, a top-up has no Order record. The requested amount is
        validated against tenant provider policy before it is sent upstream, and the
        idempotency key is bound to user, provider, amount, and currency.
        """
        if not idempotency_key:
            raise ValueError("idempotency_key is required for wallet top-up creation.")

        normalized_provider = provider_name.strip().lower()
        normalized_currency = currency.strip().upper()
        if len(normalized_currency) != 3 or not normalized_currency.isalpha():
            raise PaymentError("Wallet top-up currency must be a three-letter code.")

        try:
            normalized_amount = amount.quantize(Decimal("0.01"))
        except (InvalidOperation, AttributeError) as exc:
            raise PaymentError("Wallet top-up amount is invalid.") from exc
        if normalized_amount != amount or normalized_amount <= Decimal("0.00"):
            raise PaymentError("Wallet top-up amount must be positive with at most two decimals.")

        config_stmt = select(PaymentProviderConfig).where(
            PaymentProviderConfig.tenant_id == tenant_id,
            PaymentProviderConfig.provider_name == normalized_provider,
            PaymentProviderConfig.is_enabled.is_(True),
        )
        config = (await session.execute(config_stmt)).scalar_one_or_none()
        if config is None:
            raise PaymentProviderError(
                f"Payment provider '{provider_name}' is not configured or enabled for this tenant."
            )

        policy = config.settings_json or {}
        if policy.get("topup_enabled") is False:
            raise PaymentProviderError(
                f"Payment provider '{provider_name}' is not enabled for wallet top-ups."
            )
        try:
            min_amount = Decimal(str(policy.get("topup_min_amount", "1.00")))
            max_amount = Decimal(str(policy.get("topup_max_amount", "1000.00")))
        except InvalidOperation as exc:
            raise PaymentProviderError(
                f"Payment provider '{provider_name}' has invalid wallet top-up amount policy."
            ) from exc
        if min_amount <= Decimal("0.00") or max_amount < min_amount:
            raise PaymentProviderError(
                f"Payment provider '{provider_name}' has invalid wallet top-up limits."
            )
        currencies = {
            str(value).upper()
            for value in policy.get("topup_currencies", ["USD"])
            if str(value).strip()
        }
        if normalized_currency not in currencies:
            raise PaymentError(
                f"Currency {normalized_currency} is not enabled for provider '{provider_name}'."
            )
        if normalized_amount < min_amount or normalized_amount > max_amount:
            raise PaymentError(
                f"Wallet top-up amount must be between {min_amount} and {max_amount} {normalized_currency}."
            )
        if policy.get("topup_whole_units_only") is True and normalized_amount != normalized_amount.to_integral_value():
            raise PaymentError(
                f"Provider '{provider_name}' requires whole-unit wallet top-up amounts."
            )

        effective_metadata = dict(metadata or {})
        if policy.get("terms_required") is True:
            terms_url = str(policy.get("terms_url") or "").strip()
            parsed_terms = urlsplit(terms_url)
            if parsed_terms.scheme.lower() != "https" or not parsed_terms.netloc:
                raise PaymentProviderError(
                    f"Payment provider '{provider_name}' requires a valid HTTPS terms_url."
                )
            if effective_metadata.get("terms_accepted") is not True:
                raise PaymentError("You must accept the store payment terms before continuing.")
            effective_metadata["terms_url"] = terms_url
            effective_metadata["terms_version"] = str(policy.get("terms_version") or "current")
            effective_metadata["terms_accepted_at"] = datetime.now(UTC).isoformat()

        existing_stmt = select(PaymentIntent).where(
            PaymentIntent.tenant_id == tenant_id,
            PaymentIntent.idempotency_key == idempotency_key,
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            if (
                existing.purpose != PaymentIntentPurpose.WALLET_TOPUP
                or existing.user_id != user_id
                or existing.order_id is not None
                or existing.provider != normalized_provider
                or existing.amount != normalized_amount
                or existing.currency != normalized_currency
            ):
                raise PaymentIntegrityError(
                    f"Idempotency key {idempotency_key} is already bound to a different payment request."
                )
            return existing

        intent = PaymentIntent(
            tenant_id=tenant_id,
            order_id=None,
            purpose=PaymentIntentPurpose.WALLET_TOPUP,
            user_id=user_id,
            provider=normalized_provider,
            currency=normalized_currency,
            amount=normalized_amount,
            status=PaymentIntentStatus.CREATED,
            idempotency_key=idempotency_key,
            metadata_json=effective_metadata,
        )

        try:
            async with session.begin_nested():
                session.add(intent)
                await session.flush()
        except IntegrityError as exc:
            concurrent = (await session.execute(existing_stmt)).scalar_one_or_none()
            if concurrent is not None:
                if (
                    concurrent.purpose == PaymentIntentPurpose.WALLET_TOPUP
                    and concurrent.user_id == user_id
                    and concurrent.provider == normalized_provider
                    and concurrent.amount == normalized_amount
                    and concurrent.currency == normalized_currency
                ):
                    return concurrent
            raise PaymentIntegrityError(
                f"Concurrent wallet top-up idempotency conflict for key {idempotency_key}."
            ) from exc

        provider = await self.registry.get_provider(
            session=session,
            tenant_id=tenant_id,
            provider_name=normalized_provider,
            secret_storage=self.secret_storage,
        )
        provider_result = await provider.create_payment(
            PaymentCreateRequest(
                order_id=None,
                amount=normalized_amount,
                currency=normalized_currency,
                idempotency_key=idempotency_key,
                metadata={
                    **effective_metadata,
                    "purpose": PaymentIntentPurpose.WALLET_TOPUP.value,
                    "payment_intent_id": str(intent.id),
                    "tenant_id": str(tenant_id),
                    "user_id": str(user_id),
                },
                return_url=return_url,
            )
        )
        intent.provider_payment_id = provider_result.provider_payment_id
        intent.checkout_url = self._validated_checkout_url(provider_result.checkout_url)
        if provider_result.raw_data:
            intent.metadata_json = {**(intent.metadata_json or {}), "provider_create": provider_result.raw_data}
        if provider_result.status == PaymentIntentStatus.SUCCEEDED:
            await self._settle_wallet_topup(
                session=session,
                intent=intent,
                verified_amount=normalized_amount,
                verified_currency=normalized_currency,
            )
        elif provider_result.status != intent.status:
            intent.transition_to(provider_result.status)
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

        if intent.purpose == PaymentIntentPurpose.WALLET_TOPUP:
            return await self._settle_wallet_topup(
                session=session,
                intent=intent,
                verified_amount=verified_amount,
                verified_currency=verified_currency,
            )

        if intent.order_id is None:
            raise PaymentIntegrityError("Order payment intent is missing its authoritative order_id.")

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

    async def _settle_wallet_topup(
        self,
        session: AsyncSession,
        intent: PaymentIntent,
        verified_amount: Decimal | None = None,
        verified_currency: str | None = None,
    ) -> tuple[PaymentIntent, Any]:
        """Credit a top-up wallet exactly once after authoritative provider success."""
        if intent.order_id is not None:
            raise PaymentIntegrityError("Wallet top-up intent must not reference an order.")
        if verified_amount is not None and verified_amount != intent.amount:
            raise PaymentIntegrityError(
                f"Settlement amount mismatch: verified={verified_amount}, intent={intent.amount}."
            )
        if verified_currency is not None and verified_currency != intent.currency:
            raise PaymentIntegrityError(
                f"Settlement currency mismatch: verified={verified_currency}, intent={intent.currency}."
            )

        wallet = await LedgerService.get_or_create_wallet(
            session=session,
            tenant_id=intent.tenant_id,
            user_id=intent.user_id,
            currency=intent.currency,
        )
        ledger_stmt = select(LedgerTransaction).where(
            LedgerTransaction.wallet_id == wallet.id,
            LedgerTransaction.reference_type == CANONICAL_SETTLEMENT_TYPE,
            LedgerTransaction.reference_id == str(intent.id),
        )
        existing_tx = (await session.execute(ledger_stmt)).scalars().first()
        if existing_tx is not None:
            if intent.status != PaymentIntentStatus.SUCCEEDED:
                intent.transition_to(PaymentIntentStatus.SUCCEEDED)
            await session.flush()
            return intent, existing_tx

        ledger_tx = await LedgerService.settle_payment(
            session=session,
            wallet=wallet,
            amount=intent.amount,
            payment_intent_id=intent.id,
            description=f"Wallet top-up settlement via {intent.provider}",
        )
        if intent.status != PaymentIntentStatus.SUCCEEDED:
            intent.transition_to(PaymentIntentStatus.SUCCEEDED)

        session.add(
            PaymentTransaction(
                tenant_id=intent.tenant_id,
                payment_intent_id=intent.id,
                transaction_type=PaymentTransactionType.SETTLEMENT,
                amount=intent.amount,
                currency=intent.currency,
                status="SUCCESS",
                provider_tx_id=intent.provider_payment_id,
                reference_id=str(intent.id),
                metadata_json={"purpose": PaymentIntentPurpose.WALLET_TOPUP.value},
            )
        )
        await session.flush()
        return intent, ledger_tx

    @staticmethod
    def _parse_stars_invoice_payload(invoice_payload: str) -> uuid.UUID:
        prefix = "ghbf:wallet-topup:"
        if not invoice_payload.startswith(prefix):
            raise PaymentIntegrityError("Telegram Stars invoice payload is not recognized.")
        try:
            return uuid.UUID(invoice_payload.removeprefix(prefix))
        except ValueError as exc:
            raise PaymentIntegrityError("Telegram Stars invoice payload is malformed.") from exc

    async def validate_telegram_stars_checkout(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        invoice_payload: str,
        amount: Decimal,
        currency: str,
    ) -> PaymentIntent:
        """Validate a Telegram pre-checkout query against the authoritative top-up intent."""
        intent_id = self._parse_stars_invoice_payload(invoice_payload)
        intent = await self.get_payment_intent(session, tenant_id, intent_id)
        if intent.purpose != PaymentIntentPurpose.WALLET_TOPUP or intent.provider != "telegram_stars":
            raise PaymentIntegrityError("Telegram Stars invoice does not reference a wallet top-up.")
        if intent.user_id != user_id:
            raise PaymentIntegrityError("Telegram Stars invoice belongs to a different customer.")
        if currency.upper() != "XTR" or intent.currency != "XTR":
            raise PaymentIntegrityError("Telegram Stars checkout currency mismatch.")
        if amount != intent.amount:
            raise PaymentIntegrityError(
                f"Telegram Stars checkout amount mismatch: expected {intent.amount}, got {amount}."
            )
        if intent.status not in {PaymentIntentStatus.PENDING, PaymentIntentStatus.PROCESSING, PaymentIntentStatus.SUCCEEDED}:
            raise PaymentError(
                f"Telegram Stars top-up cannot be paid in status {intent.status.value}."
            )
        return intent

    async def settle_telegram_stars_topup(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        invoice_payload: str,
        telegram_payment_charge_id: str,
        amount: Decimal,
        currency: str,
    ) -> PaymentIntent:
        """Settle one successful_payment update idempotently and bind its Telegram charge id."""
        intent = await self.validate_telegram_stars_checkout(
            session=session,
            tenant_id=tenant_id,
            user_id=user_id,
            invoice_payload=invoice_payload,
            amount=amount,
            currency=currency,
        )
        if not telegram_payment_charge_id:
            raise PaymentIntegrityError("Telegram successful payment is missing a charge id.")
        existing_provider_id = intent.provider_payment_id or ""
        if (
            existing_provider_id
            and not existing_provider_id.startswith("stars_invoice:")
            and existing_provider_id != telegram_payment_charge_id
        ):
            raise PaymentIntegrityError("Telegram Stars top-up is already bound to another charge id.")

        intent.provider_payment_id = telegram_payment_charge_id
        intent.metadata_json = {
            **(intent.metadata_json or {}),
            "telegram_payment_charge_id": telegram_payment_charge_id,
        }
        await self._settle_wallet_topup(
            session=session,
            intent=intent,
            verified_amount=amount,
            verified_currency="XTR",
        )
        return intent

    @staticmethod
    async def _record_topup_reversal_payment_transaction(
        session: AsyncSession,
        intent: PaymentIntent,
        reversal: WalletTopUpReversal,
        provider_tx_id: str,
        origin: str,
    ) -> None:
        """Record the provider refund event once, including concurrent reconciliation races."""
        tx_ref = f"topup_reversal:{reversal.id}"
        stmt = select(PaymentTransaction).where(
            PaymentTransaction.tenant_id == intent.tenant_id,
            PaymentTransaction.payment_intent_id == intent.id,
            PaymentTransaction.transaction_type == PaymentTransactionType.REFUND,
            PaymentTransaction.reference_id == tx_ref,
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing is not None:
            if existing.amount != reversal.amount or existing.currency != reversal.currency:
                raise PaymentIntegrityError(
                    f"Top-up reversal payment transaction mismatch for reversal {reversal.id}."
                )
            return

        try:
            async with session.begin_nested():
                session.add(
                    PaymentTransaction(
                        tenant_id=intent.tenant_id,
                        payment_intent_id=intent.id,
                        transaction_type=PaymentTransactionType.REFUND,
                        amount=reversal.amount,
                        currency=reversal.currency,
                        status="SUCCESS",
                        provider_tx_id=provider_tx_id,
                        reference_id=tx_ref,
                        metadata_json={
                            "purpose": PaymentIntentPurpose.WALLET_TOPUP.value,
                            "reversal_id": str(reversal.id),
                            "origin": origin,
                        },
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            existing = (await session.execute(stmt)).scalars().first()
            if existing is None:
                raise
            if existing.amount != reversal.amount or existing.currency != reversal.currency:
                raise PaymentIntegrityError(
                    f"Concurrent top-up reversal transaction mismatch for reversal {reversal.id}."
                ) from exc

    async def complete_wallet_topup_reversal_from_provider_event(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        reversal: WalletTopUpReversal,
        provider_tx_id: str,
        provider_metadata: dict[str, Any] | None = None,
    ) -> WalletTopUpReversal:
        """Close a locally requested reversal after observing the provider-side refund.

        This is the crash-recovery path for the window where the provider accepted a refund
        but the worker died before the local completion transaction committed.
        """
        if reversal.tenant_id != tenant_id:
            raise TenantAccessViolationError("Top-up reversal belongs to another tenant.")
        if reversal.status == WalletTopUpReversalStatus.COMPLETED:
            return reversal
        if reversal.status == WalletTopUpReversalStatus.MANUAL_REVIEW:
            return reversal

        intent = await self.get_payment_intent(session, tenant_id, reversal.payment_intent_id)
        if intent.provider_payment_id != provider_tx_id:
            raise PaymentIntegrityError("Observed provider refund does not match the original payment charge.")
        wallet = await session.get(Wallet, reversal.wallet_id)
        if wallet is None or wallet.tenant_id != tenant_id:
            raise PaymentIntegrityError("Wallet for top-up reversal is missing or cross-tenant.")

        await LedgerService.reserve_topup_reversal(
            session=session,
            wallet=wallet,
            amount=reversal.amount,
            reversal_id=reversal.id,
            description=f"Reserve observed top-up refund {intent.id}",
        )
        metadata = dict(reversal.metadata_json or {})
        metadata["provider_observed_outbound"] = True
        if provider_metadata is not None:
            metadata["provider_observation"] = provider_metadata
        reversal.metadata_json = metadata
        reversal.provider_refund_id = provider_tx_id
        reversal.status = WalletTopUpReversalStatus.COMPLETED
        reversal.completed_at = datetime.now(UTC)
        reversal.next_attempt_at = None
        reversal.last_error_code = None
        reversal.last_error_detail = None
        await self._record_topup_reversal_payment_transaction(
            session, intent, reversal, provider_tx_id, origin="merchant_request_provider_observed"
        )
        await session.flush()
        return reversal

    async def record_external_wallet_topup_reversal(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        intent: PaymentIntent,
        provider_metadata: dict[str, Any],
    ) -> WalletTopUpReversal:
        """Mirror an externally-originated provider reversal into the wallet ledger.

        Unlike merchant-initiated refunds, the external provider action has already happened.
        If the customer has spent the value, the wallet is frozen and the durable record is
        moved to MANUAL_REVIEW instead of allowing further value extraction.
        """
        if intent.tenant_id != tenant_id:
            raise TenantAccessViolationError("Payment intent belongs to another tenant.")
        if intent.purpose != PaymentIntentPurpose.WALLET_TOPUP:
            raise PaymentIntegrityError("External reversal target is not a wallet top-up.")
        if intent.status != PaymentIntentStatus.SUCCEEDED:
            raise PaymentIntegrityError("External reversal target is not a settled payment intent.")
        if not intent.provider_payment_id:
            raise PaymentIntegrityError("External reversal target has no provider payment identifier.")

        existing_stmt = select(WalletTopUpReversal).where(
            WalletTopUpReversal.tenant_id == tenant_id,
            WalletTopUpReversal.payment_intent_id == intent.id,
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            return await self.complete_wallet_topup_reversal_from_provider_event(
                session,
                tenant_id,
                existing,
                intent.provider_payment_id,
                provider_metadata,
            )

        wallet = await LedgerService.get_or_create_wallet(
            session=session,
            tenant_id=tenant_id,
            user_id=intent.user_id,
            currency=intent.currency,
        )
        settlement_stmt = select(LedgerTransaction).where(
            LedgerTransaction.wallet_id == wallet.id,
            LedgerTransaction.reference_type == CANONICAL_SETTLEMENT_TYPE,
            LedgerTransaction.reference_id == str(intent.id),
        )
        settlement = (await session.execute(settlement_stmt)).scalars().first()
        if settlement is None or settlement.amount != intent.amount:
            raise PaymentIntegrityError("External reversal target lacks its authoritative settlement ledger entry.")

        digest = hashlib.sha256(
            f"{tenant_id}:{intent.provider}:{intent.provider_payment_id}:external-reversal".encode("utf-8")
        ).hexdigest()
        reversal = WalletTopUpReversal(
            tenant_id=tenant_id,
            payment_intent_id=intent.id,
            user_id=intent.user_id,
            wallet_id=wallet.id,
            provider=intent.provider,
            original_provider_payment_id=intent.provider_payment_id,
            provider_refund_id=intent.provider_payment_id,
            amount=intent.amount,
            currency=intent.currency,
            idempotency_key=f"external:{digest[:64]}",
            status=WalletTopUpReversalStatus.REQUESTED,
            metadata_json={
                "origin": "EXTERNAL_PROVIDER",
                "provider_observation": provider_metadata,
            },
        )
        try:
            async with session.begin_nested():
                session.add(reversal)
                await session.flush()
        except IntegrityError as exc:
            concurrent = (await session.execute(existing_stmt)).scalar_one_or_none()
            if concurrent is not None:
                return await self.complete_wallet_topup_reversal_from_provider_event(
                    session,
                    tenant_id,
                    concurrent,
                    intent.provider_payment_id,
                    provider_metadata,
                )
            raise PaymentIntegrityError("Concurrent external top-up reversal conflict.") from exc

        try:
            await LedgerService.reserve_topup_reversal(
                session=session,
                wallet=wallet,
                amount=intent.amount,
                reversal_id=reversal.id,
                description=f"External provider reversal for top-up {intent.id}",
            )
        except InsufficientFundsError:
            wallet.is_active = False
            reversal.status = WalletTopUpReversalStatus.MANUAL_REVIEW
            reversal.last_error_code = "EXTERNAL_REVERSAL_INSUFFICIENT_FUNDS"
            reversal.last_error_detail = (
                "Provider reversed the top-up after some or all wallet value was spent; "
                "wallet frozen pending financial review."
            )
            reversal.next_attempt_at = None
            await session.flush()
            return reversal

        reversal.status = WalletTopUpReversalStatus.COMPLETED
        reversal.completed_at = datetime.now(UTC)
        reversal.last_error_code = None
        reversal.last_error_detail = None
        await self._record_topup_reversal_payment_transaction(
            session, intent, reversal, intent.provider_payment_id, origin="external_provider"
        )
        await session.flush()
        return reversal

    async def resolve_external_wallet_topup_reversal(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        reversal_id: uuid.UUID,
    ) -> WalletTopUpReversal:
        """Retry the local debit for a previously detected external reversal.

        This never calls the provider: the external reversal already happened upstream.
        It is intended for operator resolution after the wallet has enough restored value.
        """
        reversal = await session.get(WalletTopUpReversal, reversal_id)
        if reversal is None or reversal.tenant_id != tenant_id:
            raise PaymentIntentNotFoundError(f"Wallet top-up reversal {reversal_id} not found.")
        if (reversal.metadata_json or {}).get("origin") != "EXTERNAL_PROVIDER":
            raise PaymentError("Only externally-originated reversals use local manual resolution.")
        if reversal.status == WalletTopUpReversalStatus.COMPLETED:
            return reversal
        if reversal.status != WalletTopUpReversalStatus.MANUAL_REVIEW:
            raise PaymentError(f"External reversal is not awaiting manual review: {reversal.status.value}.")

        intent = await self.get_payment_intent(session, tenant_id, reversal.payment_intent_id)
        wallet = await session.get(Wallet, reversal.wallet_id)
        if wallet is None or wallet.tenant_id != tenant_id:
            raise PaymentIntegrityError("External reversal wallet is missing or cross-tenant.")
        await LedgerService.reserve_topup_reversal(
            session=session,
            wallet=wallet,
            amount=reversal.amount,
            reversal_id=reversal.id,
            description=f"Resolve external provider reversal for top-up {intent.id}",
        )
        reversal.status = WalletTopUpReversalStatus.COMPLETED
        reversal.completed_at = datetime.now(UTC)
        reversal.last_error_code = None
        reversal.last_error_detail = None
        await self._record_topup_reversal_payment_transaction(
            session,
            intent,
            reversal,
            reversal.provider_refund_id or reversal.original_provider_payment_id,
            origin="external_provider_manual_resolution",
        )

        other_review_stmt = select(WalletTopUpReversal.id).where(
            WalletTopUpReversal.wallet_id == wallet.id,
            WalletTopUpReversal.status == WalletTopUpReversalStatus.MANUAL_REVIEW,
            WalletTopUpReversal.id != reversal.id,
        )
        if (await session.execute(other_review_stmt)).scalars().first() is None:
            wallet.is_active = True

        event_stmt = select(PaymentReconciliationEvent).where(
            PaymentReconciliationEvent.tenant_id == tenant_id,
            PaymentReconciliationEvent.provider == reversal.provider,
            PaymentReconciliationEvent.provider_event_id == reversal.original_provider_payment_id,
            PaymentReconciliationEvent.requires_review.is_(True),
        )
        event = (await session.execute(event_stmt)).scalars().first()
        if event is not None:
            event.status = PaymentReconciliationEventStatus.PROCESSED
            event.requires_review = False
            event.classification = "EXTERNAL_REVERSAL_RESOLVED"
            event_metadata = dict(event.metadata_json or {})
            event_metadata["resolved_at"] = datetime.now(UTC).isoformat()
            event.metadata_json = event_metadata
        await session.flush()
        return reversal

    async def request_wallet_topup_reversal(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        intent_id: uuid.UUID,
        idempotency_key: str,
        reason: str | None = None,
        requested_by_user_id: uuid.UUID | None = None,
    ) -> WalletTopUpReversal:
        """Durably reserve wallet funds before scheduling a full upstream top-up refund.

        Telegram Stars only supports full refunds. Reserving the credited balance before
        the external call prevents the customer from spending funds while the refund saga
        is in-flight. The reservation and reversal record commit together in the API request.
        """
        if not idempotency_key:
            raise ValueError("idempotency_key is required for wallet top-up reversal.")
        intent = await self.get_payment_intent(session, tenant_id, intent_id)
        if intent.purpose != PaymentIntentPurpose.WALLET_TOPUP:
            raise PaymentError("Only settled wallet top-ups can use the top-up reversal flow.")
        if intent.status != PaymentIntentStatus.SUCCEEDED:
            raise PaymentError(f"Cannot reverse wallet top-up in status {intent.status.value}.")
        if not intent.provider_payment_id or intent.provider_payment_id.startswith("stars_invoice:"):
            raise PaymentIntegrityError("Wallet top-up has no settled provider payment identifier.")

        by_key_stmt = select(WalletTopUpReversal).where(
            WalletTopUpReversal.tenant_id == tenant_id,
            WalletTopUpReversal.idempotency_key == idempotency_key,
        )
        existing_by_key = (await session.execute(by_key_stmt)).scalar_one_or_none()
        if existing_by_key is not None:
            if existing_by_key.payment_intent_id != intent.id:
                raise PaymentIntegrityError(
                    f"Reversal idempotency key {idempotency_key} is bound to another payment intent."
                )
            return existing_by_key

        by_intent_stmt = select(WalletTopUpReversal).where(
            WalletTopUpReversal.tenant_id == tenant_id,
            WalletTopUpReversal.payment_intent_id == intent.id,
        )
        existing_by_intent = (await session.execute(by_intent_stmt)).scalar_one_or_none()
        if existing_by_intent is not None:
            return existing_by_intent

        provider = await self.registry.get_provider(
            session=session,
            tenant_id=tenant_id,
            provider_name=intent.provider,
            secret_storage=self.secret_storage,
        )
        if not provider.supports_refunds:
            raise UnsupportedProviderCapabilityError(
                f"Provider '{intent.provider}' does not support top-up refunds."
            )
        if not provider.supports_safe_refund_retries:
            raise UnsupportedProviderCapabilityError(
                f"Provider '{intent.provider}' cannot safely retry durable refund operations."
            )

        wallet = await LedgerService.get_or_create_wallet(
            session=session,
            tenant_id=tenant_id,
            user_id=intent.user_id,
            currency=intent.currency,
        )
        settlement_stmt = select(LedgerTransaction).where(
            LedgerTransaction.wallet_id == wallet.id,
            LedgerTransaction.reference_type == CANONICAL_SETTLEMENT_TYPE,
            LedgerTransaction.reference_id == str(intent.id),
        )
        settlement = (await session.execute(settlement_stmt)).scalars().first()
        if settlement is None or settlement.amount != intent.amount:
            raise PaymentIntegrityError("Wallet top-up settlement ledger entry is missing or inconsistent.")

        reversal = WalletTopUpReversal(
            tenant_id=tenant_id,
            payment_intent_id=intent.id,
            user_id=intent.user_id,
            wallet_id=wallet.id,
            provider=intent.provider,
            original_provider_payment_id=intent.provider_payment_id,
            amount=intent.amount,
            currency=intent.currency,
            idempotency_key=idempotency_key,
            status=WalletTopUpReversalStatus.REQUESTED,
            metadata_json={
                "reason": reason,
                "requested_by_user_id": str(requested_by_user_id) if requested_by_user_id else None,
            },
        )
        try:
            async with session.begin_nested():
                session.add(reversal)
                await session.flush()
        except IntegrityError as exc:
            concurrent = (await session.execute(by_intent_stmt)).scalar_one_or_none()
            if concurrent is not None:
                return concurrent
            raise PaymentIntegrityError("Concurrent top-up reversal request conflict.") from exc

        await LedgerService.reserve_topup_reversal(
            session=session,
            wallet=wallet,
            amount=intent.amount,
            reversal_id=reversal.id,
            description=reason or f"Reserve funds for top-up refund {intent.id}",
        )
        reversal.status = WalletTopUpReversalStatus.FUNDS_RESERVED
        reversal.next_attempt_at = datetime.now(UTC)
        await session.flush()
        return reversal

    async def process_wallet_topup_reversal(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        reversal_id: uuid.UUID,
    ) -> WalletTopUpReversal:
        """Perform a previously reserved upstream refund and finalize the saga."""
        reversal = await session.get(WalletTopUpReversal, reversal_id)
        if reversal is None or reversal.tenant_id != tenant_id:
            raise PaymentIntentNotFoundError(f"Wallet top-up reversal {reversal_id} not found.")
        if reversal.status == WalletTopUpReversalStatus.COMPLETED:
            return reversal
        if reversal.status != WalletTopUpReversalStatus.PROCESSING:
            raise PaymentError(
                f"Wallet top-up reversal must be claimed before processing; status={reversal.status.value}."
            )

        intent = await self.get_payment_intent(session, tenant_id, reversal.payment_intent_id)
        user = await session.get(User, reversal.user_id)
        if user is None:
            raise PaymentIntegrityError("Wallet top-up reversal user no longer exists.")

        provider = await self.registry.get_provider(
            session=session,
            tenant_id=tenant_id,
            provider_name=reversal.provider,
            secret_storage=self.secret_storage,
        )
        refund_result = await provider.refund(
            PaymentRefundRequest(
                provider_payment_id=reversal.original_provider_payment_id,
                amount=reversal.amount,
                currency=reversal.currency,
                idempotency_key=reversal.idempotency_key,
                reason=(reversal.metadata_json or {}).get("reason"),
                metadata={
                    "telegram_user_id": user.telegram_id,
                    "payment_intent_id": str(intent.id),
                    "reversal_id": str(reversal.id),
                },
            )
        )
        if not refund_result.is_success:
            raise PaymentProviderError("Payment provider did not confirm the top-up refund.")

        reversal.provider_refund_id = refund_result.provider_refund_id
        reversal.status = WalletTopUpReversalStatus.COMPLETED
        reversal.completed_at = datetime.now(UTC)
        reversal.next_attempt_at = None
        reversal.last_error_code = None
        reversal.last_error_detail = None

        await self._record_topup_reversal_payment_transaction(
            session,
            intent,
            reversal,
            refund_result.provider_refund_id,
            origin="merchant_request",
        )
        await session.flush()
        return reversal

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

        if intent.purpose == PaymentIntentPurpose.WALLET_TOPUP:
            raise PaymentError(
                "Wallet top-up refunds require a dedicated wallet reversal flow and cannot use order payment refunds."
            )

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
