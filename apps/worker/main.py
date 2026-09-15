import asyncio
import logging
import signal

from packages.core.config import settings
from packages.core.database import async_session_factory
from packages.core.heartbeat import ServiceHeartbeat
from packages.core.observability import configure_logging
from packages.fulfillment.worker import FulfillmentWorker
from packages.factory.worker import BotProvisioningWorker
from packages.payments.reversal_worker import WalletTopUpReversalWorker
from packages.payments.stars_reconciliation import TelegramStarsReconciliationWorker

logger = logging.getLogger("apps.worker")


async def run_worker() -> None:
    """Run durable fulfillment, refund, and Telegram Stars reconciliation workers until shutdown."""
    configure_logging(service="worker", level=settings.log_level, json_logs=settings.log_json)

    heartbeat = ServiceHeartbeat("worker")
    fulfillment_worker = FulfillmentWorker()
    provisioning_worker = BotProvisioningWorker()
    reversal_worker = WalletTopUpReversalWorker()
    stars_reconciliation_worker = TelegramStarsReconciliationWorker()
    started: list[str] = []

    await heartbeat.start()
    try:
        async with async_session_factory() as session:
            recovered = await fulfillment_worker.recover_pending_jobs(session)
        logger.info("Recovered %d durable fulfillment jobs at startup", recovered)

        await fulfillment_worker.start()
        started.append("fulfillment")
        await provisioning_worker.start()
        started.append("provisioning")
        await reversal_worker.start()
        started.append("reversal")
        await stars_reconciliation_worker.start()
        started.append("stars")

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signal_name, stop_event.set)
            except NotImplementedError:
                pass
        await stop_event.wait()
    finally:
        if "provisioning" in started:
            await provisioning_worker.stop()
        if "stars" in started:
            await stars_reconciliation_worker.stop()
        if "reversal" in started:
            await reversal_worker.stop()
        if "fulfillment" in started:
            await fulfillment_worker.stop()
        await heartbeat.stop()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
