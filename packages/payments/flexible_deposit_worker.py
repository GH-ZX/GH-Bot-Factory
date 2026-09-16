from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.config import settings
from packages.core.database import async_session_factory
from packages.payments.flexible_deposits import FlexibleDepositService

logger = logging.getLogger("payments.flexible-deposit-worker")


class FlexibleDepositReconciliationWorker:
    """Poll open-amount deposits so laptop-first installs do not require public webhooks."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        service: FlexibleDepositService | None = None,
        interval_seconds: float | None = None,
        batch_size: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.session_factory = session_factory or async_session_factory
        self.service = service or FlexibleDepositService()
        self.interval_seconds = float(
            interval_seconds if interval_seconds is not None else settings.payment_provider_reconcile_interval_seconds
        )
        self.batch_size = int(
            batch_size if batch_size is not None else settings.payment_provider_reconcile_batch_size
        )
        self.enabled = bool(enabled) if enabled is not None else settings.payment_provider_reconcile_enabled
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="flexible-deposit-reconciliation")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with self.session_factory() as session:
                    deposits = await self.service.reconcile_open(session, limit=self.batch_size)
                    await session.commit()
                if deposits:
                    logger.info("Flexible deposit reconciliation scanned=%d", len(deposits))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Flexible deposit reconciliation iteration failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
