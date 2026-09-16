import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.payments.exceptions import (
    PaymentError,
    PaymentIntegrityError,
    PaymentProviderError,
    UnsupportedProviderCapabilityError,
)
from packages.payments.models import PaymentIntent
from packages.payments.payment_service import PaymentService
from packages.payments.providers.interface import PaymentCreateRequest, PaymentDetailsResult
from packages.payments.state_machine import PaymentIntentStatus

logger = logging.getLogger("payments.reconciliation")


class PaymentReconciliationService:
    """Reconciles uncertain local payment intents against authoritative provider state."""

    def __init__(self, payment_service: PaymentService | None = None) -> None:
        self.payment_service = payment_service or PaymentService()

    @staticmethod
    def _has_creation_ambiguity(intent: PaymentIntent) -> bool:
        marker = (intent.metadata_json or {}).get("_provider_creation_ambiguity")
        return isinstance(marker, dict) and marker.get("reason") == "transport_outcome_unknown"

    @staticmethod
    def _recovery_request(intent: PaymentIntent) -> PaymentCreateRequest:
        metadata = {
            "purpose": intent.purpose.value,
            "payment_intent_id": str(intent.id),
            "tenant_id": str(intent.tenant_id),
            "user_id": str(intent.user_id),
        }
        if intent.payment_method_id is not None:
            metadata["payment_method_id"] = str(intent.payment_method_id)
        return PaymentCreateRequest(
            order_id=intent.order_id,
            amount=intent.amount,
            currency=intent.currency,
            idempotency_key=intent.idempotency_key,
            metadata=metadata,
            return_url=None,
        )

    async def _apply_details(
        self,
        session: AsyncSession,
        intent: PaymentIntent,
        details: PaymentDetailsResult,
        *,
        allow_store_initial_verification: bool = False,
    ) -> PaymentIntent:
        if details.provider_payment_id != intent.provider_payment_id:
            if intent.provider_payment_id is not None:
                raise PaymentIntegrityError("Provider reconciliation changed payment identity.")
            intent.provider_payment_id = details.provider_payment_id

        if details.amount != intent.amount or details.currency != intent.currency:
            raise PaymentIntegrityError(
                "Provider reconciliation changed authoritative payment amount/currency."
            )

        expected = (intent.metadata_json or {}).get("_provider_verification")
        if expected:
            self.payment_service.assert_provider_verification_attributes(
                intent, details.verification_attributes
            )
        elif allow_store_initial_verification:
            self.payment_service._store_provider_verification_attributes(
                intent, details.verification_attributes
            )

        if details.status == PaymentIntentStatus.SUCCEEDED:
            await self.payment_service.settle_payment_intent(
                session=session,
                tenant_id=intent.tenant_id,
                intent_id=intent.id,
                verified_amount=details.amount,
                verified_currency=details.currency,
            )
        elif (
            details.status
            in (
                PaymentIntentStatus.FAILED,
                PaymentIntentStatus.CANCELLED,
                PaymentIntentStatus.EXPIRED,
            )
            and intent.status != details.status
        ):
            intent.transition_to(details.status)
        elif (
            details.status == PaymentIntentStatus.PROCESSING
            and intent.status != PaymentIntentStatus.PROCESSING
        ):
            intent.transition_to(PaymentIntentStatus.PROCESSING)
        # PENDING is deliberately not used to downgrade UNKNOWN creation ambiguity. Once
        # we know a provider order exists but lost its checkout response, UNKNOWN remains
        # the safest customer-facing state until later polling reaches a decisive state.

        await session.flush()
        return intent

    async def _recover_creation_ambiguity(
        self,
        session: AsyncSession,
        intent: PaymentIntent,
    ) -> PaymentIntent:
        provider = await self.payment_service.registry.get_provider(
            session=session,
            tenant_id=intent.tenant_id,
            provider_name=intent.provider,
            secret_storage=self.payment_service.secret_storage,
        )
        if not getattr(provider, "supports_creation_recovery", False):
            logger.warning(
                "Provider %s cannot safely recover ambiguous creation for intent %s; leaving UNKNOWN.",
                intent.provider,
                intent.id,
            )
            return intent
        recover = getattr(provider, "recover_payment_creation", None)
        if recover is None:
            raise UnsupportedProviderCapabilityError(
                f"Provider {intent.provider} advertises creation recovery without implementing it."
            )
        try:
            details = await recover(self._recovery_request(intent))
        except (TimeoutError, PaymentProviderError) as exc:
            logger.warning(
                "Creation recovery for intent %s via %s is still unresolved: %s",
                intent.id,
                intent.provider,
                exc,
            )
            return intent

        if not details.provider_payment_id:
            raise PaymentIntegrityError("Provider creation recovery returned an empty payment identity.")

        metadata = dict(intent.metadata_json or {})
        ambiguity = metadata.pop("_provider_creation_ambiguity", None)
        metadata["_provider_creation_recovery"] = {
            "recovered_at": datetime.now(UTC).isoformat(),
            "provider": intent.provider,
            "original_ambiguity": ambiguity,
        }
        intent.metadata_json = metadata
        return await self._apply_details(
            session,
            intent,
            details,
            allow_store_initial_verification=True,
        )

    async def reconcile_intent(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        intent_id: uuid.UUID,
    ) -> PaymentIntent:
        """Query upstream state and synchronize a provider-backed payment intent."""
        intent = await self.payment_service.get_payment_intent(session, tenant_id, intent_id)

        if intent.status == PaymentIntentStatus.SUCCEEDED:
            return intent

        if not intent.provider_payment_id:
            if intent.status == PaymentIntentStatus.UNKNOWN and self._has_creation_ambiguity(intent):
                return await self._recover_creation_ambiguity(session, intent)
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

        return await self._apply_details(session, intent, details)

    async def reconcile_pending_intents(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID | None = None,
        limit: int = 50,
        exclude_providers: set[str] | None = None,
    ) -> list[PaymentIntent]:
        """Scan provider-backed open intents plus recoverable ambiguous creations."""
        limit = max(1, min(int(limit), 200))
        provider_stmt = (
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
            provider_stmt = provider_stmt.where(PaymentIntent.tenant_id == tenant_id)
        normalized_exclusions = {name.strip().lower() for name in (exclude_providers or set()) if name.strip()}
        if normalized_exclusions:
            provider_stmt = provider_stmt.where(PaymentIntent.provider.notin_(normalized_exclusions))
        provider_stmt = provider_stmt.order_by(PaymentIntent.updated_at.asc(), PaymentIntent.id.asc())
        intents = list((await session.execute(provider_stmt)).scalars().all())

        remaining = limit - len(intents)
        if remaining > 0:
            # JSON marker filtering is kept in Python for SQLite/PostgreSQL portability.
            ambiguous_stmt = (
                select(PaymentIntent)
                .where(
                    PaymentIntent.status == PaymentIntentStatus.UNKNOWN,
                    PaymentIntent.provider_payment_id.is_(None),
                )
                .limit(min(remaining * 4, 200))
            )
            if tenant_id is not None:
                ambiguous_stmt = ambiguous_stmt.where(PaymentIntent.tenant_id == tenant_id)
            if normalized_exclusions:
                ambiguous_stmt = ambiguous_stmt.where(PaymentIntent.provider.notin_(normalized_exclusions))
            ambiguous_stmt = ambiguous_stmt.order_by(PaymentIntent.updated_at.asc(), PaymentIntent.id.asc())
            candidates = (await session.execute(ambiguous_stmt)).scalars().all()
            intents.extend(
                intent for intent in candidates if self._has_creation_ambiguity(intent)
            )
            intents = intents[:limit]

        reconciled: list[PaymentIntent] = []
        for intent in intents:
            try:
                rec = await self.reconcile_intent(session, intent.tenant_id, intent.id)
                reconciled.append(rec)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to reconcile intent %s: %s", intent.id, exc)

        return reconciled
