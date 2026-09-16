from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.core.config import settings
from packages.saas.billing import (
    BillingProviderAdapter,
    BillingProviderError,
    ProviderSubscriptionState,
    VerifiedWebhookEvent,
)
from packages.saas.control_plane import (
    BillingConvergenceResult,
    PlatformConflictError,
    PlatformNotFoundError,
    PlatformValidationError,
    converge_billing_event,
)
from packages.saas.models import SaaSPlan, SaaSPlanPrice, SubscriptionStatus, TenantSubscription


@dataclass(frozen=True, slots=True)
class BillingCatalogItem:
    price: SaaSPlanPrice
    plan: SaaSPlan


@dataclass(frozen=True, slots=True)
class BillingReconcileSummary:
    provider: str
    scanned: int
    applied: int
    duplicates: int
    failed: int
    errors: tuple[str, ...]


async def list_public_billing_catalog(
    session: AsyncSession,
    *,
    provider: str,
) -> list[BillingCatalogItem]:
    normalized_provider = provider.strip().lower()
    rows = (
        await session.execute(
            select(SaaSPlanPrice, SaaSPlan)
            .join(SaaSPlan, SaaSPlan.id == SaaSPlanPrice.plan_id)
            .where(
                SaaSPlan.is_active.is_(True),
                SaaSPlan.is_public.is_(True),
                SaaSPlanPrice.is_active.is_(True),
                SaaSPlanPrice.provider == normalized_provider,
            )
            .order_by(
                SaaSPlan.created_at.asc(),
                SaaSPlan.key.asc(),
                SaaSPlanPrice.unit_amount_minor.asc(),
            )
        )
    ).all()
    return [BillingCatalogItem(price=price, plan=plan) for price, plan in rows]


async def resolve_checkout_price(
    session: AsyncSession,
    *,
    price_id: uuid.UUID,
    provider: str,
) -> BillingCatalogItem:
    row = (
        await session.execute(
            select(SaaSPlanPrice, SaaSPlan)
            .join(SaaSPlan, SaaSPlan.id == SaaSPlanPrice.plan_id)
            .where(SaaSPlanPrice.id == price_id)
        )
    ).one_or_none()
    if row is None:
        raise PlatformNotFoundError("Billing price was not found.")
    price, plan = row
    if price.provider != provider.strip().lower():
        raise PlatformValidationError("Billing price belongs to another provider.")
    if not price.is_active or not plan.is_active or not plan.is_public:
        raise PlatformValidationError("Billing price is not currently available for checkout.")
    return BillingCatalogItem(price=price, plan=plan)


