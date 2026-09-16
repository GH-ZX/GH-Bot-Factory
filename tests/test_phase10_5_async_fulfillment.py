from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Order, OrderItem, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.fulfillment.models import FulfillmentStatus
from packages.fulfillment.reconciliation import ReconciliationService
from packages.fulfillment.reconciliation_worker import ProviderReconciliationWorker
from packages.fulfillment.service import FulfillmentService
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.interface import ProviderOrderRequest, ProviderOrderResponse
from packages.providers.models import Provider, ProviderProductMapping
from packages.providers.router import ProviderRouter
from packages.tenants.models import Tenant, User


class PendingProvider(MockProvider):
    async def create_order(self, request: ProviderOrderRequest) -> ProviderOrderResponse:
        existing = self.idempotency_map.get(request.idempotency_key)
        if existing is not None:
            data = self.orders[existing]
            return ProviderOrderResponse(
                external_order_id=existing,
                status=data["status"],
                cost=Decimal(str(data["cost"])),
                raw_data={"idempotent_replay": True, **data},
            )
        external_id = f"pending-{uuid.uuid4().hex[:8]}"
        record = {
            "external_order_id": external_id,
            "status": "PENDING",
            "cost": "1.25",
            "recipient": request.recipient,
            "idempotency_key": request.idempotency_key,
        }
        self.orders[external_id] = record
        self.idempotency_map[request.idempotency_key] = external_id
        return ProviderOrderResponse(
            external_order_id=external_id,
            status="PENDING",
            cost=Decimal("1.25"),
            raw_data=record,
        )


async def _setup_order(
    session: AsyncSession,
) -> tuple[Tenant, Order, Provider]:
    tenant = Tenant(name="Async Provider", slug=f"async-provider-{uuid.uuid4().hex[:7]}")
    user = User(username=f"async_{uuid.uuid4().hex[:8]}")
    session.add_all([tenant, user])
    await session.flush()
    product = Product(tenant_id=tenant.id, title="Async Product")
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku=f"ASYNC-{uuid.uuid4().hex[:6]}",
        title="Async Variant",
        price=Decimal("10.00"),
    )
    session.add(variant)
    await session.flush()
    provider = Provider(
        tenant_id=tenant.id,
        name="Pending Provider",
        slug=f"pending-provider-{uuid.uuid4().hex[:6]}",
        provider_type="MOCK",
    )
    session.add(provider)
    await session.flush()
    # Provider mappings are product-scoped with an optional variant refinement.
    session.add(
        ProviderProductMapping(
            tenant_id=tenant.id,
            provider_id=provider.id,
            product_id=product.id,
            product_variant_id=variant.id,
            external_product_id="mock-prod-stars-50",
            cost_price=Decimal("1.25"),
            cost_currency="USD",
        )
    )
    order = Order(
        tenant_id=tenant.id,
        user_id=user.id,
        order_number=f"ASYNC-{uuid.uuid4().hex[:8]}",
        status=OrderStatus.PAID,
        total_amount=Decimal("10.00"),
        currency="USD",
    )
    session.add(order)
    await session.flush()
    session.add(
        OrderItem(
            order_id=order.id,
            product_variant_id=variant.id,
            quantity=1,
            unit_price=Decimal("10.00"),
            total_price=Decimal("10.00"),
        )
    )
    await session.commit()
    return tenant, order, provider


@pytest.mark.asyncio
async def test_pending_provider_order_is_not_marked_fulfilled_or_refunded(
    db_session: AsyncSession,
) -> None:
    _, order, provider = await _setup_order(db_session)
    registry = ProviderClientRegistry()
    pending = PendingProvider(provider_name="Pending Provider")
    registry.register_singleton(str(provider.id), pending)
    service = FulfillmentService(router=ProviderRouter(registry=registry))

    attempt = await service.execute_order_fulfillment(
        db_session,
        order_id=order.id,
        recipient="buyer",
    )
    await db_session.refresh(order)
    assert attempt.status == FulfillmentStatus.PROCESSING
    assert attempt.external_order_id is not None
    assert attempt.response_payload["canonical_state"] == "PENDING"
    assert order.status == OrderStatus.PROCESSING


@pytest.mark.asyncio
async def test_pull_reconciliation_completes_pending_order_without_webhook(
    db_session: AsyncSession,
    db_session_factory,
) -> None:
    _, order, provider = await _setup_order(db_session)
    registry = ProviderClientRegistry()
    pending = PendingProvider(provider_name="Pending Provider")
    registry.register_singleton(str(provider.id), pending)
    router = ProviderRouter(registry=registry)
    service = FulfillmentService(router=router)
    attempt = await service.execute_order_fulfillment(
        db_session,
        order_id=order.id,
        recipient="buyer",
    )
    assert attempt.external_order_id is not None
    pending.orders[attempt.external_order_id]["status"] = "COMPLETED"

    reconciliation = ReconciliationService(registry=registry, provider_router=router)
    worker = ProviderReconciliationWorker(
        service=reconciliation,
        session_factory=db_session_factory,
        enabled=True,
        interval_seconds=15,
        batch_size=50,
    )
    processed = await worker.run_once()
    assert processed == 1

    await db_session.refresh(attempt)
    await db_session.refresh(order)
    assert attempt.status == FulfillmentStatus.SUCCEEDED
    assert attempt.response_payload["canonical_state"] == "COMPLETED"
    assert order.status == OrderStatus.FULFILLED
