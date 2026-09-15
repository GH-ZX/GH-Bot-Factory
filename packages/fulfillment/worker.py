import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.commerce.models import Order
from packages.core.database import async_session_factory
from packages.fulfillment.models import (
    FulfillmentAttempt,
    FulfillmentJobRecord,
    FulfillmentJobStatus,
)
from packages.fulfillment.service import FulfillmentService
from packages.providers.exceptions import ProviderError

logger = logging.getLogger("fulfillment.worker")


@dataclass
class FulfillmentJob:
    order_id: uuid.UUID
    recipient: str
    attempt_number: int = 1
    metadata: dict[str, Any] | None = None
    job_id: uuid.UUID | None = None


class FulfillmentWorker:
    """Asynchronous background worker executing resilient order fulfillment with exponential backoff."""

    def __init__(
        self,
        fulfillment_service: FulfillmentService | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        max_retries: int = 3,
        base_backoff_seconds: float = 0.5,
    ) -> None:
        self.service = fulfillment_service or FulfillmentService()
        self.session_factory = session_factory or async_session_factory
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self.queue: asyncio.Queue[FulfillmentJob] = asyncio.Queue()
        self.dead_letter_queue: list[FulfillmentJob] = []
        self._queued_job_ids: set[uuid.UUID] = set()
        self._worker_task: asyncio.Task | None = None
        self._is_running = False

    @classmethod
    async def claim_job(cls, session: AsyncSession, job_record_id: uuid.UUID) -> bool:
        """Atomically claim a queued job record using conditional UPDATE.

        UPDATE fulfillment_jobs SET status='RUNNING', locked_at=:now WHERE id=:id AND status='QUEUED'
        Returns True if claimed by this caller (1 row updated), False if already claimed or not queued (0 rows).
        """
        stmt = (
            update(FulfillmentJobRecord)
            .where(
                FulfillmentJobRecord.id == job_record_id,
                FulfillmentJobRecord.status == FulfillmentJobStatus.QUEUED,
            )
            .values(
                status=FulfillmentJobStatus.RUNNING,
                locked_at=datetime.now(UTC),
            )
        )
        res = await session.execute(stmt)
        await session.commit()
        return bool(res.rowcount and res.rowcount > 0)

    async def enqueue(
        self,
        order_id: uuid.UUID,
        recipient: str,
        attempt_number: int = 1,
        metadata: dict[str, Any] | None = None,
        persist_db: bool = True,
        job_id: uuid.UUID | None = None,
    ) -> FulfillmentJob:
        """Persist a durable job first, then schedule its in-memory wake-up."""
        if persist_db:
            try:
                async with self.session_factory() as session:
                    order = await session.get(Order, order_id)
                    if order is None:
                        raise ValueError(f"Order {order_id} does not exist.")
                    job = await self.stage_durable_job(
                        session=session,
                        order=order,
                        recipient=recipient,
                        attempt_number=attempt_number,
                        metadata=metadata,
                        job_id=job_id,
                    )
                    await session.commit()
            except Exception as exc:
                logger.error(
                    "Failed to persist durable FulfillmentJobRecord for order %s. "
                    "Aborting enqueue to fail closed.",
                    order_id,
                )
                raise RuntimeError(
                    f"Durable enqueue failed for order {order_id}: database persistence failed"
                ) from exc
        else:
            job = FulfillmentJob(
                order_id=order_id,
                recipient=recipient,
                attempt_number=attempt_number,
                metadata=metadata,
                job_id=job_id,
            )

        await self.enqueue_persisted_job(job)
        logger.info("Enqueued fulfillment job for order %s (attempt=%d)", order_id, attempt_number)
        return job

    @staticmethod
    async def stage_durable_job(
        session: AsyncSession,
        order: Order,
        recipient: str,
        attempt_number: int = 1,
        metadata: dict[str, Any] | None = None,
        job_id: uuid.UUID | None = None,
    ) -> FulfillmentJob:
        """Stage a durable job inside the caller's existing database transaction."""
        job_id = job_id or uuid.uuid4()
        job = FulfillmentJob(
            order_id=order.id,
            recipient=recipient,
            attempt_number=attempt_number,
            metadata=metadata,
            job_id=job_id,
        )
        session.add(
            FulfillmentJobRecord(
                id=job_id,
                tenant_id=order.tenant_id,
                order_id=order.id,
                recipient=recipient,
                attempt_number=attempt_number,
                status=FulfillmentJobStatus.QUEUED,
                payload=metadata or {},
            )
        )
        await session.flush()
        return job

    async def enqueue_persisted_job(self, job: FulfillmentJob) -> bool:
        """Schedule a previously persisted job without creating another database record."""
        if job.job_id is not None:
            if job.job_id in self._queued_job_ids:
                return False
            self._queued_job_ids.add(job.job_id)
        await self.queue.put(job)
        return True

    async def poll_queued_jobs(self, session: AsyncSession, limit: int = 100) -> int:
        """Load durable QUEUED jobs that are not already scheduled in this process."""
        stmt = (
            select(FulfillmentJobRecord)
            .where(FulfillmentJobRecord.status == FulfillmentJobStatus.QUEUED)
            .order_by(FulfillmentJobRecord.created_at.asc())
            .limit(limit)
        )
        records = list((await session.execute(stmt)).scalars().all())
        scheduled = 0
        for rec in records:
            job = FulfillmentJob(
                order_id=rec.order_id,
                recipient=rec.recipient,
                attempt_number=rec.attempt_number,
                metadata=rec.payload,
                job_id=rec.id,
            )
            if await self.enqueue_persisted_job(job):
                scheduled += 1
        return scheduled

    async def recover_pending_jobs(self, session: AsyncSession) -> int:
        """Recovers any abandoned QUEUED or RUNNING jobs from database after process restart/crash."""
        stmt = select(FulfillmentJobRecord).where(
            FulfillmentJobRecord.status.in_([FulfillmentJobStatus.QUEUED, FulfillmentJobStatus.RUNNING])
        )
        res = await session.execute(stmt)
        abandoned_records = res.scalars().all()
        count = 0
        for rec in abandoned_records:
            rec.status = FulfillmentJobStatus.QUEUED
            job = FulfillmentJob(
                order_id=rec.order_id,
                recipient=rec.recipient,
                attempt_number=rec.attempt_number,
                metadata=rec.payload,
                job_id=rec.id,
            )
            if await self.enqueue_persisted_job(job):
                count += 1
        await session.commit()
        logger.info("Recovered %d abandoned fulfillment jobs from durable storage.", count)
        return count

    async def start(self) -> None:
        self._is_running = True
        self._worker_task = asyncio.create_task(self._run_loop(), name="fulfillment-worker-loop")
        logger.info("Fulfillment worker started.")

    async def stop(self) -> None:
        self._is_running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Fulfillment worker stopped gracefully.")

    async def _run_loop(self) -> None:
        while self._is_running:
            try:
                job = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except TimeoutError:
                try:
                    async with self.session_factory() as session:
                        await self.poll_queued_jobs(session)
                except Exception:
                    logger.exception("Failed to poll durable fulfillment jobs")
                continue
            except asyncio.CancelledError:
                break

            if job.job_id is not None:
                self._queued_job_ids.discard(job.job_id)
            await self._process_job(job)
            self.queue.task_done()

    async def _process_job(self, job: FulfillmentJob) -> FulfillmentAttempt | None:
        logger.info("Processing fulfillment job for order %s [attempt %d]", job.order_id, job.attempt_number)
        rec_id: uuid.UUID | None = job.job_id

        async with self.session_factory() as session:
            if rec_id is None:
                # Fallback: locate latest QUEUED record for this order
                stmt_rec = (
                    select(FulfillmentJobRecord.id)
                    .where(
                        FulfillmentJobRecord.order_id == job.order_id,
                        FulfillmentJobRecord.status == FulfillmentJobStatus.QUEUED,
                    )
                    .order_by(FulfillmentJobRecord.created_at.desc())
                )
                rec_id = (await session.execute(stmt_rec)).scalars().first()

            if rec_id is not None:
                claimed = await self.claim_job(session, rec_id)
                if not claimed:
                    logger.warning(
                        "Job %s was already claimed by another worker or is not in QUEUED state. Skipping.",
                        rec_id,
                    )
                    return None

            try:
                attempt = await self.service.execute_order_fulfillment(
                    session=session,
                    order_id=job.order_id,
                    recipient=job.recipient,
                    attempt_number=job.attempt_number,
                )
                if rec_id:
                    rec = await session.get(FulfillmentJobRecord, rec_id)
                    if rec:
                        rec.status = FulfillmentJobStatus.COMPLETED
                        rec.completed_at = datetime.now(UTC)
                        await session.commit()
                return attempt

            except ProviderError as prov_err:
                if prov_err.is_retryable and job.attempt_number < self.max_retries:
                    backoff = self.base_backoff_seconds * (2 ** (job.attempt_number - 1))
                    logger.warning(
                        "Job for order %s failed retryably (%s). Retrying in %.2fs (next attempt: %d)",
                        job.order_id,
                        prov_err,
                        backoff,
                        job.attempt_number + 1,
                    )
                    if rec_id:
                        rec = await session.get(FulfillmentJobRecord, rec_id)
                        if rec:
                            rec.status = FulfillmentJobStatus.QUEUED
                            rec.attempt_number = job.attempt_number + 1
                            rec.last_error = str(prov_err)
                            rec.failure_classification = type(prov_err).__name__
                            await session.commit()

                    await asyncio.sleep(backoff)
                    await self.enqueue(
                        order_id=job.order_id,
                        recipient=job.recipient,
                        attempt_number=job.attempt_number + 1,
                        metadata=job.metadata,
                        persist_db=False,
                        job_id=rec_id,
                    )
                else:
                    logger.error(
                        "Job for order %s permanently failed or exceeded max retries (%d): %s",
                        job.order_id,
                        self.max_retries,
                        prov_err,
                    )
                    if rec_id:
                        rec = await session.get(FulfillmentJobRecord, rec_id)
                        if rec:
                            rec.status = FulfillmentJobStatus.DEAD_LETTER
                            rec.last_error = str(prov_err)
                            rec.failure_classification = type(prov_err).__name__
                            await session.commit()
                    self.dead_letter_queue.append(job)
                return None

            except Exception as e:
                logger.exception("Job for order %s encountered unhandled error", job.order_id)
                if rec_id:
                    rec = await session.get(FulfillmentJobRecord, rec_id)
                    if rec:
                        rec.status = FulfillmentJobStatus.DEAD_LETTER
                        rec.last_error = str(e)
                        rec.failure_classification = type(e).__name__
                        await session.commit()
                self.dead_letter_queue.append(job)
                return None

    async def process_one_now(self) -> FulfillmentAttempt | None:
        """Helper to process one queued job synchronously for testing."""
        if self.queue.empty():
            return None
        job = await self.queue.get()
        if job.job_id is not None:
            self._queued_job_ids.discard(job.job_id)
        res = await self._process_job(job)
        self.queue.task_done()
        return res
