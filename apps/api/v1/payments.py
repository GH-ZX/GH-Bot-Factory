import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_principal, require_staff_or_above
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.core.exceptions import (
    InsufficientFundsError,
    InvalidStateTransitionError,
    TenantAccessViolationError,
)
from packages.payments.exceptions import (
    PaymentError,
    PaymentIntegrityError,
    PaymentIntentNotFoundError,
    WebhookVerificationError,
)
from packages.payments.models import (
    PaymentIntent,
    PaymentReconciliationEvent,
    WalletTopUpReversal,
)
from packages.payments.payment_service import PaymentService
from packages.payments.reconciliation import PaymentReconciliationService

router = APIRouter(prefix="/payments", tags=["payments"])


class CreatePaymentIntentRequest(BaseModel):
    order_id: uuid.UUID
    provider_name: str
    idempotency_key: str
    metadata: dict[str, Any] | None = None
    return_url: str | None = None


class PaymentIntentResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    order_id: uuid.UUID | None
    user_id: uuid.UUID
    purpose: str
    provider: str
    provider_payment_id: str | None
    checkout_url: str | None
    currency: str
    amount: Decimal
    status: str
    idempotency_key: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, intent: PaymentIntent) -> "PaymentIntentResponse":
        return cls(
            id=intent.id,
            tenant_id=intent.tenant_id,
            order_id=intent.order_id,
            user_id=intent.user_id,
            purpose=intent.purpose.value,
            provider=intent.provider,
            provider_payment_id=intent.provider_payment_id,
            checkout_url=intent.checkout_url,
            currency=intent.currency,
            amount=intent.amount,
            status=intent.status.value,
            idempotency_key=intent.idempotency_key,
            created_at=intent.created_at,
            updated_at=intent.updated_at,
        )




class TopUpReversalRequest(BaseModel):
    idempotency_key: str
    reason: str | None = None


class TopUpReversalResponse(BaseModel):
    id: uuid.UUID
    payment_intent_id: uuid.UUID
    user_id: uuid.UUID
    provider: str
    provider_refund_id: str | None
    amount: Decimal
    currency: str
    status: str
    attempts: int
    next_attempt_at: datetime | None
    completed_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, reversal: WalletTopUpReversal) -> "TopUpReversalResponse":
        return cls(
            id=reversal.id,
            payment_intent_id=reversal.payment_intent_id,
            user_id=reversal.user_id,
            provider=reversal.provider,
            provider_refund_id=reversal.provider_refund_id,
            amount=reversal.amount,
            currency=reversal.currency,
            status=reversal.status.value,
            attempts=reversal.attempts,
            next_attempt_at=reversal.next_attempt_at,
            completed_at=reversal.completed_at,
            last_error_code=reversal.last_error_code,
            created_at=reversal.created_at,
            updated_at=reversal.updated_at,
        )



class ReconciliationEventResponse(BaseModel):
    id: uuid.UUID
    provider: str
    provider_event_id: str
    event_type: str
    payment_intent_id: uuid.UUID | None
    status: str
    amount: Decimal
    currency: str
    classification: str
    requires_review: bool
    occurred_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, event: PaymentReconciliationEvent) -> "ReconciliationEventResponse":
        return cls(
            id=event.id,
            provider=event.provider,
            provider_event_id=event.provider_event_id,
            event_type=event.event_type,
            payment_intent_id=event.payment_intent_id,
            status=event.status.value,
            amount=event.amount,
            currency=event.currency,
            classification=event.classification,
            requires_review=event.requires_review,
            occurred_at=event.occurred_at,
            created_at=event.created_at,
        )

def get_payment_service() -> PaymentService:
    return PaymentService()


def get_reconciliation_service(
    payment_service: PaymentService = Depends(get_payment_service),
) -> PaymentReconciliationService:
    return PaymentReconciliationService(payment_service=payment_service)


