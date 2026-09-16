from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.config import settings
from packages.core.database import async_session_factory
from packages.payments.reconciliation import PaymentReconciliationService
from packages.payments.state_machine import PaymentIntentStatus

logger = logging.getLogger("payments.provider-reconciliation")


class PaymentProviderReconciliationWorker:
    """Pull-reconcile provider-backed payments without requiring public ingress.

    Webhooks remain an acceleration path, never the sole convergence mechanism. The worker
    delegates all financial mutation to PaymentReconciliationService / PaymentService so
    provider polling and webhooks converge on the same exactly-once ledger settlement gate.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        reconciliation_service: PaymentReconciliationService | None = None,
        interval_seconds: float | None = None,
        batch_size: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.session_factory = session_factory or async_session_factory
        self.reconciliation_service = reconciliation_service or PaymentReconciliationService()
        self.interval_seconds = float(
            interval_seconds
            if interval_seconds is not None
            else settings.payment_provider_reconcile_interval_seconds
        )
        self.batch_size = int(
            batch_size
            if batch_size is not None
            else settings.payment_provider_reconcile_batch_size
        )
        self.enabled = (
            bool(enabled) if enabled is not None else settings.payment_provider_reconcile_enabled
        )
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(), name="payment-provider-reconciliation"
        )

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
                        "Payment provider reconciliation scanned=%d succeeded=%d processing=%d unknown=%d terminal=%d",
                        stats["scanned"],
                        stats["succeeded"],
                        stats["processing"],
                        stats["unknown"],
                        stats["terminal"],
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Payment provider reconciliation iteration failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass

    async def run_once(self, session: AsyncSession) -> dict[str, int]:
        intents = await self.reconciliation_service.reconcile_pending_intents(
            session,
            tenant_id=None,
            limit=self.batch_size,
            exclude_providers={"telegram_stars"},
        )
        stats = {
            "scanned": len(intents),
            "succeeded": 0,
            "processing": 0,
            "unknown": 0,
            "terminal": 0,
        }
        for intent in intents:
            if intent.status == PaymentIntentStatus.SUCCEEDED:
                stats["succeeded"] += 1
            elif intent.status in {PaymentIntentStatus.PENDING, PaymentIntentStatus.PROCESSING}:
                stats["processing"] += 1
            elif intent.status == PaymentIntentStatus.UNKNOWN:
                stats["unknown"] += 1
            else:
                stats["terminal"] += 1
        await session.flush()
        return stats
