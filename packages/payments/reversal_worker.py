import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.database import async_session_factory
from packages.payments.exceptions import PaymentError, PaymentIntegrityError, PaymentProviderError
from packages.payments.models import WalletTopUpReversal, WalletTopUpReversalStatus
from packages.payments.payment_service import PaymentService
from packages.payments.resolution import FinancialResolutionService

logger = logging.getLogger("payments.reversal_worker")


class WalletTopUpReversalWorker:
    """Durable saga worker for provider refunds whose wallet funds are already reserved."""

    def __init__(
        self,
        payment_service: PaymentService | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        poll_interval_seconds: float = 1.0,
        max_backoff_seconds: int = 3600,
        resolution_service: FinancialResolutionService | None = None,
    ) -> None:
        self.payment_service = payment_service or PaymentService()
        self.resolution_service = resolution_service or FinancialResolutionService()
        self.session_factory = session_factory or async_session_factory
        self.poll_interval_seconds = poll_interval_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._task: asyncio.Task | None = None
        self._is_running = False

    @staticmethod
    async def claim_reversal(session: AsyncSession, reversal_id: uuid.UUID) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(WalletTopUpReversal)
            .where(
                WalletTopUpReversal.id == reversal_id,
                WalletTopUpReversal.status.in_(
                    [
                        WalletTopUpReversalStatus.FUNDS_RESERVED,
                        WalletTopUpReversalStatus.RECONCILIATION_REQUIRED,
                    ]
                ),
                or_(
                    WalletTopUpReversal.next_attempt_at.is_(None),
                    WalletTopUpReversal.next_attempt_at <= now,
                ),
            )
            .values(
                status=WalletTopUpReversalStatus.PROCESSING,
                attempts=WalletTopUpReversal.attempts + 1,
                last_attempt_at=now,
                next_attempt_at=None,
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        return bool(result.rowcount and result.rowcount > 0)

    async def recover_processing(self) -> int:
        """Move crash-abandoned PROCESSING records into retryable reconciliation state."""
        async with self.session_factory() as session:
            now = datetime.now(UTC)
            stmt = (
                update(WalletTopUpReversal)
                .where(WalletTopUpReversal.status == WalletTopUpReversalStatus.PROCESSING)
                .values(
                    status=WalletTopUpReversalStatus.RECONCILIATION_REQUIRED,
                    next_attempt_at=now,
                    last_error_code="WORKER_RECOVERY",
                    last_error_detail="Recovered an interrupted provider refund attempt.",
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)

    async def poll_once(self, limit: int = 50) -> int:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            stmt = (
                select(WalletTopUpReversal.id)
                .where(
                    WalletTopUpReversal.status.in_(
                        [
                            WalletTopUpReversalStatus.FUNDS_RESERVED,
                            WalletTopUpReversalStatus.RECONCILIATION_REQUIRED,
                        ]
                    ),
                    or_(
                        WalletTopUpReversal.next_attempt_at.is_(None),
                        WalletTopUpReversal.next_attempt_at <= now,
                    ),
                )
                .order_by(WalletTopUpReversal.created_at.asc())
                .limit(limit)
            )
            reversal_ids = list((await session.execute(stmt)).scalars().all())

        processed = 0
        for reversal_id in reversal_ids:
            async with self.session_factory() as claim_session:
                if not await self.claim_reversal(claim_session, reversal_id):
                    continue
            await self._process_claimed(reversal_id)
            processed += 1
        return processed

    async def _process_claimed(self, reversal_id: uuid.UUID) -> None:
        async with self.session_factory() as session:
            reversal = await session.get(WalletTopUpReversal, reversal_id)
            if reversal is None:
                return
            try:
                await self.payment_service.process_wallet_topup_reversal(
                    session=session,
                    tenant_id=reversal.tenant_id,
                    reversal_id=reversal.id,
                )
                await session.commit()
                logger.info("Completed wallet top-up reversal id=%s", reversal_id)
            except (PaymentProviderError, TimeoutError, OSError) as exc:
                await session.rollback()
                await self._mark_retry(reversal_id, type(exc).__name__)
            except (PaymentIntegrityError, PaymentError, ValueError) as exc:
                await session.rollback()
                await self._mark_manual_review(reversal_id, type(exc).__name__, str(exc))
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                await self._mark_retry(reversal_id, type(exc).__name__)

    async def _mark_retry(self, reversal_id: uuid.UUID, error_code: str) -> None:
        async with self.session_factory() as session:
            reversal = await session.get(WalletTopUpReversal, reversal_id)
            if reversal is None or reversal.status == WalletTopUpReversalStatus.COMPLETED:
                return
            delay = min(self.max_backoff_seconds, 2 ** max(0, min(reversal.attempts, 12)))
            reversal.status = WalletTopUpReversalStatus.RECONCILIATION_REQUIRED
            reversal.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
            reversal.last_error_code = error_code[:100]
            reversal.last_error_detail = "Provider refund outcome is uncertain; safe retry scheduled."
            await session.commit()
            logger.warning(
                "Wallet top-up reversal id=%s requires reconciliation; retry in %ss",
                reversal_id,
                delay,
            )

    async def _mark_manual_review(
        self,
        reversal_id: uuid.UUID,
        error_code: str,
        detail: str,
    ) -> None:
        async with self.session_factory() as session:
            reversal = await session.get(WalletTopUpReversal, reversal_id)
            if reversal is None or reversal.status == WalletTopUpReversalStatus.COMPLETED:
                return
            reversal.status = WalletTopUpReversalStatus.MANUAL_REVIEW
            reversal.next_attempt_at = None
            reversal.last_error_code = error_code[:100]
            reversal.last_error_detail = detail[:255]
            await self.resolution_service.ensure_case_for_reversal(session, reversal)
            await session.commit()
            logger.error("Wallet top-up reversal id=%s moved to manual review", reversal_id)

    async def start(self) -> None:
        self._is_running = True
        recovered = await self.recover_processing()
        if recovered:
            logger.warning("Recovered %d interrupted wallet top-up reversal(s)", recovered)
        self._task = asyncio.create_task(self._run_loop(), name="topup-reversal-worker")

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
                processed = await self.poll_once()
                if processed == 0:
                    await asyncio.sleep(self.poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception("Wallet top-up reversal worker poll failed")
                await asyncio.sleep(self.poll_interval_seconds)
