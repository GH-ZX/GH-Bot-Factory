from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import text

from packages.core.config import settings
from packages.core.database import async_session_factory


async def check_database(timeout_seconds: float | None = None) -> tuple[bool, str]:
    timeout = timeout_seconds or settings.health_dependency_timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:
        return False, type(exc).__name__


async def check_redis(timeout_seconds: float | None = None) -> tuple[bool, str]:
    timeout = timeout_seconds or settings.health_dependency_timeout_seconds
    from redis.asyncio import Redis

    client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    try:
        async with asyncio.timeout(timeout):
            await client.ping()
        return True, "ok"
    except Exception as exc:
        return False, type(exc).__name__
    finally:
        await client.aclose()


async def readiness_report() -> tuple[bool, dict[str, Any]]:
    db, redis = await asyncio.gather(check_database(), check_redis())
    ready = db[0] and redis[0]
    return ready, {
        "status": "ready" if ready else "not_ready",
        "checks": {
            "database": {"ok": db[0], "detail": db[1]},
            "redis": {"ok": redis[0], "detail": redis[1]},
        },
    }
