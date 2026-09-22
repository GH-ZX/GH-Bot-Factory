"""Tenant-scoped observations; never replay purchases or settle payments."""
import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from packages.commerce.models import Order, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.fulfillment.models import FulfillmentJobRecord, FulfillmentJobStatus
from packages.payments.models import FinancialResolutionCase, PaymentIntent, PaymentMethodConfig
from packages.payments.state_machine import PaymentIntentStatus
from packages.providers.models import Provider, ProviderBalanceSnapshot, ProviderHealthStatus
from packages.telegram.fleet_state import BotFleetStateStore
from packages.telegram.models import Bot
from packages.tenants.models import Tenant


async def attention_snapshot(session, tenant_id):
    now = datetime.now(UTC)
    alerts = []
    # Bound response size while separately counting every matching record.
    rules = [
        ('order-delayed', Order, [Order.status.in_([OrderStatus.PAID, OrderStatus.PROCESSING]), Order.updated_at < now - timedelta(minutes=30)],
         'Paid order delivery is delayed', 'Inspect the order and its current fulfillment attempt. Reconcile upstream acceptance before retrying; only report a refund after its ledger transaction exists.', 'orders'),
        ('delivery', FulfillmentJobRecord, [FulfillmentJobRecord.status.in_([FulfillmentJobStatus.DEAD_LETTER, FulfillmentJobStatus.FAILED])],
         'Delivery needs review', 'Open the job and reconcile supplier evidence first. Requeue only when the existing safety decision allows it; never replay an unknown purchase.', 'fulfillment'),
        ('payment-unknown', PaymentIntent, [PaymentIntent.status == PaymentIntentStatus.UNKNOWN],
         'Payment outcome unknown', 'Reconcile the existing payment. Do not create another charge or credit a wallet from a screenshot.', 'finance'),
        ('payment-delayed', PaymentIntent, [PaymentIntent.status.in_([PaymentIntentStatus.PENDING, PaymentIntentStatus.PROCESSING]), PaymentIntent.updated_at < now - timedelta(minutes=30)],
         'Payment confirmation delayed', 'Review the existing intent and provider evidence. Pending is not a confirmed failure or refund.', 'finance'),
        ('financial-case', FinancialResolutionCase, [FinancialResolutionCase.status != 'RESOLVED'],
         'Financial case needs attention', 'Review amount, currency, provider and settlement evidence in the financial case. Resolution must use the existing audited workflow.', 'finance'),
        ('supplier-balance', ProviderBalanceSnapshot, [ProviderBalanceSnapshot.is_low_balance.is_(True)],
         'Supplier balance is low', 'Refresh the supplier balance observation and fund its account if needed. This alert does not change routing.', 'providers'),
        ('supplier-health', Provider, [Provider.is_enabled.is_(True), Provider.health_status.in_([ProviderHealthStatus.DEGRADED, ProviderHealthStatus.UNAVAILABLE])],
         'Supplier connection needs attention', 'Review credentials and connection health. Do not switch suppliers for an order with an ambiguous upstream result.', 'providers'),
        ('bot-credential', Bot, [Bot.deleted_at.is_(None), Bot.is_enabled.is_(True), Bot.credential_status != 'VERIFIED'],
         'Bot credential needs attention', 'Open this bot and verify or rotate its credential. Runtime connectivity is shown separately on the Bots page.', 'bots'),
    ]
    totals = {}
    for kind, model, conditions, title, guidance, view in rules:
        filters = [model.tenant_id == tenant_id, *conditions]
        totals[kind] = await session.scalar(select(func.count()).select_from(model).where(*filters))
        rows = (await session.scalars(select(model).where(*filters).order_by(model.updated_at, model.id).limit(30))).all()
        for row in rows:
            key = f'{kind}:{row.id}'
            fingerprint = hashlib.sha256(f'{key}:{row.updated_at.isoformat()}'.encode()).hexdigest()
            alerts.append({'key': key, 'fingerprint': fingerprint, 'title': title,
                           'guidance': guidance, 'view': view, 'record_id': str(row.id),
                           'observed_at': row.updated_at.isoformat()})
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    settings = tenant.settings or {}
    bots = (await session.scalars(select(Bot).where(Bot.tenant_id == tenant_id,
        Bot.deleted_at.is_(None), Bot.is_enabled.is_(True)).order_by(Bot.id).limit(100))).all()
    observed = await BotFleetStateStore().read_many([bot.id for bot in bots])
    for bot in bots:
        status = observed.get(bot.id, {}).get('status', 'UNOBSERVED')
        if status != 'RUNNING':
            key = f'bot-runtime:{bot.id}'
            alerts.append({'key': key, 'fingerprint': hashlib.sha256(f'{key}:{status}'.encode()).hexdigest(),
                'title': 'Bot runtime is not observed running', 'guidance': 'Open Bots for connection status. Missing observations may mean a stopped runtime or unavailable monitoring; do not rotate credentials blindly.',
                'view': 'bots', 'record_id': str(bot.id), 'observed_at': now.isoformat()})
    totals['bot-runtime'] = sum(row['key'].startswith('bot-runtime:') for row in alerts)
    installation = settings.get('_installation_evidence', {})
    backup_time = installation.get('backup_completed_at')
    try:
        last_backup = datetime.fromisoformat(backup_time) if backup_time else None
        stale = not last_backup or not last_backup.tzinfo or last_backup < now - timedelta(days=7)
    except (TypeError, ValueError):
        stale = True
    if installation.get('backup_status') != 'COMPLETED' or stale:
        key = 'backup-evidence'
        alerts.append({'key': key, 'fingerprint': hashlib.sha256(f"{key}:{installation.get('version', 0)}".encode()).hexdigest(),
            'title': 'Backup evidence needs attention', 'guidance': 'No recent successful database and vault backup is recorded, or the last reported backup failed. Record evidence in Store settings. A recorded backup is not a restore drill.',
            'view': 'store-settings', 'record_id': 'installation', 'observed_at': now.isoformat()})
        totals[key] = 1
    acks = settings.get('_attention_acknowledgements', {})
    for alert in alerts:
        ack = acks.get(alert['key'], {})
        alert['acknowledged'] = (ack.get('fingerprint') == alert['fingerprint']
                                  and ack.get('until', '') > now.isoformat())
    async def count(model, *conditions):
        return await session.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id, *conditions))
    active_products = select(Product.id).where(Product.tenant_id == tenant_id, Product.deleted_at.is_(None), Product.is_active.is_(True))
    checks = [
        ('catalog', 'Active products', await count(Product, Product.deleted_at.is_(None), Product.is_active.is_(True)) > 0, 'products'),
        ('variants', 'Active product variants', await session.scalar(select(func.count()).select_from(ProductVariant).where(ProductVariant.deleted_at.is_(None), ProductVariant.is_active.is_(True), ProductVariant.product_id.in_(active_products))) > 0, 'products'),
        ('payments', 'Enabled payment methods', await count(PaymentMethodConfig, PaymentMethodConfig.is_enabled.is_(True)) > 0, 'providers'),
        ('bot', 'Verified enabled bot', await count(Bot, Bot.deleted_at.is_(None), Bot.is_enabled.is_(True), Bot.credential_status == 'VERIFIED') > 0, 'bots'),
        ('help', 'Customer help or FAQ', bool(settings.get('support_url') or settings.get('faq')), 'store-settings'),
        ('terms', 'Store terms and privacy links', bool(settings.get('terms_url') and settings.get('privacy_url')), 'store-settings'),
    ]
    bot_count = await count(Bot, Bot.deleted_at.is_(None), Bot.is_enabled.is_(True))
    return {'collected_at': now.isoformat(), 'alerts': alerts, 'totals': totals,
            'truncated': any(value > 30 for value in totals.values()) or bot_count > 100,
            'checks': [{'key': key, 'label': label, 'configured': bool(done), 'view': view} for key, label, done, view in checks],
            'verification': 'not_assessed', 'acknowledgement_hours': 24}
