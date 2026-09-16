import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_staff_or_above
from packages.analytics.service import AdminAnalyticsService
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.tenants.models import AuditLog, User

router = APIRouter(prefix="/admin", tags=["admin-analytics"])


class AnalyticsPeriodResponse(BaseModel):
    days: int
    start: datetime
    end: datetime


class OrderAnalyticsResponse(BaseModel):
    total: int
    fulfilled: int
    failed: int
    refunded: int
    by_status: dict[str, int]


class FulfillmentAnalyticsResponse(BaseModel):
    attempts: int
    succeeded: int
    success_rate: float | None
    by_status: dict[str, int]


class OperationsAnalyticsResponse(BaseModel):
    dead_letter_jobs: int
    open_financial_cases: int
    frozen_wallets: int
    reconciliation_reviews: int
    audit_events: int


class CurrencyAnalyticsResponse(BaseModel):
    currency: str
    order_count: int
    gross_order_value: Decimal
    refunded_order_value: Decimal
    net_order_value: Decimal
    topup_count: int
    topup_value: Decimal
    reversal_count: int
    reversal_value: Decimal
    wallet_count: int
    wallet_liability: Decimal


class ProviderAnalyticsResponse(BaseModel):
    provider_id: uuid.UUID | None
    provider_name: str
    currency: str
    attempts: int
    succeeded: int
    non_succeeded: int
    success_rate: float | None
    cost_amount: Decimal


class DailyAnalyticsResponse(BaseModel):
    date: str
    currency: str
    order_count: int
    order_value: Decimal
    topup_count: int
    topup_value: Decimal


class AdminAnalyticsResponse(BaseModel):
    period: AnalyticsPeriodResponse
    orders: OrderAnalyticsResponse
    fulfillment: FulfillmentAnalyticsResponse
    operations: OperationsAnalyticsResponse
    financials: list[CurrencyAnalyticsResponse]
    providers: list[ProviderAnalyticsResponse]
    daily: list[DailyAnalyticsResponse]


class AuditActorResponse(BaseModel):
    id: uuid.UUID | None
    display_name: str
    username: str | None


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    action: str
    resource_type: str
    resource_id: str
    actor: AuditActorResponse
    details: dict[str, Any]
    ip_address: str | None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    logs: list[AuditLogResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


_SECRET_KEY_FRAGMENTS = (
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
    "api_key",
    "apikey",
)


def _redact_audit_details(value: Any) -> Any:
    """Defense-in-depth redaction for legacy/future audit details before API exposure."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_audit_details(item)
        return redacted
    if isinstance(value, list):
        return [_redact_audit_details(item) for item in value]
    return value


@router.get("/analytics/overview", response_model=AdminAnalyticsResponse)
async def admin_analytics_overview(
    days: int = Query(30),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> AdminAnalyticsResponse:
    if days not in {7, 30, 90}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Analytics days must be one of 7, 30, or 90.",
        )
    payload = await AdminAnalyticsService().overview(
        session,
        principal.tenant_id,
        days=days,
    )
    return AdminAnalyticsResponse.model_validate(payload)


@router.get("/audit-logs", response_model=AuditLogListResponse)
async def admin_audit_logs(
    q: str | None = Query(None, max_length=120),
    action: str | None = Query(None, max_length=100),
    resource_type: str | None = Query(None, max_length=100),
    actor_user_id: uuid.UUID | None = Query(None),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> AuditLogListResponse:
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Audit start must not be later than end.",
        )

    filters = [AuditLog.tenant_id == principal.tenant_id]
    if action:
        filters.append(AuditLog.action == action.strip())
    if resource_type:
        filters.append(AuditLog.resource_type == resource_type.strip())
    if actor_user_id:
        filters.append(AuditLog.user_id == actor_user_id)
    if start is not None:
        filters.append(AuditLog.created_at >= start)
    if end is not None:
        filters.append(AuditLog.created_at <= end)
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        filters.append(
            or_(
                func.lower(AuditLog.action).like(needle),
                func.lower(AuditLog.resource_type).like(needle),
                func.lower(AuditLog.resource_id).like(needle),
            )
        )

    total = await session.scalar(
        select(func.count()).select_from(AuditLog).where(*filters)
    ) or 0
    rows = (
        await session.execute(
            select(AuditLog, User)
            .outerjoin(User, User.id == AuditLog.user_id)
            .where(*filters)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()

    logs: list[AuditLogResponse] = []
    for log, user in rows:
        if user is None:
            display_name = "System"
            username = None
        else:
            display_name = (
                " ".join(filter(None, [user.first_name, user.last_name])).strip()
                or user.username
                or "Tenant member"
            )
            username = user.username
        logs.append(
            AuditLogResponse(
                id=log.id,
                action=log.action,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                actor=AuditActorResponse(
                    id=log.user_id,
                    display_name=display_name,
                    username=username,
                ),
                details=_redact_audit_details(log.details or {}),
                ip_address=log.ip_address,
                created_at=log.created_at,
            )
        )

    next_offset = offset + len(logs) if offset + len(logs) < total else None
    return AuditLogListResponse(
        logs=logs,
        total=int(total),
        offset=offset,
        limit=limit,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )
