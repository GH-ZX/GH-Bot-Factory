import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.payments.exceptions import (
    PaymentError,
    PaymentProviderError,
    UnsupportedProviderCapabilityError,
)
from packages.payments.models import PaymentIntent
from packages.payments.payment_service import PaymentService
from packages.payments.state_machine import PaymentIntentStatus

logger = logging.getLogger("payments.reconciliation")


class PaymentReconciliationService:
    """Reconciles uncertain local payment intents (PENDING, PROCESSING, UNKNOWN) against authoritative provider state."""

    def __init__(self, payment_service: PaymentService | None = None) -> None:
        self.payment_service = payment_service or PaymentService()

    async def reconcile_intent(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        intent_id: uuid.UUID,
    ) -> PaymentIntent:
        """Queries the upstream gateway for an uncertain payment intent and synchronizes state and settlement."""
        intent = await self.payment_service.get_payment_intent(session, tenant_id, intent_id)

        # Terminal succeeded intents need no reconciliation
        if intent.status == PaymentIntentStatus.SUCCEEDED:
            return intent

        if not intent.provider_payment_id:
            raise PaymentError(f"Payment intent {intent_id} has no provider_payment_id to reconcile.")

        provider = await self.payment_service.registry.get_provider(
            session=session,
            tenant_id=tenant_id,
            provider_name=intent.provider,
            secret_storage=self.payment_service.secret_storage,
        )

        if not provider.supports_payment_lookup:
            raise UnsupportedProviderCapabilityError(
                f"Provider {intent.provider} does not support payment lookup."
            )

        try:
            details = await provider.get_payment(intent.provider_payment_id)
        except TimeoutError:
            logger.warning(
                "Timeout querying provider %s for intent %s. Marking state UNKNOWN.",
                intent.provider,
                intent.id,
            )
            if intent.status != PaymentIntentStatus.UNKNOWN:
                intent.transition_to(PaymentIntentStatus.UNKNOWN)
            await session.flush()
            return intent
        except PaymentProviderError as exc:
            logger.error(
                "Provider error during reconciliation for intent %s: %s",
                intent.id,
                exc,
            )
            if intent.status != PaymentIntentStatus.UNKNOWN:
                intent.transition_to(PaymentIntentStatus.UNKNOWN)
            await session.flush()
            return intent

        # Synchronize local state based on provider status
        if details.status == PaymentIntentStatus.SUCCEEDED:
            await self.payment_service.settle_payment_intent(
                session=session,
                tenant_id=tenant_id,
                intent_id=intent.id,
                verified_amount=details.amount,
                verified_currency=details.currency,
            )
        elif (
            details.status in (
                PaymentIntentStatus.FAILED,
                PaymentIntentStatus.CANCELLED,
                PaymentIntentStatus.EXPIRED,
            )
            and intent.status != details.status
        ):
            intent.transition_to(details.status)
        elif details.status == PaymentIntentStatus.PROCESSING and intent.status != PaymentIntentStatus.PROCESSING:
            intent.transition_to(PaymentIntentStatus.PROCESSING)

        await session.flush()
        return intent

    async def reconcile_pending_intents(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[PaymentIntent]:
        """Scans and reconciles open/uncertain intents across tenants or for a specific tenant."""
        stmt = (
            select(PaymentIntent)
            .where(
                PaymentIntent.status.in_(
                    [
                        PaymentIntentStatus.PENDING,
                        PaymentIntentStatus.PROCESSING,
                        PaymentIntentStatus.UNKNOWN,
                    ]
                ),
                PaymentIntent.provider_payment_id.isnot(None),
            )
            .limit(limit)
        )
        if tenant_id is not None:
            stmt = stmt.where(PaymentIntent.tenant_id == tenant_id)

        intents = (await session.execute(stmt)).scalars().all()
        reconciled = []
        for intent in intents:
            try:
                rec = await self.reconcile_intent(session, intent.tenant_id, intent.id)
                reconciled.append(rec)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to reconcile intent %s: %s", intent.id, exc)

        return reconciled
