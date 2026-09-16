from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.platform_deps import PlatformOperator, require_platform_operator
from packages.core.database import get_db_session
from packages.saas.billing import BillingProviderError, get_billing_provider
from packages.saas.billing_service import reconcile_provider_subscriptions
from packages.saas.control_plane import (
    BillingEventConflictError,
    PlatformConflictError,
    PlatformControlPlaneError,
    PlatformNotFoundError,
    PlatformValidationError,
    append_platform_audit,
    clear_subscription,
    converge_billing_event,
    create_plan,
    create_plan_price,
    platform_counts,
    update_plan,
    update_plan_price,
    upsert_subscription,
)
from packages.saas.models import (
    BillingEvent,
    BillingInterval,
    PlatformAuditLog,
    SaaSPlan,
    SaaSPlanPrice,
    SubscriptionStatus,
    TenantSubscription,
)
from packages.saas.operations import collect_saas_health, inspect_tenant_saas
from packages.tenants.models import Tenant

router = APIRouter(prefix="/platform", tags=["platform-control-plane"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _http_error(exc: PlatformControlPlaneError) -> HTTPException:
    if isinstance(exc, PlatformNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, (PlatformConflictError, BillingEventConflictError)):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, PlatformValidationError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail={"code": exc.code, "message": str(exc)})


class PlatformOverviewResponse(BaseModel):
    tenants: int
    active_tenants: int
    plans: int
    active_plans: int
    subscriptions: int
    billing_events: int


class PlanCreateRequest(BaseModel):
    key: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    is_public: bool = False
    entitlements: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None
    is_public: bool | None = None
    entitlements: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class PlanResponse(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None
    is_active: bool
    is_public: bool
    entitlements: dict[str, Any]
    metadata: dict[str, Any]
    subscriber_count: int = 0
    created_at: datetime
    updated_at: datetime


class PlanPriceCreateRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    external_price_id: str = Field(min_length=1, max_length=255)
    currency: str = Field(min_length=3, max_length=3)
    unit_amount_minor: int = Field(ge=0)
    interval: BillingInterval
    interval_count: int = Field(default=1, ge=1, le=36)
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanPriceUpdateRequest(BaseModel):
    is_active: bool | None = None
    metadata: dict[str, Any] | None = None


class PlanPriceResponse(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    provider: str
    external_price_id: str
    currency: str
    unit_amount_minor: int
    interval: BillingInterval
    interval_count: int
    is_active: bool
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SubscriptionWriteRequest(BaseModel):
    plan_key: str = Field(min_length=3, max_length=64)
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    billing_provider: str | None = Field(default=None, max_length=50)
    external_customer_id: str | None = Field(default=None, max_length=255)
    external_subscription_id: str | None = Field(default=None, max_length=255)
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    trial_ends_at: datetime | None = None
    grace_ends_at: datetime | None = None
    cancel_at_period_end: bool = False
    entitlement_overrides: dict[str, Any] = Field(default_factory=dict)
    billing_metadata: dict[str, Any] = Field(default_factory=dict)


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    plan_id: uuid.UUID
    plan_key: str
    plan_name: str
    status: SubscriptionStatus
    billing_provider: str | None
    external_customer_id: str | None
    external_subscription_id: str | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_ends_at: datetime | None
    grace_ends_at: datetime | None
    provider_synced_at: datetime | None
    provider_sync_source: str | None
    provider_sync_error: str | None
    provider_sync_error_at: datetime | None
    cancel_at_period_end: bool
    entitlement_overrides: dict[str, Any]
    billing_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class PlatformTenantResponse(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    is_active: bool
    subscription: SubscriptionResponse | None
    created_at: datetime


class PlatformTenantListResponse(BaseModel):
    tenants: list[PlatformTenantResponse]
    total: int
    offset: int
    limit: int
    next_offset: int | None


class BillingConvergenceRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    external_event_id: str = Field(min_length=1, max_length=255)
    event_type: str = Field(min_length=1, max_length=120)
    plan_key: str = Field(min_length=3, max_length=64)
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    external_customer_id: str | None = Field(default=None, max_length=255)
    external_subscription_id: str | None = Field(default=None, max_length=255)
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    trial_ends_at: datetime | None = None
    grace_ends_at: datetime | None = None
    cancel_at_period_end: bool = False
    entitlement_overrides: dict[str, Any] = Field(default_factory=dict)
    billing_metadata: dict[str, Any] = Field(default_factory=dict)
    event_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.strip().lower()


class BillingEventResponse(BaseModel):
    id: uuid.UUID
    provider: str
    external_event_id: str
    event_type: str
    payload_sha256: str
    status: str
    tenant_id: uuid.UUID | None
    subscription_id: uuid.UUID | None
    attempts: int
    processed_at: datetime | None
    event_metadata: dict[str, Any]
    last_error: str | None
    duplicate: bool = False
    created_at: datetime


class BillingReconcileResponse(BaseModel):
    provider: str
    scanned: int
    applied: int
    duplicates: int
    failed: int
    errors: list[str]


class SaaSOperationsAlertResponse(BaseModel):
    severity: str
    code: str
    tenant_id: uuid.UUID
    tenant_slug: str
    message: str
    action: str


class SaaSHealthResponse(BaseModel):
    total_tenants: int
    self_hosted_tenants: int
    subscribed_tenants: int
    active_access: int
    grace_access: int
    blocked_access: int
    provider_managed_subscriptions: int
    stale_provider_syncs: int
    status_counts: dict[str, int]
    alerts: list[SaaSOperationsAlertResponse]


class TenantEntitlementInspectionResponse(BaseModel):
    tenant_id: uuid.UUID
    tenant_slug: str
    tenant_name: str
    source: str
    plan_key: str | None
    plan_name: str | None
    subscription_status: str | None
    billing_provider: str | None
    provider_synced_at: datetime | None
    provider_sync_source: str | None
    provider_sync_error: str | None
    configured_features: dict[str, bool]
    effective_features: dict[str, bool]
    commercial_access_state: str
    commercial_access_allowed: bool
    commercial_access_reason: str
    grace_ends_at: datetime | None
    usage: dict[str, int]
    limits: dict[str, int]


class PlatformAuditResponse(BaseModel):
    id: uuid.UUID
    actor: str
    action: str
    resource_type: str
    resource_id: str
    tenant_id: uuid.UUID | None
    details: dict[str, Any]
    ip_address: str | None
    created_at: datetime


def _subscription_response(subscription: TenantSubscription, plan: SaaSPlan) -> SubscriptionResponse:
    return SubscriptionResponse(
        id=subscription.id,
        tenant_id=subscription.tenant_id,
        plan_id=subscription.plan_id,
        plan_key=plan.key,
        plan_name=plan.name,
        status=subscription.status,
        billing_provider=subscription.billing_provider,
        external_customer_id=subscription.external_customer_id,
        external_subscription_id=subscription.external_subscription_id,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        trial_ends_at=subscription.trial_ends_at,
        grace_ends_at=subscription.grace_ends_at,
        provider_synced_at=subscription.provider_synced_at,
        provider_sync_source=subscription.provider_sync_source,
        provider_sync_error=subscription.provider_sync_error,
        provider_sync_error_at=subscription.provider_sync_error_at,
        cancel_at_period_end=subscription.cancel_at_period_end,
        entitlement_overrides=subscription.entitlement_overrides or {},
        billing_metadata=subscription.billing_metadata or {},
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
    )


def _price_response(price: SaaSPlanPrice) -> PlanPriceResponse:
    return PlanPriceResponse(
        id=price.id,
        plan_id=price.plan_id,
        provider=price.provider,
        external_price_id=price.external_price_id,
        currency=price.currency,
        unit_amount_minor=price.unit_amount_minor,
        interval=price.interval,
        interval_count=price.interval_count,
        is_active=price.is_active,
        metadata=price.metadata_json or {},
        created_at=price.created_at,
        updated_at=price.updated_at,
    )


@router.get("/overview", response_model=PlatformOverviewResponse)
async def get_platform_overview(
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> PlatformOverviewResponse:
    return PlatformOverviewResponse(**await platform_counts(session))


@router.get("/plans", response_model=list[PlanResponse])
async def list_plans(
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> list[PlanResponse]:
    rows = (
        await session.execute(
            select(SaaSPlan, func.count(TenantSubscription.id))
            .outerjoin(TenantSubscription, TenantSubscription.plan_id == SaaSPlan.id)
            .group_by(SaaSPlan.id)
            .order_by(SaaSPlan.created_at.asc(), SaaSPlan.key.asc())
        )
    ).all()
    return [
        PlanResponse(
            id=plan.id,
            key=plan.key,
            name=plan.name,
            description=plan.description,
            is_active=plan.is_active,
            is_public=plan.is_public,
            entitlements=plan.entitlements or {},
            metadata=plan.metadata_json or {},
            subscriber_count=int(count or 0),
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )
        for plan, count in rows
    ]


@router.post("/plans", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
async def create_platform_plan(
    req: PlanCreateRequest,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> PlanResponse:
    try:
        plan = await create_plan(
            session,
            key=req.key,
            name=req.name,
            description=req.description,
            is_active=req.is_active,
            is_public=req.is_public,
            entitlements=req.entitlements,
            metadata_json=req.metadata,
        )
        await append_platform_audit(
            session,
            action="SAAS_PLAN_CREATED",
            resource_type="SAAS_PLAN",
            resource_id=str(plan.id),
            details={"plan_key": plan.key, "is_public": plan.is_public},
            ip_address=_client_ip(request),
            actor=operator.actor,
        )
        await session.commit()
        await session.refresh(plan)
    except PlatformControlPlaneError as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    return PlanResponse(
        id=plan.id,
        key=plan.key,
        name=plan.name,
        description=plan.description,
        is_active=plan.is_active,
        is_public=plan.is_public,
        entitlements=plan.entitlements or {},
        metadata=plan.metadata_json or {},
        subscriber_count=0,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


@router.patch("/plans/{plan_id}", response_model=PlanResponse)
async def patch_platform_plan(
    plan_id: uuid.UUID,
    req: PlanUpdateRequest,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> PlanResponse:
    try:
        plan = await update_plan(
            session,
            plan_id=plan_id,
            name=req.name,
            description=req.description,
            description_supplied="description" in req.model_fields_set,
            is_active=req.is_active,
            is_public=req.is_public,
            entitlements=req.entitlements,
            metadata_json=req.metadata,
        )
        await append_platform_audit(
            session,
            action="SAAS_PLAN_UPDATED",
            resource_type="SAAS_PLAN",
            resource_id=str(plan.id),
            details={"plan_key": plan.key, "changed_fields": sorted(req.model_fields_set)},
            ip_address=_client_ip(request),
            actor=operator.actor,
        )
        await session.commit()
        subscriber_count = int(
            await session.scalar(
                select(func.count()).select_from(TenantSubscription).where(TenantSubscription.plan_id == plan.id)
            )
            or 0
        )
        await session.refresh(plan)
    except PlatformControlPlaneError as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    return PlanResponse(
        id=plan.id,
        key=plan.key,
        name=plan.name,
        description=plan.description,
        is_active=plan.is_active,
        is_public=plan.is_public,
        entitlements=plan.entitlements or {},
        metadata=plan.metadata_json or {},
        subscriber_count=subscriber_count,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


@router.get("/plans/{plan_id}/prices", response_model=list[PlanPriceResponse])
async def list_platform_plan_prices(
    plan_id: uuid.UUID,
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> list[PlanPriceResponse]:
    if await session.get(SaaSPlan, plan_id) is None:
        raise HTTPException(status_code=404, detail="SaaS plan was not found.")
    rows = (
        await session.execute(
            select(SaaSPlanPrice)
            .where(SaaSPlanPrice.plan_id == plan_id)
            .order_by(SaaSPlanPrice.created_at.asc(), SaaSPlanPrice.id.asc())
        )
    ).scalars().all()
    return [_price_response(row) for row in rows]


@router.post(
    "/plans/{plan_id}/prices",
    response_model=PlanPriceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_platform_plan_price(
    plan_id: uuid.UUID,
    req: PlanPriceCreateRequest,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> PlanPriceResponse:
    try:
        price = await create_plan_price(
            session,
            plan_id=plan_id,
            provider=req.provider,
            external_price_id=req.external_price_id,
            currency=req.currency,
            unit_amount_minor=req.unit_amount_minor,
            interval=req.interval,
            interval_count=req.interval_count,
            is_active=req.is_active,
            metadata_json=req.metadata,
        )
        await append_platform_audit(
            session,
            action="SAAS_PLAN_PRICE_CREATED",
            resource_type="SAAS_PLAN_PRICE",
            resource_id=str(price.id),
            details={
                "plan_id": str(plan_id),
                "provider": price.provider,
                "currency": price.currency,
                "interval": price.interval.value,
            },
            ip_address=_client_ip(request),
            actor=operator.actor,
        )
        await session.commit()
        await session.refresh(price)
    except PlatformControlPlaneError as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    return _price_response(price)


@router.patch("/prices/{price_id}", response_model=PlanPriceResponse)
async def patch_platform_plan_price(
    price_id: uuid.UUID,
    req: PlanPriceUpdateRequest,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> PlanPriceResponse:
    try:
        price = await update_plan_price(
            session,
            price_id=price_id,
            is_active=req.is_active,
            metadata_json=req.metadata,
        )
        await append_platform_audit(
            session,
            action="SAAS_PLAN_PRICE_UPDATED",
            resource_type="SAAS_PLAN_PRICE",
            resource_id=str(price.id),
            details={"changed_fields": sorted(req.model_fields_set)},
            ip_address=_client_ip(request),
            actor=operator.actor,
        )
        await session.commit()
        await session.refresh(price)
    except PlatformControlPlaneError as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    return _price_response(price)


@router.get("/tenants", response_model=PlatformTenantListResponse)
async def list_platform_tenants(
    q: str | None = Query(default=None, max_length=120),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> PlatformTenantListResponse:
    filters = [Tenant.deleted_at.is_(None)]
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        filters.append(or_(func.lower(Tenant.name).like(needle), func.lower(Tenant.slug).like(needle)))

    total = int(await session.scalar(select(func.count()).select_from(Tenant).where(*filters)) or 0)
    tenants = (
        await session.execute(
            select(Tenant)
            .where(*filters)
            .order_by(Tenant.created_at.asc(), Tenant.id.asc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    tenant_ids = [tenant.id for tenant in tenants]
    subscriptions: dict[uuid.UUID, tuple[TenantSubscription, SaaSPlan]] = {}
    if tenant_ids:
        rows = (
            await session.execute(
                select(TenantSubscription, SaaSPlan)
                .join(SaaSPlan, SaaSPlan.id == TenantSubscription.plan_id)
                .where(TenantSubscription.tenant_id.in_(tenant_ids))
            )
        ).all()
        subscriptions = {subscription.tenant_id: (subscription, plan) for subscription, plan in rows}

    payload: list[PlatformTenantResponse] = []
    for tenant in tenants:
        pair = subscriptions.get(tenant.id)
        payload.append(
            PlatformTenantResponse(
                id=tenant.id,
                slug=tenant.slug,
                name=tenant.name,
                is_active=tenant.is_active,
                subscription=_subscription_response(*pair) if pair else None,
                created_at=tenant.created_at,
            )
        )
    next_offset = offset + len(payload) if offset + len(payload) < total else None
    return PlatformTenantListResponse(
        tenants=payload,
        total=total,
        offset=offset,
        limit=limit,
        next_offset=next_offset,
    )


@router.put("/tenants/{tenant_id}/subscription", response_model=SubscriptionResponse)
async def set_platform_subscription(
    tenant_id: uuid.UUID,
    req: SubscriptionWriteRequest,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionResponse:
    try:
        subscription = await upsert_subscription(
            session,
            tenant_id=tenant_id,
            plan_key=req.plan_key,
            status=req.status,
            billing_provider=req.billing_provider,
            external_customer_id=req.external_customer_id,
            external_subscription_id=req.external_subscription_id,
            current_period_start=req.current_period_start,
            current_period_end=req.current_period_end,
            trial_ends_at=req.trial_ends_at,
            grace_ends_at=req.grace_ends_at,
            cancel_at_period_end=req.cancel_at_period_end,
            entitlement_overrides=req.entitlement_overrides,
            billing_metadata=req.billing_metadata,
        )
        plan = await session.get(SaaSPlan, subscription.plan_id)
        assert plan is not None
        await append_platform_audit(
            session,
            action="TENANT_SUBSCRIPTION_SET",
            resource_type="TENANT_SUBSCRIPTION",
            resource_id=str(subscription.id),
            tenant_id=tenant_id,
            details={"plan_key": plan.key, "status": subscription.status.value},
            ip_address=_client_ip(request),
            actor=operator.actor,
        )
        await session.commit()
        await session.refresh(subscription)
    except PlatformControlPlaneError as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    return _subscription_response(subscription, plan)


@router.delete("/tenants/{tenant_id}/subscription", status_code=status.HTTP_204_NO_CONTENT)
async def delete_platform_subscription(
    tenant_id: uuid.UUID,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    try:
        subscription = await clear_subscription(session, tenant_id=tenant_id)
        plan_key = subscription.plan.key if subscription.plan else None
        await append_platform_audit(
            session,
            action="TENANT_SUBSCRIPTION_CLEARED",
            resource_type="TENANT",
            resource_id=str(tenant_id),
            tenant_id=tenant_id,
            details={"previous_plan_key": plan_key},
            ip_address=_client_ip(request),
            actor=operator.actor,
        )
        await session.commit()
    except PlatformControlPlaneError as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.post(
    "/tenants/{tenant_id}/billing-events/converge",
    response_model=BillingEventResponse,
)
async def converge_platform_billing_event(
    tenant_id: uuid.UUID,
    req: BillingConvergenceRequest,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> BillingEventResponse:
    try:
        result = await converge_billing_event(
            session,
            provider=req.provider,
            external_event_id=req.external_event_id,
            event_type=req.event_type,
            tenant_id=tenant_id,
            plan_key=req.plan_key,
            status=req.status,
            external_customer_id=req.external_customer_id,
            external_subscription_id=req.external_subscription_id,
            current_period_start=req.current_period_start,
            current_period_end=req.current_period_end,
            trial_ends_at=req.trial_ends_at,
            grace_ends_at=req.grace_ends_at,
            cancel_at_period_end=req.cancel_at_period_end,
            entitlement_overrides=req.entitlement_overrides,
            billing_metadata=req.billing_metadata,
            event_metadata=req.event_metadata,
        )
        await append_platform_audit(
            session,
            action="BILLING_EVENT_DUPLICATE" if result.duplicate else "BILLING_EVENT_APPLIED",
            resource_type="BILLING_EVENT",
            resource_id=str(result.event.id),
            tenant_id=tenant_id,
            details={
                "provider": result.event.provider,
                "event_type": result.event.event_type,
                "duplicate": result.duplicate,
            },
            ip_address=_client_ip(request),
            actor=operator.actor,
        )
        await session.commit()
        event = result.event
    except PlatformControlPlaneError as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    return BillingEventResponse(
        id=event.id,
        provider=event.provider,
        external_event_id=event.external_event_id,
        event_type=event.event_type,
        payload_sha256=event.payload_sha256,
        status=event.status.value,
        tenant_id=event.tenant_id,
        subscription_id=event.subscription_id,
        attempts=event.attempts,
        processed_at=event.processed_at,
        event_metadata=event.event_metadata or {},
        last_error=event.last_error,
        duplicate=result.duplicate,
        created_at=event.created_at,
    )


@router.get("/operations/saas-health", response_model=SaaSHealthResponse)
async def get_saas_operations_health(
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> SaaSHealthResponse:
    snapshot = await collect_saas_health(session)
    return SaaSHealthResponse(
        total_tenants=snapshot.total_tenants,
        self_hosted_tenants=snapshot.self_hosted_tenants,
        subscribed_tenants=snapshot.subscribed_tenants,
        active_access=snapshot.active_access,
        grace_access=snapshot.grace_access,
        blocked_access=snapshot.blocked_access,
        provider_managed_subscriptions=snapshot.provider_managed_subscriptions,
        stale_provider_syncs=snapshot.stale_provider_syncs,
        status_counts=snapshot.status_counts,
        alerts=[
            SaaSOperationsAlertResponse(
                severity=alert.severity,
                code=alert.code,
                tenant_id=alert.tenant_id,
                tenant_slug=alert.tenant_slug,
                message=alert.message,
                action=alert.action,
            )
            for alert in snapshot.alerts
        ],
    )


@router.get("/tenants/{tenant_id}/entitlements", response_model=TenantEntitlementInspectionResponse)
async def inspect_platform_tenant_entitlements(
    tenant_id: uuid.UUID,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> TenantEntitlementInspectionResponse:
    inspection = await inspect_tenant_saas(session, tenant_id=tenant_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Tenant was not found.")
    subscription = inspection.subscription
    await append_platform_audit(
        session,
        action="TENANT_ENTITLEMENTS_INSPECTED",
        resource_type="TENANT",
        resource_id=str(tenant_id),
        tenant_id=tenant_id,
        details={"support_mode": "NO_IMPERSONATION", "access_state": inspection.access_state},
        ip_address=_client_ip(request),
        actor=operator.actor,
    )
    await session.commit()
    return TenantEntitlementInspectionResponse(
        tenant_id=inspection.tenant.id,
        tenant_slug=inspection.tenant.slug,
        tenant_name=inspection.tenant.name,
        source=inspection.source,
        plan_key=inspection.plan_key,
        plan_name=inspection.plan_name,
        subscription_status=subscription.status.value if subscription else None,
        billing_provider=subscription.billing_provider if subscription else None,
        provider_synced_at=subscription.provider_synced_at if subscription else None,
        provider_sync_source=subscription.provider_sync_source if subscription else None,
        provider_sync_error=subscription.provider_sync_error if subscription else None,
        configured_features=inspection.configured_features,
        effective_features=inspection.effective_features,
        commercial_access_state=inspection.access_state,
        commercial_access_allowed=inspection.access_allowed,
        commercial_access_reason=inspection.access_reason,
        grace_ends_at=inspection.grace_ends_at,
        usage={
            "bots": inspection.usage_bots,
            "enabled_bots": inspection.usage_enabled_bots,
            "open_provisioning_jobs": inspection.usage_open_jobs,
        },
        limits={
            "max_bots": inspection.max_bots,
            "max_enabled_bots": inspection.max_enabled_bots,
            "max_open_provisioning_jobs": inspection.max_open_jobs,
        },
    )


@router.post("/billing/reconcile", response_model=BillingReconcileResponse)
async def reconcile_platform_billing(
    request: Request,
    provider: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=200, ge=1, le=1000),
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> BillingReconcileResponse:
    try:
        adapter = get_billing_provider(provider)
        summary = await reconcile_provider_subscriptions(session, adapter=adapter, limit=limit)
        await append_platform_audit(
            session,
            action="BILLING_RECONCILIATION_RUN",
            resource_type="BILLING_PROVIDER",
            resource_id=summary.provider,
            details={
                "scanned": summary.scanned,
                "applied": summary.applied,
                "duplicates": summary.duplicates,
                "failed": summary.failed,
            },
            ip_address=_client_ip(request),
            actor=operator.actor,
        )
        await session.commit()
    except BillingProviderError as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)}) from exc
    except PlatformControlPlaneError as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    return BillingReconcileResponse(
        provider=summary.provider,
        scanned=summary.scanned,
        applied=summary.applied,
        duplicates=summary.duplicates,
        failed=summary.failed,
        errors=list(summary.errors),
    )


@router.get("/billing-events", response_model=list[BillingEventResponse])
async def list_platform_billing_events(
    tenant_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> list[BillingEventResponse]:
    stmt = select(BillingEvent).order_by(BillingEvent.created_at.desc(), BillingEvent.id.desc()).limit(limit)
    if tenant_id is not None:
        stmt = stmt.where(BillingEvent.tenant_id == tenant_id)
    events = (await session.execute(stmt)).scalars().all()
    return [
        BillingEventResponse(
            id=event.id,
            provider=event.provider,
            external_event_id=event.external_event_id,
            event_type=event.event_type,
            payload_sha256=event.payload_sha256,
            status=event.status.value,
            tenant_id=event.tenant_id,
            subscription_id=event.subscription_id,
            attempts=event.attempts,
            processed_at=event.processed_at,
            event_metadata=event.event_metadata or {},
            last_error=event.last_error,
            duplicate=False,
            created_at=event.created_at,
        )
        for event in events
    ]


@router.get("/audit", response_model=list[PlatformAuditResponse])
async def list_platform_audit(
    tenant_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> list[PlatformAuditResponse]:
    stmt = select(PlatformAuditLog).order_by(PlatformAuditLog.created_at.desc(), PlatformAuditLog.id.desc()).limit(limit)
    if tenant_id is not None:
        stmt = stmt.where(PlatformAuditLog.tenant_id == tenant_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        PlatformAuditResponse(
            id=row.id,
            actor=row.actor,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            tenant_id=row.tenant_id,
            details=row.details or {},
            ip_address=row.ip_address,
            created_at=row.created_at,
        )
        for row in rows
    ]
