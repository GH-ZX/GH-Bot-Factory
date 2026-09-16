from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.config import settings
from packages.core.database import async_session_factory
from packages.fulfillment.reconciliation import ReconciliationService

logger = logging.getLogger("fulfillment.reconciliation_worker")


class ProviderReconciliationWorker:
    """Periodic pull reconciliation for dispatched provider orders.

    This is intentionally ingress-independent so laptop/self-hosted deployments converge even
    when providers cannot deliver webhooks to the installation.
    """

    def __init__(
        self,
        *,
        service: ReconciliationService | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        enabled: bool | None = None,
        interval_seconds: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        self.service = service or ReconciliationService()
        self.session_factory = session_factory or async_session_factory
        self.enabled = (
            settings.provider_reconcile_enabled if enabled is None else bool(enabled)
        )
        self.interval_seconds = max(
            15,
            min(
                int(
                    settings.provider_reconcile_interval_seconds
                    if interval_seconds is None
                    else interval_seconds
                ),
                3600,
            ),
        )
        self.batch_size = max(
            1,
            min(
                int(
                    settings.provider_reconcile_batch_size
                    if batch_size is None
                    else batch_size
                ),
                1000,
            ),
        )
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def run_once(self) -> int:
        if not self.enabled:
            return 0
        async with self.session_factory() as session:
            discrepancies = await self.service.reconcile_active_attempts(
                session,
                limit=self.batch_size,
            )
        if discrepancies:
            logger.info(
                "Provider reconciliation processed %d discrepancy/state observation(s)",
                len(discrepancies),
            )
        return len(discrepancies)

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Provider reconciliation cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue

    async def start(self) -> None:
        if not self.enabled or (self._task is not None and not self._task.done()):
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="provider-reconciliation-worker",
        )
        logger.info(
            "Provider reconciliation worker started (interval=%ss, batch=%s)",
            self.interval_seconds,
            self.batch_size,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
