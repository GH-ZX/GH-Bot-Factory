from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_staff_or_above
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.saas.product_entitlements import resolve_product_entitlements
from packages.saas.service import (
    EntitlementConfigurationError,
    resolve_tenant_usage,
)

router = APIRouter(prefix="/admin/saas", tags=["admin-saas"])


class SaaSLimitsResponse(BaseModel):
    max_bots: int
    max_enabled_bots: int
    max_open_provisioning_jobs: int


class SaaSUsageResponse(BaseModel):
    bots: int
    enabled_bots: int
    open_provisioning_jobs: int


class SaaSCommercialAccessResponse(BaseModel):
    state: str
    allowed: bool
    reason: str
    grace_ends_at: str | None


class SaaSOverviewResponse(BaseModel):
    source: str
    plan_key: str | None
    plan_name: str | None
    subscription_status: str | None
    limits: SaaSLimitsResponse
    usage: SaaSUsageResponse
    features: dict[str, bool]
    effective_features: dict[str, bool]
    commercial_access: SaaSCommercialAccessResponse


@router.get("/overview", response_model=SaaSOverviewResponse)
async def get_saas_overview(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> SaaSOverviewResponse:
    try:
        product = await resolve_product_entitlements(session, tenant_id=principal.tenant_id)
        entitlements = product.entitlements
    except EntitlementConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant plan entitlements are invalid; contact the platform operator.",
        ) from exc
    usage = await resolve_tenant_usage(session, tenant_id=principal.tenant_id)
    return SaaSOverviewResponse(
        source=entitlements.source,
        plan_key=entitlements.plan_key,
        plan_name=entitlements.plan_name,
        subscription_status=entitlements.subscription_status,
        limits=SaaSLimitsResponse(
            max_bots=entitlements.max_bots,
            max_enabled_bots=entitlements.max_enabled_bots,
            max_open_provisioning_jobs=entitlements.max_open_provisioning_jobs,
        ),
        usage=SaaSUsageResponse(
            bots=usage.bots,
            enabled_bots=usage.enabled_bots,
            open_provisioning_jobs=usage.open_provisioning_jobs,
        ),
        features=entitlements.features,
        effective_features=product.effective_features,
        commercial_access=SaaSCommercialAccessResponse(
            state=product.access.state.value,
            allowed=product.access.allowed,
            reason=product.access.reason,
            grace_ends_at=product.access.grace_ends_at.isoformat() if product.access.grace_ends_at else None,
        ),
    )