@router.post("/intents", response_model=PaymentIntentResponse, status_code=status.HTTP_201_CREATED)
async def create_payment_intent(
    req: CreatePaymentIntentRequest,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    payment_service: PaymentService = Depends(get_payment_service),
) -> PaymentIntentResponse:
    """Create a durable PaymentIntent derived strictly from server-authoritative Order amount.

    Identity is derived authoritatively from the verified AuthenticatedPrincipal.
    """
    try:
        intent = await payment_service.create_payment_intent(
            session=session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            order_id=req.order_id,
            provider_name=req.provider_name,
            idempotency_key=req.idempotency_key,
            metadata=req.metadata,
            return_url=req.return_url,
        )
        return PaymentIntentResponse.from_model(intent)
    except PaymentIntentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PaymentIntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except TenantAccessViolationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except PaymentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/intents/{intent_id}", response_model=PaymentIntentResponse)
async def get_payment_intent(
    intent_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    payment_service: PaymentService = Depends(get_payment_service),
) -> PaymentIntentResponse:
    """Fetch an authoritative PaymentIntent scoped to tenant and user permissions."""
    try:
        intent = await payment_service.get_payment_intent(session, principal.tenant_id, intent_id)

        # Customer role can only access their own payment intents
        if principal.is_customer() and intent.user_id != principal.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Customer cannot access another user's payment intent.",
            )

        return PaymentIntentResponse.from_model(intent)
    except PaymentIntentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TenantAccessViolationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post("/intents/{intent_id}/cancel", response_model=PaymentIntentResponse)
@router.post("/{intent_id}/cancel", response_model=PaymentIntentResponse)
async def cancel_payment_intent(
    intent_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    payment_service: PaymentService = Depends(get_payment_service),
) -> PaymentIntentResponse:
    """Cancel an active non-terminal PaymentIntent."""
    try:
        intent = await payment_service.get_payment_intent(session, principal.tenant_id, intent_id)

        # Customer role can only cancel their own payment intents
        if principal.is_customer() and intent.user_id != principal.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Customer cannot cancel another user's payment intent.",
            )

        cancelled_intent = await payment_service.cancel_payment_intent(
            session, principal.tenant_id, intent_id
        )
        return PaymentIntentResponse.from_model(cancelled_intent)
    except PaymentIntentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except TenantAccessViolationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post("/intents/{intent_id}/reconcile", response_model=PaymentIntentResponse)
@router.post("/{intent_id}/reconcile", response_model=PaymentIntentResponse)
async def reconcile_payment_intent(
    intent_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    reconciliation_service: PaymentReconciliationService = Depends(get_reconciliation_service),
    payment_service: PaymentService = Depends(get_payment_service),
) -> PaymentIntentResponse:
    """Reconcile an uncertain payment against the upstream provider."""
    try:
        intent = await payment_service.get_payment_intent(session, principal.tenant_id, intent_id)

        # Customer role can only reconcile their own payment intents
        if principal.is_customer() and intent.user_id != principal.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Customer cannot reconcile another user's payment intent.",
            )

        reconciled_intent = await reconciliation_service.reconcile_intent(
            session, principal.tenant_id, intent_id
        )
        return PaymentIntentResponse.from_model(reconciled_intent)
    except PaymentIntentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TenantAccessViolationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except PaymentIntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc




@router.post(
    "/intents/{intent_id}/topup-reversal",
    response_model=TopUpReversalResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_topup_reversal(
    intent_id: uuid.UUID,
    req: TopUpReversalRequest,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
    payment_service: PaymentService = Depends(get_payment_service),
) -> TopUpReversalResponse:
    """Reserve customer wallet funds and enqueue a durable full top-up refund saga."""
    if len(req.idempotency_key.strip()) < 8 or len(req.idempotency_key) > 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="idempotency_key must contain 8-100 characters.",
        )
    try:
        reversal = await payment_service.request_wallet_topup_reversal(
            session=session,
            tenant_id=principal.tenant_id,
            intent_id=intent_id,
            idempotency_key=req.idempotency_key.strip(),
            reason=req.reason,
            requested_by_user_id=principal.user_id,
        )
        return TopUpReversalResponse.from_model(reversal)
    except PaymentIntentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InsufficientFundsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PaymentIntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PaymentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/topup-reversals/{reversal_id}", response_model=TopUpReversalResponse)
