import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.database import async_session_factory
from packages.fulfillment.models import FulfillmentAttempt
from packages.fulfillment.service import FulfillmentService
from packages.providers.exceptions import ProviderError

logger = logging.getLogger("fulfillment.worker")


@dataclass
class FulfillmentJob:
    order_id: uuid.UUID
    recipient: str
    attempt_number: int = 1
    metadata: dict[str, Any] | None = None


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
        self._worker_task: asyncio.Task | None = None
        self._is_running = False

    async def enqueue(
        self,
        order_id: uuid.UUID,
        recipient: str,
        attempt_number: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        job = FulfillmentJob(
            order_id=order_id,
            recipient=recipient,
            attempt_number=attempt_number,
            metadata=metadata,
        )
        await self.queue.put(job)
        logger.info("Enqueued fulfillment job for order %s (attempt=%d)", order_id, attempt_number)

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
                continue
            except asyncio.CancelledError:
                break

            await self._process_job(job)
            self.queue.task_done()

    async def _process_job(self, job: FulfillmentJob) -> FulfillmentAttempt | None:
        logger.info("Processing fulfillment job for order %s [attempt %d]", job.order_id, job.attempt_number)
        async with self.session_factory() as session:
            try:
                attempt = await self.service.execute_order_fulfillment(
                    session=session,
                    order_id=job.order_id,
                    recipient=job.recipient,
                    attempt_number=job.attempt_number,
                )
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
                    await asyncio.sleep(backoff)
                    await self.enqueue(
                        order_id=job.order_id,
                        recipient=job.recipient,
                        attempt_number=job.attempt_number + 1,
                        metadata=job.metadata,
                    )
                else:
                    logger.error(
                        "Job for order %s permanently failed or exceeded max retries (%d): %s",
                        job.order_id,
                        self.max_retries,
                        prov_err,
                    )
                    self.dead_letter_queue.append(job)
                return None

            except Exception:
                logger.exception("Job for order %s encountered unhandled error", job.order_id)
                self.dead_letter_queue.append(job)
                return None

    async def process_one_now(self) -> FulfillmentAttempt | None:
        """Helper to process one queued job synchronously for testing."""
        if self.queue.empty():
            return None
        job = await self.queue.get()
        res = await self._process_job(job)
        self.queue.task_done()
        return res
