import asyncio
import logging

from packages.core.database import async_session_factory
from packages.factory.provisioning import BotProvisioningService

logger = logging.getLogger("factory.provisioning_worker")


class BotProvisioningWorker:
    def __init__(self, service: BotProvisioningService | None = None, poll_seconds: float = 1.0) -> None:
        self.service = service or BotProvisioningService()
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task | None = None
        self._is_running = False

    async def start(self) -> None:
        async with async_session_factory() as session:
            recovered = await self.service.recover_stale_jobs(session)
        if recovered:
            logger.warning("Recovered %d stale bot provisioning job(s)", recovered)
        self._is_running = True
        self._task = asyncio.create_task(self._run_loop(), name="bot-provisioning-worker")

    async def stop(self) -> None:
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run_loop(self) -> None:
        while self._is_running:
            try:
                processed = await self.service.process_one()
                if not processed:
                    await asyncio.sleep(self.poll_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("Bot provisioning worker cycle failed: %s", type(exc).__name__)
                await asyncio.sleep(self.poll_seconds)
