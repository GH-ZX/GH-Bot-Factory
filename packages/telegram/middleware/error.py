import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from packages.core.exceptions import (
    InsufficientFundsError,
    InvalidStateTransitionError,
    ProviderError,
    TenantAccessViolationError,
)

logger = logging.getLogger("telegram.errors")


class ErrorHandlingMiddleware(BaseMiddleware):
    """Intercepts uncaught exceptions, categorizes them, logs details, and presents safe messages to users."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        correlation_id = data.get("correlation_id", "unknown")
        try:
            return await handler(event, data)
        except InsufficientFundsError as exc:
            logger.info("Insufficient funds [cid=%s]: %s", correlation_id, exc)
            await self._respond_user(event, "⚠️ Insufficient wallet balance to complete this transaction.")
            return None
        except InvalidStateTransitionError as exc:
            logger.info("Invalid state transition [cid=%s]: %s", correlation_id, exc)
            await self._respond_user(event, "⚠️ This action is not allowed for the order's current status.")
            return None
        except TenantAccessViolationError as exc:
            logger.error("CRITICAL tenant access violation [cid=%s]: %s", correlation_id, exc)
            await self._respond_user(event, "⛔ Access denied: Tenant boundary violation.")
            return None
        except ProviderError as exc:
            logger.error("Upstream provider failure [cid=%s]: %s", correlation_id, exc)
            await self._respond_user(event, "⚠️ Upstream provider is temporarily unavailable. Please try again shortly.")
            return None
        except Exception:
            logger.exception("Unhandled error processing update [cid=%s]", correlation_id)
            safe_msg = f"❌ An unexpected error occurred. Please try again later.\nRef: <code>{correlation_id}</code>"
            await self._respond_user(event, safe_msg)
            return None

    @staticmethod
    async def _respond_user(event: TelegramObject, text: str) -> None:
        try:
            if isinstance(event, Message):
                await event.answer(text, parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
                if event.message:
                    await event.message.answer(text, parse_mode="HTML")
        except Exception as send_err:  # noqa: BLE001
            logger.warning("Failed sending error notification to user: %s", send_err)

