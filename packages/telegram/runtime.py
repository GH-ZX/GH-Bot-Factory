import asyncio
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot as AiogramBot
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from packages.core.config import settings
from packages.core.database import async_session_factory
from packages.telegram.fleet_state import BotFleetStateStore
from packages.telegram.launch import configure_bot_menu_button, resolve_tenant_public_url
from packages.telegram.middleware.correlation import CorrelationMiddleware
from packages.telegram.middleware.error import ErrorHandlingMiddleware
from packages.telegram.middleware.logging import UpdateLoggingMiddleware
from packages.telegram.middleware.tenant import TenantResolutionMiddleware
from packages.telegram.models import Bot
from packages.telegram.secrets import SecretStorage, get_default_secret_storage

logger = logging.getLogger("telegram.runtime_manager")


def _bot_runtime_signature(bot_record: Bot) -> str:
    payload = {
        "token_secret_ref": bot_record.token_secret_ref,
        "credential_version": bot_record.credential_version,
        "runtime_revision": bot_record.runtime_revision,
        "release_channel": bot_record.release_channel,
        "username": bot_record.username,
        "display_name": bot_record.display_name,
        "is_enabled": bot_record.is_enabled,
        "config": bot_record.config or {},
        "updated_at": bot_record.updated_at.isoformat() if bot_record.updated_at else None,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class BotInstance:
    """Encapsulates a live Aiogram bot and the desired-state signature that launched it."""

    def __init__(self, bot_record: Bot, bot: AiogramBot, dispatcher: Dispatcher) -> None:
        self.bot_record = bot_record
        self.bot = bot
        self.dispatcher = dispatcher
        self.signature = _bot_runtime_signature(bot_record)
        self.task: asyncio.Task | None = None
        self.started_at: datetime | None = None


class BotRuntimeManager:
    """Converges live Telegram polling tasks toward enabled Bot rows in PostgreSQL."""

    def __init__(
        self,
        secret_storage: SecretStorage | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        base_dispatcher_factory: Any | None = None,
        fleet_state_store: BotFleetStateStore | None = None,
    ) -> None:
        self.secret_storage = secret_storage or get_default_secret_storage()
        self.session_factory = session_factory or async_session_factory
        self.base_dispatcher_factory = base_dispatcher_factory
        self.fleet_state_store = fleet_state_store or BotFleetStateStore()
        self.active_bots: dict[uuid.UUID, BotInstance] = {}
        self.startup_failures: list[dict[str, Any]] = []
        self.last_initialization_time: datetime | None = None
        self.last_reconciliation_time: datetime | None = None
        self._is_running = False
        self._reconcile_task: asyncio.Task | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._restart_failures: dict[uuid.UUID, int] = {}
        self._next_restart_at: dict[uuid.UUID, datetime] = {}

    def create_dispatcher(self, bot_id: uuid.UUID) -> Dispatcher:
        storage = MemoryStorage()
        dp = Dispatcher(storage=storage)
        dp.update.outer_middleware(CorrelationMiddleware())
        dp.update.outer_middleware(
            TenantResolutionMiddleware(bot_id=bot_id, session_factory=self.session_factory)
        )
        dp.update.outer_middleware(UpdateLoggingMiddleware())
        dp.update.outer_middleware(ErrorHandlingMiddleware())
        if self.base_dispatcher_factory:
            self.base_dispatcher_factory(dp)
        else:
            from packages.telegram.routers import get_root_router

            dp.include_router(get_root_router())
        return dp

    def _record_startup_failure(self, bot_record: Bot, exc: Exception) -> None:
        self.startup_failures.append(
            {
                "bot_id": str(bot_record.id),
                "username": bot_record.username,
                "error": type(exc).__name__,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        del self.startup_failures[:-50]

    async def initialize_bot(self, bot_record: Bot) -> BotInstance | None:
        """Resolve token, configure the bot, and stage an instance without logging secrets."""
        try:
            token = await self.secret_storage.get_secret(bot_record.token_secret_ref)
            bot = AiogramBot(token=token)
            try:
                dp = self.create_dispatcher(bot_record.id)
                instance = BotInstance(bot_record=bot_record, bot=bot, dispatcher=dp)
                miniapp_public_url = resolve_tenant_public_url(
                    getattr(bot_record.tenant, "settings", None),
                    kind="miniapp",
                    fallback=settings.miniapp_public_url,
                )
                if miniapp_public_url:
                    try:
                        branding = (bot_record.config or {}).get("branding", {})
                        menu_text = (
                            branding.get("menu_text")
                            if isinstance(branding, dict)
                            else None
                        ) or settings.miniapp_menu_text
                        await configure_bot_menu_button(
                            bot,
                            public_url=miniapp_public_url,
                            bot_id=bot_record.id,
                            menu_text=menu_text,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Mini App menu configuration failed bot_id=%s error_type=%s",
                            bot_record.id,
                            type(exc).__name__,
                        )
                self.active_bots[bot_record.id] = instance
                self.last_initialization_time = datetime.now(UTC)
                return instance
            except Exception:
                await bot.session.close()
                raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Bot initialization failed bot_id=%s username=%s error_type=%s",
                bot_record.id,
                bot_record.username,
                type(exc).__name__,
            )
            self._record_startup_failure(bot_record, exc)
            await self.fleet_state_store.publish(
                bot_record.id,
                status="FAILED",
                detail=type(exc).__name__,
                username=bot_record.username,
            )
            return None

    async def _start_instance(self, instance: BotInstance) -> None:
        if instance.task and not instance.task.done():
            return
        instance.started_at = datetime.now(UTC)
        instance.task = asyncio.create_task(
            instance.dispatcher.start_polling(instance.bot),
            name=f"bot-polling-{instance.bot_record.id}",
        )
        await self.fleet_state_store.publish(
            instance.bot_record.id,
            status="RUNNING",
            username=instance.bot_record.username,
        )

    async def _stop_instance(self, bot_id: uuid.UUID) -> None:
        instance = self.active_bots.pop(bot_id, None)
        if instance is None:
            return
        if instance.task and not instance.task.done():
            instance.task.cancel()
            await asyncio.gather(instance.task, return_exceptions=True)
        try:
            await instance.bot.session.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bot session close failed bot_id=%s error_type=%s", bot_id, type(exc).__name__)
        await self.fleet_state_store.delete(bot_id)

    async def _load_desired_bots(self) -> list[Bot]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(Bot)
                .where(
                    Bot.is_enabled.is_(True),
                    Bot.deleted_at.is_(None),
                    Bot.release_channel.in_(settings.bot_runtime_release_channel_set),
                )
                .options(selectinload(Bot.tenant))
            )
            return list(result.scalars().all())

    async def load_and_initialize_all(self) -> int:
        enabled_bots = await self._load_desired_bots()
        initialized_count = 0
        for bot_rec in enabled_bots:
            instance = await self.initialize_bot(bot_rec)
            if instance:
                initialized_count += 1
        return initialized_count

    async def start_polling(self) -> None:
        self._is_running = True
        async with self._lifecycle_lock:
            for instance in list(self.active_bots.values()):
                await self._start_instance(instance)
        logger.info("Started polling for %d bot instance(s)", len(self.active_bots))

    async def reconcile_once(self) -> dict[str, int]:
        """Converge create/update/disable/crashed task changes without process restart."""
        desired_rows = await self._load_desired_bots()
        desired = {row.id: row for row in desired_rows}
        stats = {"started": 0, "stopped": 0, "restarted": 0, "unchanged": 0, "failed": 0}
        async with self._lifecycle_lock:
            for bot_id in list(self.active_bots):
                if bot_id not in desired:
                    await self._stop_instance(bot_id)
                    self._restart_failures.pop(bot_id, None)
                    self._next_restart_at.pop(bot_id, None)
                    stats["stopped"] += 1

            now = datetime.now(UTC)
            for bot_id, bot_record in desired.items():
                current = self.active_bots.get(bot_id)
                signature = _bot_runtime_signature(bot_record)
                crashed = bool(current and current.task and current.task.done())
                changed = bool(current and current.signature != signature)
                if current is not None and not crashed and not changed:
                    self._restart_failures.pop(bot_id, None)
                    self._next_restart_at.pop(bot_id, None)
                    stats["unchanged"] += 1
                    continue

                if changed:
                    self._restart_failures.pop(bot_id, None)
                    self._next_restart_at.pop(bot_id, None)

                if crashed and not changed:
                    await self._stop_instance(bot_id)
                    failures = self._restart_failures.get(bot_id, 0) + 1
                    self._restart_failures[bot_id] = failures
                    delay = min(300, 2 ** min(failures, 8))
                    self._next_restart_at[bot_id] = now + timedelta(seconds=delay)
                    await self.fleet_state_store.publish(
                        bot_id,
                        status="BACKOFF",
                        detail=f"restart_in_{delay}s",
                        username=bot_record.username,
                    )
                    stats["failed"] += 1
                    continue

                retry_at = self._next_restart_at.get(bot_id)
                if current is None and retry_at is not None and retry_at > now:
                    stats["failed"] += 1
                    continue

                if current is not None:
                    await self._stop_instance(bot_id)
                    stats["restarted"] += 1
                instance = await self.initialize_bot(bot_record)
                if instance is None:
                    failures = self._restart_failures.get(bot_id, 0) + 1
                    self._restart_failures[bot_id] = failures
                    delay = min(300, 2 ** min(failures, 8))
                    self._next_restart_at[bot_id] = now + timedelta(seconds=delay)
                    await self.fleet_state_store.publish(
                        bot_id,
                        status="BACKOFF",
                        detail=f"retry_in_{delay}s",
                        username=bot_record.username,
                    )
                    stats["failed"] += 1
                    continue
                self._next_restart_at.pop(bot_id, None)
                await self._start_instance(instance)
                if current is None:
                    stats["started"] += 1

        self.last_reconciliation_time = datetime.now(UTC)
        return stats

    async def start_reconciliation(self, interval_seconds: float | None = None) -> None:
        interval = interval_seconds or settings.bot_runtime_reconcile_seconds
        if self._reconcile_task and not self._reconcile_task.done():
            return

        async def loop() -> None:
            while self._is_running:
                try:
                    await asyncio.sleep(interval)
                    stats = await self.reconcile_once()
                    if stats["started"] or stats["stopped"] or stats["restarted"] or stats["failed"]:
                        logger.info("Bot runtime reconciled desired state stats=%s", stats)
                except asyncio.CancelledError:
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.error("Bot runtime reconciliation failed error_type=%s", type(exc).__name__)

        self._reconcile_task = asyncio.create_task(loop(), name="bot-runtime-reconciliation")

    async def stop(self) -> None:
        self._is_running = False
        if self._reconcile_task and not self._reconcile_task.done():
            self._reconcile_task.cancel()
            await asyncio.gather(self._reconcile_task, return_exceptions=True)
        async with self._lifecycle_lock:
            for bot_id in list(self.active_bots):
                await self._stop_instance(bot_id)
        logger.info("All bot instances successfully stopped")

    def get_health_status(self) -> dict[str, Any]:
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
                    "polling_done": bool(instance.task and instance.task.done()),
                }
                for instance in self.active_bots.values()
            ],
            "startup_failures_count": len(self.startup_failures),
            "startup_failures": self.startup_failures[-10:],
            "last_initialized_at": self.last_initialization_time.isoformat() if self.last_initialization_time else None,
            "last_reconciled_at": self.last_reconciliation_time.isoformat() if self.last_reconciliation_time else None,
            "restart_backoff_bots": len(self._next_restart_at),
        }
