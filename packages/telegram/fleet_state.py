from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from packages.core.config import settings

logger = logging.getLogger(__name__)


def fleet_state_key(bot_id: uuid.UUID | str) -> str:
    return f"ghbf:fleet:bot:{bot_id}"


class BotFleetStateStore:
    """Ephemeral runtime observation store.

    PostgreSQL remains desired-state authority. Redis contains only short-lived
    observed runtime state so Admin can distinguish configured from actually
    running bots without coupling API memory to bot-runtime memory.
    """

    def __init__(self, *, ttl_seconds: int = 30) -> None:
        self.ttl_seconds = ttl_seconds

    async def publish(
        self,
        bot_id: uuid.UUID,
        *,
        status: str,
        detail: str | None = None,
        username: str | None = None,
    ) -> None:
        from redis.asyncio import Redis

        payload = {
            "status": status,
            "detail": detail,
            "username": username,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        try:
            await client.set(
                fleet_state_key(bot_id),
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ex=self.ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - observability must not kill runtime
            logger.warning("Bot fleet state publish failed bot_id=%s error_type=%s", bot_id, type(exc).__name__)
        finally:
            await client.aclose()

    async def delete(self, bot_id: uuid.UUID) -> None:
        from redis.asyncio import Redis

        client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        try:
            await client.delete(fleet_state_key(bot_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bot fleet state delete failed bot_id=%s error_type=%s", bot_id, type(exc).__name__)
        finally:
            await client.aclose()

    async def read_many(self, bot_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
        if not bot_ids:
            return {}
        from redis.asyncio import Redis

        client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        try:
            raw = await client.mget([fleet_state_key(bot_id) for bot_id in bot_ids])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bot fleet state read failed error_type=%s", type(exc).__name__)
            return {}
        finally:
            await client.aclose()

        result: dict[uuid.UUID, dict[str, Any]] = {}
        for bot_id, value in zip(bot_ids, raw, strict=True):
            if not value:
                continue
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                result[bot_id] = parsed
        return result
