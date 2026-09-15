import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStatus
from packages.fulfillment.models import FulfillmentAttempt, FulfillmentStatus
from packages.notifications.service import (
    NotificationEventType,
    NotificationPayload,
    NotificationService,
)
from packages.payments.service import CANONICAL_REFUND_TYPE, LedgerService
from packages.providers.clients.registry import ProviderClientRegistry, provider_registry
from packages.providers.models import Provider
from packages.providers.router import ProviderRouter

logger = logging.getLogger("fulfillment.reconciliation")


@dataclass
class ReconciliationDiscrepancy:
    order_id: uuid.UUID
    order_number: str
    issue_type: str  # e.g. STUCK_PROCESSING, UNKNOWN_PROVIDER_STATUS, OUT_OF_SYNC
    details: str
    action_taken: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ReconciliationService:
    """Detects and resolves discrepancies between internal order states and external provider statuses."""

    def __init__(
        self,
        registry: ProviderClientRegistry | None = None,
        notification_service: NotificationService | None = None,
        provider_router: ProviderRouter | None = None,
    ) -> None:
        self.registry = registry or provider_registry
        self.notifications = notification_service or NotificationService()
        self.provider_router = provider_router or ProviderRouter(registry=self.registry)

    async def _reconcile_attempt(
        self,
        session: AsyncSession,
        attempt: FulfillmentAttempt,
    ) -> list[ReconciliationDiscrepancy]:
        order = await session.get(Order, attempt.order_id)
        if not order or order.tenant_id != attempt.tenant_id:
            return []

        query_target = attempt.external_order_id or attempt.idempotency_key
        provider_id = attempt.provider_id
        if provider_id is None and attempt.request_payload and "provider_id" in attempt.request_payload:
            try:
                provider_id = uuid.UUID(str(attempt.request_payload["provider_id"]))
            except (TypeError, ValueError, AttributeError):
                provider_id = None

        provider_record = None
        if provider_id is not None:
            provider_record = (
                await session.execute(
                    select(Provider)
                    .where(
                        Provider.id == provider_id,
                        Provider.tenant_id == attempt.tenant_id,
                    )
                    .options(selectinload(Provider.credentials))
                )
            ).scalar_one_or_none()

        if query_target and provider_record:
            try:
                client = self.registry.get_client(
                    provider_type=provider_record.provider_type,
                    provider_name=provider_record.name,
                    config=await self.provider_router.build_provider_config(provider_record),
                    provider_id=str(provider_record.id),
                )
                check_res = await client.get_order(query_target)
                if check_res.is_completed:
                    attempt.status = FulfillmentStatus.SUCCEEDED
                    if check_res.external_order_id:
                        attempt.external_order_id = check_res.external_order_id
                    if order.status != OrderStatus.FULFILLED:
                        order.transition_to(OrderStatus.FULFILLED)
                    await session.commit()

                    await self.notifications.notify(
                        NotificationPayload(
                            event_type=NotificationEventType.FULFILLMENT_SUCCEEDED,
                            tenant_id=order.tenant_id,
                            recipient="customer",
                            order_id=order.id,
                            order_number=order.order_number,
                            message=f"🎉 Order #{order.order_number} verified and completed successfully!",
                            metadata={"external_order_id": attempt.external_order_id},
                        )
                    )

                    return [
                        ReconciliationDiscrepancy(
                            order_id=order.id,
                            order_number=order.order_number,
                            issue_type="OUT_OF_SYNC_RESOLVED",
                            details=f"External order {query_target} was completed upstream.",
                            action_taken="Synchronized internal attempt and marked order FULFILLED",
                        )
                    ]

                if check_res.is_failed:
                    attempt.status = FulfillmentStatus.FAILED
                    attempt.error_classification = "UPSTREAM_FAILED_RECONCILED"
                    order.transition_to(OrderStatus.FAILED)

                    wallet = await LedgerService.get_or_create_wallet(
                        session=session,
                        tenant_id=order.tenant_id,
                        user_id=order.user_id,
                        currency=order.currency,
                    )
                    await LedgerService.refund(
                        session=session,
                        wallet=wallet,
                        amount=order.total_amount,
                        reference_id=str(order.id),
                        reference_type=CANONICAL_REFUND_TYPE,
                        description=f"Automated refund via reconciliation for failed order #{order.order_number}",
                    )
                    await session.commit()

                    await self.notifications.notify(
                        NotificationPayload(
                            event_type=NotificationEventType.ORDER_REFUNDED,
                            tenant_id=order.tenant_id,
                            recipient="customer",
                            order_id=order.id,
                            order_number=order.order_number,
                            message=f"⚠️ Order #{order.order_number} failed upstream. Your balance has been refunded.",
                            metadata={"order_number": order.order_number},
                        )
                    )

                    return [
                        ReconciliationDiscrepancy(
                            order_id=order.id,
                            order_number=order.order_number,
                            issue_type="UPSTREAM_FAILED_RESOLVED",
                            details=f"External order {query_target} failed upstream.",
                            action_taken="Marked attempt FAILED and executed automated ledger refund",
                        )
                    ]

                return [
                    ReconciliationDiscrepancy(
                        order_id=order.id,
                        order_number=order.order_number,
                        issue_type="UPSTREAM_STILL_PENDING",
                        details=f"External order {query_target} is still pending upstream.",
                        action_taken="No mutation; retained current internal state",
                    )
                ]
            except Exception as query_err:  # noqa: BLE001
                logger.warning(
                    "Reconciliation query failed for query_target %s: %s",
                    query_target,
                    query_err,
                )
                return [
                    ReconciliationDiscrepancy(
                        order_id=order.id,
                        order_number=order.order_number,
                        issue_type="PROVIDER_QUERY_FAILED",
                        details="Provider status query failed during reconciliation.",
                        action_taken="No mutation; operator review required",
                        metadata={"error_classification": type(query_err).__name__},
                    )
                ]

        if not attempt.external_order_id:
            return [
                ReconciliationDiscrepancy(
                    order_id=order.id,
                    order_number=order.order_number,
                    issue_type="STUCK_PROCESSING_WITHOUT_EXTERNAL_ID",
                    details="Attempt is active without a recorded external order ID or queryable provider.",
                    action_taken="Flagged for manual inspection or safe requeue evaluation",
                )
            ]
        return []

    async def reconcile_order(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        order_id: uuid.UUID,
    ) -> list[ReconciliationDiscrepancy]:
        """Reconcile one tenant-scoped order without scanning or mutating unrelated orders."""
        order = await session.get(Order, order_id)
        if order is None or order.tenant_id != tenant_id:
            return []
        stmt = (
            select(FulfillmentAttempt)
            .where(
                FulfillmentAttempt.tenant_id == tenant_id,
                FulfillmentAttempt.order_id == order_id,
                FulfillmentAttempt.status.in_(
                    [
                        FulfillmentStatus.PROCESSING,
                        FulfillmentStatus.RETRYING,
                        FulfillmentStatus.UNKNOWN,
                    ]
                ),
            )
            .order_by(FulfillmentAttempt.attempt_number.desc(), FulfillmentAttempt.created_at.desc())
            .limit(1)
        )
        attempt = (await session.execute(stmt)).scalar_one_or_none()
        if attempt is None:
            return []
        return await self._reconcile_attempt(session, attempt)

    async def scan_and_reconcile_tenant(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
    ) -> list[ReconciliationDiscrepancy]:
        """Scans for stuck or unconfirmed fulfillment attempts in a tenant and resolves them."""
        stmt = select(FulfillmentAttempt).where(
            FulfillmentAttempt.tenant_id == tenant_id,
            FulfillmentAttempt.status.in_(
                [
                    FulfillmentStatus.PROCESSING,
                    FulfillmentStatus.RETRYING,
                    FulfillmentStatus.UNKNOWN,
                ]
            ),
        )
        stuck_attempts = list((await session.execute(stmt)).scalars().all())
        discrepancies: list[ReconciliationDiscrepancy] = []
        for attempt in stuck_attempts:
            discrepancies.extend(await self._reconcile_attempt(session, attempt))
        return discrepancies
