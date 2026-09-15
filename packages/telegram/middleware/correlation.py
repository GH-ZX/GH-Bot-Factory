import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class CorrelationMiddleware(BaseMiddleware):
    """Generates an explicit correlation ID for every incoming Telegram update."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        correlation_id = str(uuid.uuid4())
        data["correlation_id"] = correlation_id
        return await handler(event, data)
