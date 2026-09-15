import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot as AiogramBot
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from packages.core.database import async_session_factory
from packages.telegram.middleware.correlation import CorrelationMiddleware
from packages.telegram.middleware.error import ErrorHandlingMiddleware
from packages.telegram.middleware.logging import UpdateLoggingMiddleware
from packages.telegram.middleware.tenant import TenantResolutionMiddleware
from packages.telegram.models import Bot
from packages.telegram.secrets import EnvSecretStorage, SecretStorage

logger = logging.getLogger("telegram.runtime_manager")


class BotInstance:
    """Encapsulates a live, running Aiogram Bot and its attached task."""

    def __init__(
        self,
        bot_record: Bot,
        bot: AiogramBot,
        dispatcher: Dispatcher,
    ) -> None:
        self.bot_record = bot_record
        self.bot = bot
        self.dispatcher = dispatcher
        self.task: asyncio.Task | None = None
        self.started_at: datetime | None = None


class BotRuntimeManager:
    """Manages the lifecycle of multiple isolated Telegram bots across tenants."""

    def __init__(
        self,
        secret_storage: SecretStorage | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        base_dispatcher_factory: Any | None = None,
    ) -> None:
        self.secret_storage = secret_storage or EnvSecretStorage()
        self.session_factory = session_factory or async_session_factory
        self.base_dispatcher_factory = base_dispatcher_factory
        self.active_bots: dict[uuid.UUID, BotInstance] = {}
        self.startup_failures: list[dict[str, Any]] = []
        self.last_initialization_time: datetime | None = None
        self._is_running = False

    def create_dispatcher(self, bot_id: uuid.UUID) -> Dispatcher:
        """Instantiates a Dispatcher configured with the middleware pipeline for a specific bot."""
        storage = MemoryStorage()
        dp = Dispatcher(storage=storage)

        # Register outer middlewares in order
        dp.update.outer_middleware(CorrelationMiddleware())
        dp.update.outer_middleware(
            TenantResolutionMiddleware(
                bot_id=bot_id,
                session_factory=self.session_factory,
            )
        )
        dp.update.outer_middleware(UpdateLoggingMiddleware())
        dp.update.outer_middleware(ErrorHandlingMiddleware())

        # Include standard routers if custom factory provided, or default root router
        if self.base_dispatcher_factory:
            self.base_dispatcher_factory(dp)
        else:
            from packages.telegram.routers import get_root_router

            dp.include_router(get_root_router())

        return dp

    async def initialize_bot(self, bot_record: Bot) -> BotInstance | None:
        """Initializes an individual bot instance using resolved credentials."""
        try:
            token = await self.secret_storage.get_secret(bot_record.token_secret_ref)
            bot = AiogramBot(token=token)
            dp = self.create_dispatcher(bot_record.id)

            instance = BotInstance(
                bot_record=bot_record,
                bot=bot,
                dispatcher=dp,
            )
            self.active_bots[bot_record.id] = instance
            self.last_initialization_time = datetime.now(UTC)
            return instance
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to initialize bot id=%s username=%s: %s",
                bot_record.id,
                bot_record.username,
                exc,
            )
            self.startup_failures.append(
                {
                    "bot_id": str(bot_record.id),
                    "username": bot_record.username,
                    "error": type(exc).__name__,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            return None

    async def load_and_initialize_all(self) -> int:
        """Loads all enabled bots from database and initializes them."""
        async with self.session_factory() as session:
            stmt = (
                select(Bot)
                .where(Bot.is_enabled.is_(True), Bot.deleted_at.is_(None))
                .options(selectinload(Bot.tenant))
            )
            result = await session.execute(stmt)
            enabled_bots = result.scalars().all()

        initialized_count = 0
        for bot_rec in enabled_bots:
            instance = await self.initialize_bot(bot_rec)
            if instance:
                initialized_count += 1

        return initialized_count

    async def start_polling(self) -> None:
        """Starts polling loops for all active bots concurrently."""
        self._is_running = True
        tasks = []
        for bot_id, instance in self.active_bots.items():
            instance.started_at = datetime.now(UTC)
            task = asyncio.create_task(
                instance.dispatcher.start_polling(instance.bot),
                name=f"bot-polling-{bot_id}",
            )
            instance.task = task
            tasks.append(task)

        logger.info("Started polling for %d bot instances.", len(tasks))

    async def stop(self) -> None:
        """Gracefully shuts down all active bot instances and releases connections."""
        self._is_running = False
        for bot_id, instance in list(self.active_bots.items()):
            logger.info("Stopping bot id=%s username=%s", bot_id, instance.bot_record.username)
            if instance.task and not instance.task.done():
                instance.task.cancel()
                try:
                    await instance.task
                except asyncio.CancelledError:
                    pass
            await instance.bot.session.close()

        self.active_bots.clear()
        logger.info("All bot instances successfully stopped.")

    def get_health_status(self) -> dict[str, Any]:
        """Provides non-sensitive runtime diagnostic health metrics."""
        return {
            "status": "running" if self._is_running else "idle",
            "active_bots_count": len(self.active_bots),
            "running_bots": [
                {
                    "bot_id": str(instance.bot_record.id),
                    "tenant_id": str(instance.bot_record.tenant_id),
                    "username": instance.bot_record.username,
                    "display_name": instance.bot_record.display_name,
                    "started_at": instance.started_at.isoformat() if instance.started_at else None,
                }
                for instance in self.active_bots.values()
            ],
            "startup_failures_count": len(self.startup_failures),
            "startup_failures": self.startup_failures[-10:],
            "last_initialized_at": (
                self.last_initialization_time.isoformat()
                if self.last_initialization_time
                else None
            ),
        }
