from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.config import settings
from packages.core.database import async_session_factory
from packages.saas.billing import BillingProviderError, get_billing_provider
from packages.saas.billing_service import BillingReconcileSummary, reconcile_provider_subscriptions
from packages.saas.control_plane import append_platform_audit

logger = logging.getLogger("saas.billing_reconciliation")


class SaaSBillingReconciliationWorker:
    """Periodic provider pull reconciliation for laptop/VPS deployments.

    This worker does not require inbound webhook connectivity. If hosted billing is disabled,
    it remains inert. When enabled, it performs the same normalized convergence as the manual
    platform reconciliation command and signed webhook path.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        poll_interval_seconds: float | None = None,
        batch_size: int | None = None,
    ) -> None:
        self.session_factory = session_factory or async_session_factory
        self.poll_interval_seconds = float(
            poll_interval_seconds or settings.billing_reconcile_interval_seconds
        )
        self.batch_size = int(batch_size or settings.billing_reconcile_batch_size)
        self._task: asyncio.Task | None = None
        self._is_running = False

    @property
    def enabled(self) -> bool:
        return bool(
            settings.billing_reconcile_enabled
            and settings.billing_provider.strip().lower() != "disabled"
        )

    async def poll_once(self) -> BillingReconcileSummary | None:
        if not self.enabled:
            return None
        adapter = get_billing_provider()
        async with self.session_factory() as session:
            try:
                summary = await reconcile_provider_subscriptions(
                    session,
                    adapter=adapter,
                    limit=self.batch_size,
                )
                await append_platform_audit(
                    session,
                    action="BILLING_RECONCILIATION_RUN",
                    resource_type="BILLING_PROVIDER",
                    resource_id=summary.provider,
                    actor="SYSTEM_BILLING_RECONCILER",
                    details={
                        "scanned": summary.scanned,
                        "applied": summary.applied,
                        "duplicates": summary.duplicates,
                        "failed": summary.failed,
                        "mode": "PERIODIC_PULL",
                    },
                )
                await session.commit()
                return summary
            except Exception:
                await session.rollback()
                raise

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Hosted billing reconciliation worker disabled")
            return
        self._is_running = True
        self._task = asyncio.create_task(self._run_loop(), name="saas-billing-reconciliation-worker")

    async def stop(self) -> None:
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        while self._is_running:
            try:
                summary = await self.poll_once()
                if summary is not None:
                    if summary.failed:
                        logger.warning(
                            "SaaS billing reconciliation provider=%s scanned=%d failed=%d",
                            summary.provider,
                            summary.scanned,
                            summary.failed,
                        )
                    else:
                        logger.info(
                            "SaaS billing reconciliation provider=%s scanned=%d applied=%d duplicates=%d",
                            summary.provider,
                            summary.scanned,
                            summary.applied,
                            summary.duplicates,
                        )
            except asyncio.CancelledError:
                break
            except BillingProviderError as exc:
                logger.warning("SaaS billing provider reconciliation unavailable: %s", exc)
            except Exception:
                logger.exception("SaaS billing reconciliation poll failed")
            await asyncio.sleep(self.poll_interval_seconds)
