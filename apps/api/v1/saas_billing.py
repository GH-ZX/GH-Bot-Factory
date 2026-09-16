from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_admin_or_owner, require_staff_or_above
from packages.core.auth import AuthenticatedPrincipal
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.saas.billing import (
    BillingProviderError,
    BillingWebhookVerificationError,
    get_billing_provider,
)
from packages.saas.billing_service import (
    converge_verified_webhook,
    ensure_checkout_allowed,
    get_tenant_subscription,
    list_public_billing_catalog,
    resolve_checkout_price,
)
from packages.saas.control_plane import (
    PlatformConflictError,
    PlatformControlPlaneError,
    PlatformNotFoundError,
    PlatformValidationError,
    append_platform_audit,
)
from packages.saas.models import BillingInterval, SubscriptionStatus
from packages.tenants.models import AuditLog, Tenant

router = APIRouter(tags=["saas-billing"])


class BillingCatalogPriceResponse(BaseModel):
    id: uuid.UUID
    plan_key: str
    plan_name: str
    plan_description: str | None
    provider: str
    currency: str
    unit_amount_minor: int
    interval: BillingInterval
    interval_count: int


class TenantBillingSubscriptionResponse(BaseModel):
    plan_key: str
    plan_name: str
    status: SubscriptionStatus
    billing_provider: str | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_ends_at: datetime | None
    grace_ends_at: datetime | None
    provider_synced_at: datetime | None
    provider_sync_source: str | None
    provider_sync_error: str | None
    cancel_at_period_end: bool


class BillingPolicyResponse(BaseModel):
    past_due_grace_days: int
    commercial_enforcement: str


class TenantBillingOverviewResponse(BaseModel):
    provider: str | None
    provider_configured: bool
    checkout_available: bool
    portal_available: bool
    subscription: TenantBillingSubscriptionResponse | None
    catalog: list[BillingCatalogPriceResponse]
    policy: BillingPolicyResponse


class BillingCheckoutRequest(BaseModel):
    price_id: uuid.UUID


class BillingRedirectResponse(BaseModel):
    provider: str
    session_id: str
    url: str
    expires_at: datetime | None = None


class BillingWebhookResponse(BaseModel):
    status: str
    event_id: str
    duplicate: bool = False


def _billing_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PlatformNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PlatformConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PlatformValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, BillingProviderError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": str(exc)},
        )
    return HTTPException(status_code=400, detail=str(exc))


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _tenant_audit(
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, object],
    request: Request,
) -> None:
    session.add(
        AuditLog(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=_client_ip(request),
        )
    )


def _require_checkout_urls() -> tuple[str, str]:
    success = (settings.billing_success_url or "").strip()
    cancel = (settings.billing_cancel_url or "").strip()
    if not success or not cancel:
        raise BillingProviderError(
            "Billing checkout URLs are not configured. Set BILLING_SUCCESS_URL and BILLING_CANCEL_URL."
        )
    return success, cancel


def _require_portal_return_url() -> str:
    portal = (settings.billing_portal_return_url or "").strip()
    if not portal:
        raise BillingProviderError(
            "Billing portal return URL is not configured. Set BILLING_PORTAL_RETURN_URL."
        )
    return portal


