import asyncio
import logging
import signal
import uuid

from packages.core.config import settings
from packages.core.heartbeat import ServiceHeartbeat
from packages.core.observability import configure_logging
from packages.telegram.launch import build_miniapp_url
from packages.telegram.runtime import BotRuntimeManager

logger = logging.getLogger("apps.bot_runtime")


async def run_bot_runtime() -> None:
    """Continuously reconcile enabled Bot rows into isolated Telegram polling tasks."""
    configure_logging(service="bot-runtime", level=settings.log_level, json_logs=settings.log_json)

    if settings.miniapp_public_url:
        build_miniapp_url(
            settings.miniapp_public_url,
            uuid.UUID("00000000-0000-0000-0000-000000000000"),
        )

    heartbeat = ServiceHeartbeat("bot-runtime")
    manager = BotRuntimeManager()
    await heartbeat.start()
    try:
        initialized = await manager.load_and_initialize_all()
        logger.info("Initialized %d Telegram bot instance(s)", initialized)
        if initialized == 0:
            logger.warning("No enabled Telegram bots are active; reconciliation will watch for new bots")

        await manager.start_polling()
        await manager.start_reconciliation()

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signal_name, stop_event.set)
            except NotImplementedError:
                pass
        await stop_event.wait()
    finally:
        await manager.stop()
        await heartbeat.stop()


def main() -> None:
    asyncio.run(run_bot_runtime())


if __name__ == "__main__":
    main()