async def get_topup_reversal(
    reversal_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> TopUpReversalResponse:
    reversal = await session.get(WalletTopUpReversal, reversal_id)
    if reversal is None or reversal.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Top-up reversal not found.")
    return TopUpReversalResponse.from_model(reversal)


@router.post(
    "/topup-reversals/{reversal_id}/resolve-external",
    response_model=TopUpReversalResponse,
)
async def resolve_external_topup_reversal(
    reversal_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
    payment_service: PaymentService = Depends(get_payment_service),
) -> TopUpReversalResponse:
    """Retry the local wallet debit after an externally-originated reversal froze the wallet."""
    try:
        reversal = await payment_service.resolve_external_wallet_topup_reversal(
            session=session,
            tenant_id=principal.tenant_id,
            reversal_id=reversal_id,
        )
        return TopUpReversalResponse.from_model(reversal)
    except PaymentIntentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InsufficientFundsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PaymentIntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PaymentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/reconciliation-events", response_model=list[ReconciliationEventResponse])
async def list_reconciliation_events(
    requires_review: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[ReconciliationEventResponse]:
    """Return tenant-scoped provider reconciliation observations for operator audit."""
    stmt = select(PaymentReconciliationEvent).where(
        PaymentReconciliationEvent.tenant_id == principal.tenant_id
    )
    if requires_review is not None:
        stmt = stmt.where(PaymentReconciliationEvent.requires_review.is_(requires_review))
    stmt = stmt.order_by(PaymentReconciliationEvent.created_at.desc()).limit(limit)
    events = list((await session.execute(stmt)).scalars().all())
    return [ReconciliationEventResponse.from_model(event) for event in events]


@router.post("/webhooks/{tenant_id}/{provider_name}")
async def handle_payment_webhook_with_path_tenant(
    tenant_id: uuid.UUID,
    provider_name: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    payment_service: PaymentService = Depends(get_payment_service),
) -> dict[str, Any]:
    """Ingest, verify, and process provider webhooks with tenant specified in URL path.

    The tenant_id is treated as a candidate tenant identifier and must be cryptographically
    verified against that tenant's configured provider webhook secret.
    """
    return await _process_webhook_common(
        tenant_id=tenant_id,
        provider_name=provider_name,
        request=request,
        session=session,
        payment_service=payment_service,
    )


@router.post("/webhooks/{provider_name}")
async def handle_payment_webhook(
    provider_name: str,
    request: Request,
    x_tenant_id: uuid.UUID | None = Header(None, alias="X-Tenant-ID"),
    tenant_id: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
    payment_service: PaymentService = Depends(get_payment_service),
) -> dict[str, Any]:
    """Ingest, verify, and process provider webhooks with candidate tenant resolution.

    Candidate tenant is cryptographically verified against the provider secret before any state mutation.
    """
    candidate_tenant_id = tenant_id or x_tenant_id
    if candidate_tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing tenant identification for webhook processing.",
        )

    return await _process_webhook_common(
        tenant_id=candidate_tenant_id,
        provider_name=provider_name,
        request=request,
        session=session,
        payment_service=payment_service,
    )


async def _process_webhook_common(
    tenant_id: uuid.UUID,
    provider_name: str,
    request: Request,
    session: AsyncSession,
    payment_service: PaymentService,
) -> dict[str, Any]:
    payload_bytes = await request.body()
    headers_dict = dict(request.headers)

    try:
        event = await payment_service.process_webhook(
            session=session,
            tenant_id=tenant_id,
            provider_name=provider_name,
            payload_bytes=payload_bytes,
            headers=headers_dict,
        )
        return {
            "status": "ok",
            "event_id": str(event.id),
            "processed": event.processed,
        }
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except PaymentIntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except TenantAccessViolationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except PaymentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