@router.get("/admin/saas/billing", response_model=TenantBillingOverviewResponse)
async def get_tenant_billing_overview(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> TenantBillingOverviewResponse:
    provider_name = settings.billing_provider.strip().lower()
    configured = provider_name != "disabled"
    catalog = []
    if configured:
        rows = await list_public_billing_catalog(session, provider=provider_name)
        catalog = [
            BillingCatalogPriceResponse(
                id=item.price.id,
                plan_key=item.plan.key,
                plan_name=item.plan.name,
                plan_description=item.plan.description,
                provider=item.price.provider,
                currency=item.price.currency,
                unit_amount_minor=item.price.unit_amount_minor,
                interval=item.price.interval,
                interval_count=item.price.interval_count,
            )
            for item in rows
        ]

    subscription = await get_tenant_subscription(session, tenant_id=principal.tenant_id)
    subscription_payload = None
    if subscription is not None and subscription.plan is not None:
        subscription_payload = TenantBillingSubscriptionResponse(
            plan_key=subscription.plan.key,
            plan_name=subscription.plan.name,
            status=subscription.status,
            billing_provider=subscription.billing_provider,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            trial_ends_at=subscription.trial_ends_at,
            grace_ends_at=subscription.grace_ends_at,
            provider_synced_at=subscription.provider_synced_at,
            provider_sync_source=subscription.provider_sync_source,
            provider_sync_error=subscription.provider_sync_error,
            cancel_at_period_end=subscription.cancel_at_period_end,
        )

    checkout_available = configured and (
        subscription is None or subscription.status == SubscriptionStatus.CANCELED
    )
    portal_available = bool(
        configured
        and subscription is not None
        and subscription.billing_provider == provider_name
        and subscription.external_customer_id
    )
    return TenantBillingOverviewResponse(
        provider=provider_name if configured else None,
        provider_configured=configured,
        checkout_available=checkout_available,
        portal_available=portal_available,
        subscription=subscription_payload,
        catalog=catalog,
        policy=BillingPolicyResponse(
            past_due_grace_days=settings.billing_past_due_grace_days,
            commercial_enforcement="SERVER_SIDE_PRODUCT_GATES_PHASE_9_3",
        ),
    )


@router.post("/admin/saas/billing/checkout", response_model=BillingRedirectResponse)
async def create_tenant_billing_checkout(
    req: BillingCheckoutRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> BillingRedirectResponse:
    raw_idempotency = (idempotency_key or "").strip()
    if len(raw_idempotency) < 8 or len(raw_idempotency) > 200:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must contain 8-200 characters.",
        )
    try:
        adapter = get_billing_provider()
        success_url, cancel_url = _require_checkout_urls()
        tenant = await session.get(Tenant, principal.tenant_id)
        if tenant is None or tenant.deleted_at is not None:
            raise PlatformNotFoundError("Tenant was not found.")
        subscription = await get_tenant_subscription(session, tenant_id=principal.tenant_id)
        ensure_checkout_allowed(subscription)
        catalog_item = await resolve_checkout_price(
            session,
            price_id=req.price_id,
            provider=adapter.provider_name,
        )
        provider_idempotency = hashlib.sha256(
            f"ghbf:{principal.tenant_id}:{req.price_id}:{raw_idempotency}".encode()
        ).hexdigest()
        result = await adapter.create_checkout_session(
            tenant_id=principal.tenant_id,
            tenant_name=tenant.name,
            plan_key=catalog_item.plan.key,
            external_price_id=catalog_item.price.external_price_id,
            success_url=success_url,
            cancel_url=cancel_url,
            idempotency_key=provider_idempotency,
            existing_customer_id=None,
        )
        await _tenant_audit(
            session,
            principal,
            action="SAAS_BILLING_CHECKOUT_CREATED",
            resource_type="SAAS_PLAN_PRICE",
            resource_id=str(catalog_item.price.id),
            details={
                "provider": result.provider,
                "plan_key": catalog_item.plan.key,
                "interval": catalog_item.price.interval.value,
            },
            request=request,
        )
        await session.commit()
    except (BillingProviderError, PlatformControlPlaneError) as exc:
        await session.rollback()
        raise _billing_http_error(exc) from exc
    return BillingRedirectResponse(
        provider=result.provider,
        session_id=result.external_session_id,
        url=result.url,
        expires_at=result.expires_at,
    )


@router.post("/admin/saas/billing/portal", response_model=BillingRedirectResponse)
async def create_tenant_billing_portal(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> BillingRedirectResponse:
    try:
        adapter = get_billing_provider()
        return_url = _require_portal_return_url()
        subscription = await get_tenant_subscription(session, tenant_id=principal.tenant_id)
        if subscription is None:
            raise PlatformConflictError("Tenant does not have a provider-managed subscription.")
        if subscription.billing_provider != adapter.provider_name or not subscription.external_customer_id:
            raise PlatformConflictError(
                "Tenant subscription is not managed by the configured hosted billing provider."
            )
        result = await adapter.create_portal_session(
            external_customer_id=subscription.external_customer_id,
            return_url=return_url,
        )
        await _tenant_audit(
            session,
            principal,
            action="SAAS_BILLING_PORTAL_CREATED",
            resource_type="TENANT_SUBSCRIPTION",
            resource_id=str(subscription.id),
            details={"provider": result.provider},
            request=request,
        )
        await session.commit()
    except (BillingProviderError, PlatformControlPlaneError) as exc:
        await session.rollback()
        raise _billing_http_error(exc) from exc
    return BillingRedirectResponse(
        provider=result.provider,
        session_id=result.external_session_id,
        url=result.url,
        expires_at=None,
    )


@router.post("/billing/webhooks/stripe", response_model=BillingWebhookResponse)
async def stripe_billing_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_db_session),
) -> BillingWebhookResponse:
    payload = await request.body()
    try:
        adapter = get_billing_provider("stripe")
        verified = adapter.verify_webhook(payload, stripe_signature or "")
        result = await converge_verified_webhook(session, adapter=adapter, verified=verified)
        if result is None:
            await session.rollback()
            return BillingWebhookResponse(status="IGNORED", event_id=verified.external_event_id)
        await append_platform_audit(
            session,
            action="BILLING_WEBHOOK_DUPLICATE" if result.duplicate else "BILLING_WEBHOOK_APPLIED",
            resource_type="BILLING_EVENT",
            resource_id=str(result.event.id),
            tenant_id=result.event.tenant_id,
            details={
                "provider": result.event.provider,
                "event_type": result.event.event_type,
                "duplicate": result.duplicate,
            },
            ip_address=_client_ip(request),
            actor="BILLING_WEBHOOK:stripe",
        )
        await session.commit()
        return BillingWebhookResponse(
            status="DUPLICATE" if result.duplicate else "APPLIED",
            event_id=verified.external_event_id,
            duplicate=result.duplicate,
        )
    except BillingWebhookVerificationError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc
    except BillingProviderError as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)}) from exc
    except PlatformControlPlaneError as exc:
        await session.rollback()
        raise _billing_http_error(exc) from exc
