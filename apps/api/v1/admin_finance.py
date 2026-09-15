import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import require_admin_or_owner, require_staff_or_above
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.core.exceptions import InsufficientFundsError, TenantAccessViolationError
from packages.payments.exceptions import PaymentError, PaymentIntegrityError
from packages.payments.models import (
    FinancialResolutionCase,
    FinancialResolutionCaseStatus,
    Wallet,
)
from packages.payments.payment_service import PaymentService
from packages.payments.resolution import FinancialResolutionAction, FinancialResolutionService
from packages.tenants.models import AuditLog, Role

router = APIRouter(prefix="/admin/financial-resolution", tags=["admin-financial-resolution"])


class FinancialCaseResponse(BaseModel):
    id: uuid.UUID
    case_type: str
    severity: str
    status: FinancialResolutionCaseStatus
    version: int
    payment_intent_id: uuid.UUID | None
    wallet_id: uuid.UUID | None
    wallet_balance: Decimal | None
    wallet_currency: str | None
    wallet_active: bool | None
    reversal_id: uuid.UUID | None
    reversal_status: str | None
    reversal_error_code: str | None
    reversal_error_detail: str | None
    reconciliation_event_id: uuid.UUID | None
    provider: str | None
    provider_event_id: str | None
    event_classification: str | None
    amount: Decimal | None
    currency: str | None
    assigned_to_user_id: uuid.UUID | None
    resolution_code: str | None
    resolution_note: str | None
    resolved_by_user_id: uuid.UUID | None
    resolved_at: datetime | None
    available_actions: list[FinancialResolutionAction]
    created_at: datetime
    updated_at: datetime


class FinancialCaseListResponse(BaseModel):
    cases: list[FinancialCaseResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


class FinancialCaseVersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class FinancialCaseResolveRequest(BaseModel):
    expected_version: int = Field(ge=1)
    action: FinancialResolutionAction
    note: str = Field(min_length=12, max_length=1000)


def get_financial_resolution_service() -> FinancialResolutionService:
    return FinancialResolutionService()


def get_payment_service() -> PaymentService:
    return PaymentService()


def _available_actions(
    case: FinancialResolutionCase,
    *,
    actor_is_owner: bool,
) -> list[FinancialResolutionAction]:
    if case.status == FinancialResolutionCaseStatus.RESOLVED:
        return []
    actions: list[FinancialResolutionAction] = []
    reversal = case.reversal
    wallet = case.wallet
    if (
        reversal is not None
        and reversal.status.value == "MANUAL_REVIEW"
        and (reversal.metadata_json or {}).get("origin") == "EXTERNAL_PROVIDER"
    ):
        actions.append(FinancialResolutionAction.RETRY_LOCAL_REVERSAL)
    if case.wallet_id is None and case.reversal_id is None:
        actions.append(FinancialResolutionAction.ACKNOWLEDGE_NO_WALLET_IMPACT)
    if wallet is not None and not wallet.is_active:
        actions.append(FinancialResolutionAction.CLOSE_KEEP_WALLET_FROZEN)
        if actor_is_owner and case.reversal_id is None:
            actions.append(FinancialResolutionAction.MARK_FALSE_POSITIVE_AND_UNFREEZE)
    return actions


def _case_response(
    case: FinancialResolutionCase,
    *,
    actor_is_owner: bool,
) -> FinancialCaseResponse:
    wallet = case.wallet
    reversal = case.reversal
    event = case.reconciliation_event
    return FinancialCaseResponse(
        id=case.id,
        case_type=case.case_type,
        severity=case.severity,
        status=case.status,
        version=case.version,
        payment_intent_id=case.payment_intent_id,
        wallet_id=case.wallet_id,
        wallet_balance=wallet.balance if wallet is not None else None,
        wallet_currency=wallet.currency if wallet is not None else None,
        wallet_active=wallet.is_active if wallet is not None else None,
        reversal_id=case.reversal_id,
        reversal_status=reversal.status.value if reversal is not None else None,
        reversal_error_code=reversal.last_error_code if reversal is not None else None,
        reversal_error_detail=reversal.last_error_detail if reversal is not None else None,
        reconciliation_event_id=case.reconciliation_event_id,
        provider=event.provider if event is not None else (reversal.provider if reversal is not None else None),
        provider_event_id=event.provider_event_id if event is not None else None,
        event_classification=event.classification if event is not None else None,
        amount=event.amount if event is not None else (reversal.amount if reversal is not None else None),
        currency=event.currency if event is not None else (reversal.currency if reversal is not None else None),
        assigned_to_user_id=case.assigned_to_user_id,
        resolution_code=case.resolution_code,
        resolution_note=case.resolution_note,
        resolved_by_user_id=case.resolved_by_user_id,
        resolved_at=case.resolved_at,
        available_actions=_available_actions(case, actor_is_owner=actor_is_owner),
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


async def _load_case(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
) -> FinancialResolutionCase:
    stmt = (
        select(FinancialResolutionCase)
        .where(
            FinancialResolutionCase.id == case_id,
            FinancialResolutionCase.tenant_id == tenant_id,
        )
        .options(
            selectinload(FinancialResolutionCase.wallet),
            selectinload(FinancialResolutionCase.reversal),
            selectinload(FinancialResolutionCase.reconciliation_event),
        )
    )
    case = (await session.execute(stmt)).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Financial case not found.")
    return case


async def _audit(
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    *,
    action: str,
    case_id: uuid.UUID,
    details: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditLog(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action=action,
            resource_type="financial_resolution_case",
            resource_id=str(case_id),
            details=details or {},
        )
    )


@router.get("/cases", response_model=FinancialCaseListResponse)
async def list_financial_cases(
    case_status: FinancialResolutionCaseStatus | None = Query(None, alias="status"),
    severity: str | None = Query(None, max_length=20),
    q: str | None = Query(None, max_length=100),
    assigned: str | None = Query(None, pattern=r"^(mine|unassigned)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> FinancialCaseListResponse:
    filters = [FinancialResolutionCase.tenant_id == principal.tenant_id]
    if case_status is not None:
        filters.append(FinancialResolutionCase.status == case_status)
    if severity:
        filters.append(FinancialResolutionCase.severity == severity.strip().upper())
    if assigned == "mine":
        filters.append(FinancialResolutionCase.assigned_to_user_id == principal.user_id)
    elif assigned == "unassigned":
        filters.append(FinancialResolutionCase.assigned_to_user_id.is_(None))
    if q and q.strip():
        pattern = f"%{q.strip().lower()}%"
        filters.append(
            or_(
                func.lower(FinancialResolutionCase.case_type).like(pattern),
                func.lower(FinancialResolutionCase.source_key).like(pattern),
                func.lower(FinancialResolutionCase.resolution_code).like(pattern),
            )
        )

    total = int(
        (
            await session.scalar(
                select(func.count()).select_from(FinancialResolutionCase).where(*filters)
            )
        )
        or 0
    )
    stmt = (
        select(FinancialResolutionCase)
        .where(*filters)
        .options(
            selectinload(FinancialResolutionCase.wallet),
            selectinload(FinancialResolutionCase.reversal),
            selectinload(FinancialResolutionCase.reconciliation_event),
        )
        .order_by(FinancialResolutionCase.created_at.desc(), FinancialResolutionCase.id.desc())
        .offset(offset)
        .limit(limit)
    )
    cases = list((await session.execute(stmt)).scalars().all())
    actor_is_owner = Role.OWNER in principal.roles
    next_offset = offset + len(cases) if offset + len(cases) < total else None
    return FinancialCaseListResponse(
        cases=[_case_response(case, actor_is_owner=actor_is_owner) for case in cases],
        total=total,
        offset=offset,
        limit=limit,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.post("/cases/{case_id}/claim", response_model=FinancialCaseResponse)
async def claim_financial_case(
    case_id: uuid.UUID,
    req: FinancialCaseVersionRequest,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
    resolution_service: FinancialResolutionService = Depends(get_financial_resolution_service),
) -> FinancialCaseResponse:
    try:
        case = await resolution_service.claim_case(
            session,
            principal.tenant_id,
            case_id,
            principal.user_id,
            req.expected_version,
        )
        await _audit(
            session,
            principal,
            action="FINANCIAL_CASE_CLAIMED",
            case_id=case.id,
            details={"version": case.version},
        )
        await session.flush()
    except PaymentError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    loaded = await _load_case(session, principal.tenant_id, case_id)
    return _case_response(loaded, actor_is_owner=Role.OWNER in principal.roles)


@router.post("/cases/{case_id}/release", response_model=FinancialCaseResponse)
async def release_financial_case(
    case_id: uuid.UUID,
    req: FinancialCaseVersionRequest,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
    resolution_service: FinancialResolutionService = Depends(get_financial_resolution_service),
) -> FinancialCaseResponse:
    try:
        case = await resolution_service.release_case(
            session,
            principal.tenant_id,
            case_id,
            principal.user_id,
            req.expected_version,
            allow_override=bool(principal.roles.intersection({Role.ADMIN, Role.OWNER})),
        )
        await _audit(
            session,
            principal,
            action="FINANCIAL_CASE_RELEASED",
            case_id=case.id,
            details={"version": case.version},
        )
        await session.flush()
    except PaymentError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    loaded = await _load_case(session, principal.tenant_id, case_id)
    return _case_response(loaded, actor_is_owner=Role.OWNER in principal.roles)


@router.post("/cases/{case_id}/resolve", response_model=FinancialCaseResponse)
async def resolve_financial_case(
    case_id: uuid.UUID,
    req: FinancialCaseResolveRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
    resolution_service: FinancialResolutionService = Depends(get_financial_resolution_service),
    payment_service: PaymentService = Depends(get_payment_service),
) -> FinancialCaseResponse:
    try:
        case = await resolution_service.resolve_case(
            session=session,
            tenant_id=principal.tenant_id,
            case_id=case_id,
            actor_user_id=principal.user_id,
            expected_version=req.expected_version,
            action=req.action,
            note=req.note,
            actor_is_owner=Role.OWNER in principal.roles,
            payment_service=payment_service,
        )
        await _audit(
            session,
            principal,
            action="FINANCIAL_CASE_RESOLVED",
            case_id=case.id,
            details={
                "resolution_action": req.action.value,
                "resolution_code": case.resolution_code,
                "version": case.version,
                "wallet_id": str(case.wallet_id) if case.wallet_id else None,
            },
        )
        await session.flush()
    except TenantAccessViolationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except InsufficientFundsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (PaymentIntegrityError, PaymentError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    loaded = await _load_case(session, principal.tenant_id, case_id)
    return _case_response(loaded, actor_is_owner=Role.OWNER in principal.roles)
