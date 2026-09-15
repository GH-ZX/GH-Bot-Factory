import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

logger = logging.getLogger("telegram.runtime")


class UpdateLoggingMiddleware(BaseMiddleware):
    """Logs incoming Telegram events with correlation IDs while guaranteeing zero secret leaks."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        start_time = time.monotonic()
        correlation_id = data.get("correlation_id", "unknown")
        tenant_context = data.get("tenant_context")

        bot_id = str(tenant_context.bot_id) if tenant_context else "unresolved"
        tenant_id = str(tenant_context.tenant_id) if tenant_context else "unresolved"
        user_id = str(tenant_context.telegram_user_id) if tenant_context else "anonymous"

        event_type = type(event).__name__

        logger.info(
            "START update=%s bot=%s tenant=%s user=%s [cid=%s]",
            event_type,
            bot_id,
            tenant_id,
            user_id,
            correlation_id,
        )

        try:
            result = await handler(event, data)
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                "COMPLETED update=%s elapsed=%.2fms [cid=%s]",
                event_type,
                elapsed_ms,
                correlation_id,
            )
            return result
        except Exception:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.warning(
                "FAILED update=%s elapsed=%.2fms [cid=%s]",
                event_type,
                elapsed_ms,
                correlation_id,
            )
            raise
