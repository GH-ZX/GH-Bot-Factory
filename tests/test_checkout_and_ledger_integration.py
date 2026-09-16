from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.checkout import CheckoutService
from packages.commerce.economics_models import OrderItemEconomics
from packages.commerce.models import Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.core.exceptions import InsufficientFundsError
from packages.fulfillment.service import FulfillmentService
from packages.notifications.service import (
    MockNotificationTransport,
    NotificationEventType,
    NotificationService,
)
from packages.payments.service import LedgerService
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.exceptions import ProviderError
from packages.providers.models import Provider, ProviderProductMapping
from packages.providers.router import ProviderRouter
from packages.tenants.models import Tenant, User


@pytest.mark.asyncio
async def test_successful_checkout_and_ledger_integration(db_session: AsyncSession):
    tenant = Tenant(name="Checkout Store", slug="chk-store")
    user = User(username="shopper_1")
    db_session.add_all([tenant, user])
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Pro Subscription")
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(product_id=product.id, sku="PRO-1M", title="Pro 1 Month", price=Decimal("30.00"), currency="USD")
    db_session.add(variant)
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="SubVendor", slug="sub-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    mapping = ProviderProductMapping(tenant_id=tenant.id, provider_id=prov.id, product_id=product.id, product_variant_id=variant.id, external_product_id="ext-pro")
    db_session.add(mapping)

    # 1. Deposit 100.00 into User's wallet
    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    await LedgerService.credit(db_session, wallet, Decimal("100.00"), description="Deposit")
    await db_session.commit()

    # 2. Setup Notification Transport
    mock_transport = MockNotificationTransport()
    notif_service = NotificationService(transports=[mock_transport])
    checkout_service = CheckoutService(notification_service=notif_service)

    # 3. Execute Checkout for 1 item (30.00 USD)
    order, attempt = await checkout_service.checkout(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        variant_id=variant.id,
        quantity=1,
        recipient="@subscriber",
        execute_sync=True,
    )

    # Assertions
    assert order.status == OrderStatus.FULFILLED
    assert attempt is not None
    assert attempt.external_order_id is not None

    # Wallet balance must be debited to 70.00
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("70.00")

    # Double-entry ledger reconciliation must be 100% accurate
    reconstructed, is_valid = await LedgerService.reconstruct_and_verify_balance(db_session, wallet.id, tenant.id)
    assert is_valid is True
    assert reconstructed == Decimal("70.00")

    # Notifications received
    events = [n.event_type for n in mock_transport.get_events_for_order(order.id)]
    assert NotificationEventType.PAYMENT_CONFIRMED in events
    assert NotificationEventType.FULFILLMENT_SUCCEEDED in events

    # Phase 12: fulfillment must attribute the actual upstream cost to the immutable sale economics.
    economics = (
        await db_session.execute(
            select(OrderItemEconomics).where(OrderItemEconomics.order_id == order.id)
        )
    ).scalar_one()
    assert economics.provider_id == prov.id
    assert economics.actual_supplier_cost == Decimal("1.250000")
    assert economics.actual_cost_currency == "USD"
    assert economics.gross_profit == Decimal("28.750000")


@pytest.mark.asyncio
async def test_checkout_insufficient_funds_fails_cleanly(db_session: AsyncSession):
    tenant = Tenant(name="Poor Store", slug="poor-store")
    user = User(username="poor_shopper")
    db_session.add_all([tenant, user])
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Expensive Item")
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(product_id=product.id, sku="EXP-1", title="Expensive Item", price=Decimal("1000.00"), currency="USD")
    db_session.add(variant)
    await db_session.commit()

    checkout_service = CheckoutService()

    # Wallet has 0.00; attempt to purchase 1000.00 item
    with pytest.raises(InsufficientFundsError):
        await checkout_service.checkout(
            session=db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            variant_id=variant.id,
            quantity=1,
            recipient="@buyer",
        )


@pytest.mark.asyncio
async def test_failed_fulfillment_triggers_automated_ledger_refund(db_session: AsyncSession):
    tenant = Tenant(name="Refund Store", slug="ref-store")
    user = User(username="ref_shopper")
    db_session.add_all([tenant, user])
    await db_session.flush()

    product = Product(tenant_id=tenant.id, title="Item to Fail")
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(product_id=product.id, sku="FAIL-1", title="Fail Item", price=Decimal("40.00"), currency="USD")
    db_session.add(variant)
    await db_session.flush()

    prov = Provider(tenant_id=tenant.id, name="BrokenVendor", slug="broken-vendor", provider_type="MOCK")
    db_session.add(prov)
    await db_session.flush()

    mapping = ProviderProductMapping(tenant_id=tenant.id, provider_id=prov.id, product_id=product.id, product_variant_id=variant.id, external_product_id="ext-fail")
    db_session.add(mapping)

    # Deposit 50.00 into wallet
    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, currency="USD")
    await LedgerService.credit(db_session, wallet, Decimal("50.00"), description="Deposit")
    await db_session.commit()

    # Configure provider to fail permanently with non-retryable error
    mock_client = MockProvider(provider_name="BrokenVendor")
    mock_client.fail_with_auth_error = True  # Non-retryable!

    registry = ProviderClientRegistry()
    registry.register_singleton(str(prov.id), mock_client)

    router = ProviderRouter(registry=registry)
    fulfillment_service = FulfillmentService(router=router)
    checkout_service = CheckoutService(fulfillment_service=fulfillment_service)

    with pytest.raises(ProviderError):
        await checkout_service.checkout(
            session=db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            variant_id=variant.id,
            quantity=1,
            recipient="@buyer",
            execute_sync=True,
        )

    # Verify that the customer's wallet was debited then automatically refunded back to 50.00!
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("50.00")

    # Verify ledger history has: CREDIT 50 -> DEBIT 40 -> REFUND 40
    reconstructed, is_valid = await LedgerService.reconstruct_and_verify_balance(db_session, wallet.id, tenant.id)
    assert is_valid is True
    assert reconstructed == Decimal("50.00")
