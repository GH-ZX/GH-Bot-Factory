import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStatus
from packages.fulfillment.models import FulfillmentAttempt, FulfillmentStatus
from packages.providers.clients.registry import ProviderClientRegistry, provider_registry
from packages.providers.models import Provider

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

    def __init__(self, registry: ProviderClientRegistry | None = None) -> None:
        self.registry = registry or provider_registry

    async def scan_and_reconcile_tenant(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
    ) -> list[ReconciliationDiscrepancy]:
        """Scans for stuck or unconfirmed fulfillment attempts in a tenant and resolves them."""
        stmt = (
            select(FulfillmentAttempt)
            .where(
                FulfillmentAttempt.tenant_id == tenant_id,
                FulfillmentAttempt.status.in_(
                    [FulfillmentStatus.PROCESSING, FulfillmentStatus.RETRYING]
                ),
            )
        )
        result = await session.execute(stmt)
        stuck_attempts = result.scalars().all()

        discrepancies: list[ReconciliationDiscrepancy] = []

        for attempt in stuck_attempts:
            order = await session.get(Order, attempt.order_id)
            if not order:
                continue

            # Case 1: External order was placed, but internal state remained PROCESSING
            if attempt.external_order_id and attempt.provider_id:
                provider_record = await session.get(Provider, attempt.provider_id)
                if not provider_record:
                    continue

                client = self.registry.get_client(
                    provider_type=provider_record.provider_type,
                    provider_name=provider_record.name,
                    provider_id=str(provider_record.id),
                )

                try:
                    check_res = await client.get_order(attempt.external_order_id)
                    if check_res.is_completed:
                        attempt.status = FulfillmentStatus.SUCCEEDED
                        if order.status != OrderStatus.FULFILLED:
                            order.transition_to(OrderStatus.FULFILLED)
                        await session.commit()

                        discrepancies.append(
                            ReconciliationDiscrepancy(
                                order_id=order.id,
                                order_number=order.order_number,
                                issue_type="OUT_OF_SYNC_RESOLVED",
                                details=f"External order {attempt.external_order_id} was completed upstream.",
                                action_taken="Synchronized internal attempt and marked order FULFILLED",
                            )
                        )
                    elif check_res.is_failed:
                        attempt.status = FulfillmentStatus.FAILED
                        attempt.error_classification = "UPSTREAM_FAILED_RECONCILED"
                        await session.commit()

                        discrepancies.append(
                            ReconciliationDiscrepancy(
                                order_id=order.id,
                                order_number=order.order_number,
                                issue_type="UPSTREAM_FAILED_RESOLVED",
                                details=f"External order {attempt.external_order_id} failed upstream.",
                                action_taken="Marked attempt FAILED for refund handling",
                            )
                        )
                except Exception as query_err:  # noqa: BLE001
                    logger.warning(
                        "Reconciliation query failed for external_order %s: %s",
                        attempt.external_order_id,
                        query_err,
                    )

            # Case 2: Stuck in PROCESSING without external order id
            elif not attempt.external_order_id:
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        order_id=order.id,
                        order_number=order.order_number,
                        issue_type="STUCK_PROCESSING_WITHOUT_EXTERNAL_ID",
                        details="Attempt is in PROCESSING state without recorded external order ID.",
                        action_taken="Flagged for manual inspection or worker retry",
                    )
                )

        return discrepancies
