import asyncio
import logging
import signal

from packages.core.config import settings
from packages.core.database import async_session_factory
from packages.core.heartbeat import ServiceHeartbeat
from packages.core.observability import configure_logging
from packages.factory.worker import BotProvisioningWorker
from packages.fulfillment.reconciliation_worker import ProviderReconciliationWorker
from packages.fulfillment.worker import FulfillmentWorker
from packages.payments.flexible_deposit_worker import FlexibleDepositReconciliationWorker
from packages.payments.onchain_reconciliation import OnChainPaymentReconciliationWorker
from packages.payments.onchain_verifiers import register_configured_onchain_verifiers
from packages.payments.platform import default_onchain_verifier_registry
from packages.payments.provider_reconciliation_worker import PaymentProviderReconciliationWorker
from packages.payments.reversal_worker import WalletTopUpReversalWorker
from packages.payments.stars_reconciliation import TelegramStarsReconciliationWorker
from packages.providers.balance_monitor import ProviderBalanceMonitorWorker
from packages.saas.reconciliation_worker import SaaSBillingReconciliationWorker

logger = logging.getLogger("apps.worker")


async def run_worker() -> None:
    """Run durable fulfillment, refund, and Telegram Stars reconciliation workers until shutdown."""
    configure_logging(service="worker", level=settings.log_level, json_logs=settings.log_json)

    heartbeat = ServiceHeartbeat("worker")
    registered_chains = register_configured_onchain_verifiers(
        default_onchain_verifier_registry, settings
    )
    if registered_chains:
        logger.info("Registered trusted payment chain verifiers: %s", ",".join(registered_chains))
    fulfillment_worker = FulfillmentWorker()
    provider_reconciliation_worker = ProviderReconciliationWorker()
    provider_balance_monitor_worker = ProviderBalanceMonitorWorker()
    provisioning_worker = BotProvisioningWorker()
    reversal_worker = WalletTopUpReversalWorker()
    onchain_payment_reconciliation_worker = OnChainPaymentReconciliationWorker()
    payment_provider_reconciliation_worker = PaymentProviderReconciliationWorker()
    flexible_deposit_reconciliation_worker = FlexibleDepositReconciliationWorker()
    stars_reconciliation_worker = TelegramStarsReconciliationWorker()
    saas_billing_reconciliation_worker = SaaSBillingReconciliationWorker()
    started: list[str] = []

    await heartbeat.start()
    try:
        async with async_session_factory() as session:
            recovered = await fulfillment_worker.recover_pending_jobs(session)
        logger.info("Recovered %d durable fulfillment jobs at startup", recovered)

        await fulfillment_worker.start()
        started.append("fulfillment")
        await provider_reconciliation_worker.start()
        if provider_reconciliation_worker.enabled:
            started.append("provider-reconciliation")
        await provider_balance_monitor_worker.start()
        if provider_balance_monitor_worker.enabled:
            started.append("provider-balance")
        await provisioning_worker.start()
        started.append("provisioning")
        await reversal_worker.start()
        started.append("reversal")
        await payment_provider_reconciliation_worker.start()
        if payment_provider_reconciliation_worker.enabled:
            started.append("payment-provider")
        await flexible_deposit_reconciliation_worker.start()
        if flexible_deposit_reconciliation_worker.enabled:
            started.append("payment-flexible-deposit")
        await onchain_payment_reconciliation_worker.start()
        if onchain_payment_reconciliation_worker.enabled:
            started.append("payment-onchain")
        await stars_reconciliation_worker.start()
        started.append("stars")
        await saas_billing_reconciliation_worker.start()
        if saas_billing_reconciliation_worker.enabled:
            started.append("saas-billing")

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signal_name, stop_event.set)
            except NotImplementedError:
                pass
        await stop_event.wait()
    finally:
        if "saas-billing" in started:
            await saas_billing_reconciliation_worker.stop()
        if "provisioning" in started:
            await provisioning_worker.stop()
        if "provider-balance" in started:
            await provider_balance_monitor_worker.stop()
        if "provider-reconciliation" in started:
            await provider_reconciliation_worker.stop()
        if "stars" in started:
            await stars_reconciliation_worker.stop()
        if "payment-flexible-deposit" in started:
            await flexible_deposit_reconciliation_worker.stop()
        if "payment-provider" in started:
            await payment_provider_reconciliation_worker.stop()
        if "payment-onchain" in started:
            await onchain_payment_reconciliation_worker.stop()
        if "reversal" in started:
            await reversal_worker.stop()
        if "fulfillment" in started:
            await fulfillment_worker.stop()
        await heartbeat.stop()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
