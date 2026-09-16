from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.config import settings
from packages.core.database import async_session_factory
from packages.payments.exceptions import (
    OnChainVerificationPending,
    PaymentError,
    PaymentIntegrityError,
)
from packages.payments.models import (
    PaymentMethodConfig,
    PaymentObservation,
    PaymentObservationSource,
    PaymentObservationStatus,
)
from packages.payments.platform import (
    OnChainVerifierRegistry,
    PaymentPlatformService,
    default_onchain_verifier_registry,
)

logger = logging.getLogger("payments.onchain-reconciliation")


class OnChainPaymentReconciliationWorker:
    """Pull-reconcile pending on-chain evidence without requiring public ingress.

    The worker only runs when at least one trusted verifier is application-registered.
    Tenant/customer data can never cause dynamic code loading or arbitrary RPC selection.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        registry: OnChainVerifierRegistry | None = None,
        interval_seconds: float | None = None,
        batch_size: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.session_factory = session_factory or async_session_factory
        self.registry = registry or default_onchain_verifier_registry
        self.interval_seconds = float(
            interval_seconds
            if interval_seconds is not None
            else settings.payment_onchain_reconcile_interval_seconds
        )
        self.batch_size = int(
            batch_size if batch_size is not None else settings.payment_onchain_reconcile_batch_size
        )
        requested_enabled = (
            bool(enabled) if enabled is not None else settings.payment_onchain_reconcile_enabled
        )
        self.enabled = requested_enabled and bool(self.registry.registered_networks())
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="payment-onchain-reconciliation")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with self.session_factory() as session:
                    stats = await self.run_once(session)
                    await session.commit()
                if stats["scanned"]:
                    logger.info(
                        "On-chain payment reconciliation scanned=%d verified=%d review=%d pending=%d errors=%d",
                        stats["scanned"],
                        stats["verified"],
                        stats["review"],
                        stats["pending"],
                        stats["errors"],
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("On-chain payment reconciliation iteration failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass

    async def run_once(self, session: AsyncSession) -> dict[str, int]:
        stats = {"scanned": 0, "verified": 0, "review": 0, "pending": 0, "errors": 0}
        if not self.registry.registered_networks():
            return stats

        stmt = (
            select(PaymentObservation)
            .join(
                PaymentMethodConfig,
                PaymentMethodConfig.id == PaymentObservation.payment_method_id,
            )
            .where(
                PaymentObservation.source == PaymentObservationSource.ONCHAIN,
                PaymentObservation.status == PaymentObservationStatus.PENDING_VERIFICATION,
                PaymentMethodConfig.is_enabled.is_(True),
                PaymentMethodConfig.network.in_(self.registry.registered_networks()),
            )
            .order_by(PaymentObservation.created_at.asc(), PaymentObservation.id.asc())
            .limit(self.batch_size)
        )
        observations = list((await session.execute(stmt)).scalars().all())
        service = PaymentPlatformService(onchain_registry=self.registry)
        for observation in observations:
            stats["scanned"] += 1
            try:
                updated = await service.verify_onchain_observation(
                    session,
                    tenant_id=observation.tenant_id,
                    observation_id=observation.id,
                )
                if updated.status == PaymentObservationStatus.VERIFIED:
                    stats["verified"] += 1
                elif updated.status == PaymentObservationStatus.MANUAL_REVIEW:
                    stats["review"] += 1
                else:
                    stats["pending"] += 1
            except OnChainVerificationPending:
                stats["pending"] += 1
            except (PaymentError, PaymentIntegrityError) as exc:
                # Verification failures do not become payment failure automatically; they are
                # operational evidence and must not trigger refund/credit side effects.
                logger.warning(
                    "On-chain verification failed observation=%s tenant=%s: %s",
                    observation.id,
                    observation.tenant_id,
                    exc,
                )
                stats["errors"] += 1
        await session.flush()
        return stats
