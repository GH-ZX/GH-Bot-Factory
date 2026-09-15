import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStatus
from packages.fulfillment.models import (
    FulfillmentAttempt,
    FulfillmentJobRecord,
    FulfillmentJobStatus,
    FulfillmentStatus,
)
from packages.payments.models import LedgerTransaction, TransactionType
from packages.payments.service import CANONICAL_REFUND_TYPE


class FulfillmentOperationError(RuntimeError):
    """Raised when an operator action would violate fulfillment safety invariants."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RequeueDecision:
    allowed: bool
    reason_code: str
    reason: str
    next_attempt_number: int | None = None


async def latest_fulfillment_attempt(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    order_id: uuid.UUID,
) -> FulfillmentAttempt | None:
    stmt = (
        select(FulfillmentAttempt)
        .where(
            FulfillmentAttempt.tenant_id == tenant_id,
            FulfillmentAttempt.order_id == order_id,
        )
        .order_by(FulfillmentAttempt.attempt_number.desc(), FulfillmentAttempt.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def order_has_fulfillment_refund(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    order_id: uuid.UUID,
) -> bool:
    stmt = select(LedgerTransaction.id).where(
        LedgerTransaction.tenant_id == tenant_id,
        LedgerTransaction.transaction_type == TransactionType.REFUND,
        LedgerTransaction.reference_type == CANONICAL_REFUND_TYPE,
        LedgerTransaction.reference_id == str(order_id),
    )
    return (await session.execute(stmt)).scalars().first() is not None


async def evaluate_manual_requeue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job: FulfillmentJobRecord,
) -> RequeueDecision:
    """Prove a dead-letter retry is safe enough to re-enter the worker queue.

    Manual requeue is deliberately conservative. UNKNOWN attempts and attempts that
    have an upstream external order ID must reconcile first because retrying them
    could purchase twice. Orders already financially compensated must never be
    re-fulfilled without a separate recovery workflow.
    """
    if job.tenant_id != tenant_id:
        return RequeueDecision(False, "TENANT_MISMATCH", "Fulfillment job is outside this tenant.")
    if job.status != FulfillmentJobStatus.DEAD_LETTER:
        return RequeueDecision(False, "JOB_NOT_DEAD_LETTER", "Only dead-letter jobs can be manually requeued.")

    order = await session.get(Order, job.order_id)
    if order is None or order.tenant_id != tenant_id:
        return RequeueDecision(False, "ORDER_NOT_FOUND", "Order is not available in this tenant.")
    if order.status not in {OrderStatus.PAID, OrderStatus.PROCESSING}:
        return RequeueDecision(
            False,
            "ORDER_TERMINAL_OR_UNSAFE",
            f"Order status {order.status.value} cannot be manually requeued.",
        )
    if await order_has_fulfillment_refund(session, tenant_id=tenant_id, order_id=order.id):
        return RequeueDecision(
            False,
            "ORDER_ALREADY_REFUNDED",
            "Order already has a canonical fulfillment refund and cannot be requeued.",
        )

    latest = await latest_fulfillment_attempt(session, tenant_id=tenant_id, order_id=order.id)
    if latest is not None:
        if latest.external_order_id:
            return RequeueDecision(
                False,
                "EXTERNAL_ORDER_EXISTS",
                "An upstream order ID exists. Reconcile provider state before any retry.",
            )
        if latest.status == FulfillmentStatus.UNKNOWN:
            return RequeueDecision(
                False,
                "ATTEMPT_STATUS_UNKNOWN",
                "Latest provider outcome is UNKNOWN. Reconciliation is required before retry.",
            )
        if latest.status in {FulfillmentStatus.SUCCEEDED, FulfillmentStatus.PROCESSING}:
            return RequeueDecision(
                False,
                "ATTEMPT_ACTIVE_OR_SUCCEEDED",
                f"Latest attempt is {latest.status.value} and must not be replayed.",
            )
        if latest.status == FulfillmentStatus.FAILED:
            retryable = bool((latest.response_payload or {}).get("retryable"))
            if not retryable:
                return RequeueDecision(
                    False,
                    "PERMANENT_PROVIDER_FAILURE",
                    "Latest failure was classified as permanent; fix or reconcile it instead of replaying.",
                )

    next_attempt = max(job.attempt_number, latest.attempt_number if latest else 0) + 1
    return RequeueDecision(
        True,
        "SAFE_TO_REQUEUE",
        "No upstream success/unknown outcome or refund was found.",
        next_attempt_number=next_attempt,
    )


async def requeue_dead_letter_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> tuple[FulfillmentJobRecord, RequeueDecision]:
    job = (
        await session.execute(
            select(FulfillmentJobRecord).where(
                FulfillmentJobRecord.id == job_id,
                FulfillmentJobRecord.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise FulfillmentOperationError("JOB_NOT_FOUND", "Fulfillment job not found.")

    decision = await evaluate_manual_requeue(session, tenant_id=tenant_id, job=job)
    if not decision.allowed or decision.next_attempt_number is None:
        raise FulfillmentOperationError(decision.reason_code, decision.reason)

    now = datetime.now(UTC)
    stmt = (
        update(FulfillmentJobRecord)
        .where(
            FulfillmentJobRecord.id == job.id,
            FulfillmentJobRecord.tenant_id == tenant_id,
            FulfillmentJobRecord.status == FulfillmentJobStatus.DEAD_LETTER,
        )
        .values(
            status=FulfillmentJobStatus.QUEUED,
            attempt_number=decision.next_attempt_number,
            locked_at=None,
            completed_at=None,
            last_error=None,
            failure_classification=None,
            manual_requeue_count=FulfillmentJobRecord.manual_requeue_count + 1,
            last_requeued_at=now,
            last_requeued_by=actor_user_id,
        )
    )
    result = await session.execute(stmt)
    if not result.rowcount:
        raise FulfillmentOperationError(
            "JOB_ALREADY_CHANGED",
            "Job was already requeued or changed by another operator/worker.",
        )
    await session.flush()
    refreshed = await session.get(FulfillmentJobRecord, job.id)
    assert refreshed is not None
    return refreshed, decision