async def get_tenant_subscription(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> TenantSubscription | None:
    return (
        await session.execute(
            select(TenantSubscription)
            .options(selectinload(TenantSubscription.plan))
            .where(TenantSubscription.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()


def ensure_checkout_allowed(subscription: TenantSubscription | None) -> None:
    if subscription is None:
        return
    if subscription.status == SubscriptionStatus.CANCELED:
        return
    if subscription.billing_provider and subscription.external_subscription_id:
        raise PlatformConflictError(
            "Tenant already has a provider-managed subscription; use the billing portal instead."
        )
    raise PlatformConflictError(
        "Tenant already has a platform-managed subscription. Clear or migrate it from the platform control plane before hosted checkout."
    )


async def converge_verified_webhook(
    session: AsyncSession,
    *,
    adapter: BillingProviderAdapter,
    verified: VerifiedWebhookEvent,
) -> BillingConvergenceResult | None:
    state = verified.subscription_state
    if state is None and verified.external_subscription_id:
        state = await adapter.fetch_subscription(verified.external_subscription_id)
        if verified.tenant_id is not None and state.tenant_id != verified.tenant_id:
            raise PlatformConflictError("Billing checkout tenant metadata does not match subscription metadata.")
    if state is None:
        return None
    return await converge_provider_subscription_state(
        session,
        state=state,
        external_event_id=verified.external_event_id,
        event_type=verified.event_type,
        source="SIGNED_WEBHOOK",
    )


async def converge_provider_subscription_state(
    session: AsyncSession,
    *,
    state: ProviderSubscriptionState,
    external_event_id: str | None = None,
    event_type: str | None = None,
    source: str,
) -> BillingConvergenceResult:
    price = (
        await session.execute(
            select(SaaSPlanPrice)
            .options(selectinload(SaaSPlanPrice.plan))
            .where(
                SaaSPlanPrice.provider == state.provider,
                SaaSPlanPrice.external_price_id == state.external_price_id,
            )
        )
    ).scalar_one_or_none()
    if price is None or price.plan is None:
        raise PlatformValidationError(
            "Provider subscription references an unmapped SaaS price. Configure the price in the platform catalog first."
        )

    existing = await get_tenant_subscription(session, tenant_id=state.tenant_id)
    grace_ends_at = None
    if state.status == SubscriptionStatus.PAST_DUE:
        if existing is not None and existing.status == SubscriptionStatus.PAST_DUE and existing.grace_ends_at:
            grace_ends_at = existing.grace_ends_at
        else:
            grace_ends_at = state.provider_created_at + timedelta(days=settings.billing_past_due_grace_days)

    effective_event_id = external_event_id or _reconciliation_event_id(state)
    safe_event_type = event_type or state.event_type
    event_metadata = {
        **(state.event_metadata or {}),
        "source": source,
        "external_price_id": state.external_price_id,
    }
    billing_metadata: dict[str, Any] = {
        "external_price_id": state.external_price_id,
        "catalog_price_id": str(price.id),
        "billing_interval": price.interval.value,
        "billing_interval_count": price.interval_count,
        "currency": price.currency,
        "unit_amount_minor": price.unit_amount_minor,
    }
    result = await converge_billing_event(
        session,
        provider=state.provider,
        external_event_id=effective_event_id,
        event_type=safe_event_type,
        tenant_id=state.tenant_id,
        plan_key=price.plan.key,
        status=state.status,
        external_customer_id=state.external_customer_id,
        external_subscription_id=state.external_subscription_id,
        current_period_start=state.current_period_start,
        current_period_end=state.current_period_end,
        trial_ends_at=state.trial_ends_at,
        grace_ends_at=grace_ends_at,
        cancel_at_period_end=state.cancel_at_period_end,
        billing_metadata=billing_metadata,
        event_metadata=event_metadata,
    )
    result.subscription.provider_synced_at = datetime.now(UTC)
    result.subscription.provider_sync_source = source[:50]
    result.subscription.provider_sync_error = None
    result.subscription.provider_sync_error_at = None
    await session.flush()
    return result


async def reconcile_provider_subscriptions(
    session: AsyncSession,
    *,
    adapter: BillingProviderAdapter,
    limit: int = 200,
) -> BillingReconcileSummary:
    provider = adapter.provider_name.strip().lower()
    subscriptions = (
        await session.execute(
            select(TenantSubscription)
            .where(
                TenantSubscription.billing_provider == provider,
                TenantSubscription.external_subscription_id.is_not(None),
            )
            .order_by(TenantSubscription.updated_at.asc(), TenantSubscription.id.asc())
            .limit(limit)
        )
    ).scalars().all()
    applied = 0
    duplicates = 0
    failed = 0
    errors: list[str] = []
    for subscription in subscriptions:
        external_id = subscription.external_subscription_id
        if not external_id:
            continue
        try:
            state = await adapter.fetch_subscription(external_id)
            if state.tenant_id != subscription.tenant_id:
                raise PlatformConflictError(
                    f"Provider subscription {external_id} resolved to a different tenant."
                )
            result = await converge_provider_subscription_state(
                session,
                state=state,
                source="PULL_RECONCILIATION",
            )
            if result.duplicate:
                duplicates += 1
            else:
                applied += 1
        except (BillingProviderError, PlatformValidationError, PlatformConflictError) as exc:
            failed += 1
            message = str(exc)[:1000]
            subscription.provider_sync_error = message
            subscription.provider_sync_error_at = datetime.now(UTC)
            errors.append(f"{external_id}: {message[:240]}")
    return BillingReconcileSummary(
        provider=provider,
        scanned=len(subscriptions),
        applied=applied,
        duplicates=duplicates,
        failed=failed,
        errors=tuple(errors[:20]),
    )


def _reconciliation_event_id(state: ProviderSubscriptionState) -> str:
    payload = {
        "subscription_id": state.external_subscription_id,
        "tenant_id": str(state.tenant_id),
        "price_id": state.external_price_id,
        "status": state.status.value,
        "current_period_start": state.current_period_start,
        "current_period_end": state.current_period_end,
        "trial_ends_at": state.trial_ends_at,
        "cancel_at_period_end": state.cancel_at_period_end,
        "revision": state.revision,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return f"sync:{state.external_subscription_id}:{digest}"
