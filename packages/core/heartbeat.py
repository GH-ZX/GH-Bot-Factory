from __future__ import annotations

import asyncio
import logging
import os

from packages.core.config import settings

logger = logging.getLogger(__name__)


def heartbeat_key(service: str) -> str:
    instance = os.getenv("HOSTNAME", "local")
    return f"ghbf:heartbeat:{service}:{instance}"


class ServiceHeartbeat:
    def __init__(self, service: str, *, interval_seconds: int = 10, ttl_seconds: int = 35) -> None:
        self.service = service
        self.interval_seconds = interval_seconds
        self.ttl_seconds = ttl_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"{self.service}-heartbeat")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        from redis.asyncio import Redis

        client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        try:
            while not self._stop.is_set():
                try:
                    await client.set(heartbeat_key(self.service), "alive", ex=self.ttl_seconds)
                except Exception as exc:  # noqa: BLE001 - health boundary reports dependency failure
                    logger.warning("Service heartbeat update failed: %s", type(exc).__name__)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
                except TimeoutError:
                    pass
        finally:
            await client.aclose()
