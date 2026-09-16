from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import settings
from packages.saas.models import SubscriptionStatus, TenantSubscription
from packages.saas.product_entitlements import (
    CommercialAccessState,
    commercial_access_for_subscription,
    resolve_product_entitlements,
)
from packages.saas.service import resolve_tenant_usage
from packages.tenants.models import Tenant


@dataclass(frozen=True, slots=True)
class SaaSOperationsAlert:
    severity: str
    code: str
    tenant_id: uuid.UUID
    tenant_slug: str
    message: str
    action: str


@dataclass(frozen=True, slots=True)
class SaaSHealthSnapshot:
    total_tenants: int
    self_hosted_tenants: int
    subscribed_tenants: int
    active_access: int
    grace_access: int
    blocked_access: int
    provider_managed_subscriptions: int
    stale_provider_syncs: int
    status_counts: dict[str, int]
    alerts: tuple[SaaSOperationsAlert, ...]


@dataclass(frozen=True, slots=True)
class TenantSaaSInspection:
    tenant: Tenant
    subscription: TenantSubscription | None
    source: str
    plan_key: str | None
    plan_name: str | None
    configured_features: dict[str, bool]
    effective_features: dict[str, bool]
    access_state: str
    access_allowed: bool
    access_reason: str
    grace_ends_at: datetime | None
    usage_bots: int
    usage_enabled_bots: int
    usage_open_jobs: int
    max_bots: int
    max_enabled_bots: int
    max_open_jobs: int


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def collect_saas_health(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    stale_seconds: int | None = None,
) -> SaaSHealthSnapshot:
    current = _utc(now) or datetime.now(UTC)
    stale_after = timedelta(seconds=stale_seconds or settings.billing_sync_stale_seconds)

    tenants = list(
        (
            await session.execute(
                select(Tenant)
                .where(Tenant.deleted_at.is_(None))
                .order_by(Tenant.created_at.asc(), Tenant.id.asc())
            )
        ).scalars().all()
    )
    subscriptions = list((await session.execute(select(TenantSubscription))).scalars().all())
    by_tenant = {subscription.tenant_id: subscription for subscription in subscriptions}

    active_access = 0
    grace_access = 0
    blocked_access = 0
    provider_managed = 0
    stale_syncs = 0
    status_counts: dict[str, int] = {}
    alerts: list[SaaSOperationsAlert] = []

    tenant_by_id = {tenant.id: tenant for tenant in tenants}
    for subscription in subscriptions:
        tenant = tenant_by_id.get(subscription.tenant_id)
        if tenant is None:
            continue
        status_counts[subscription.status.value] = status_counts.get(subscription.status.value, 0) + 1
        access = commercial_access_for_subscription(subscription, now=current)
        if access.state == CommercialAccessState.ACTIVE:
            active_access += 1
        elif access.state == CommercialAccessState.GRACE:
            grace_access += 1
            alerts.append(
                SaaSOperationsAlert(
                    severity="WARN",
                    code="SUBSCRIPTION_IN_GRACE",
                    tenant_id=tenant.id,
                    tenant_slug=tenant.slug,
                    message=(
                        f"Subscription is PAST_DUE and grace ends at {access.grace_ends_at.isoformat()}."
                        if access.grace_ends_at
                        else "Subscription is PAST_DUE."
                    ),
                    action="Open the provider/customer billing state and reconcile after payment recovery.",
                )
            )
        elif access.state == CommercialAccessState.BLOCKED:
            blocked_access += 1
            if subscription.status != SubscriptionStatus.CANCELED:
                alerts.append(
                    SaaSOperationsAlert(
                        severity="CRITICAL" if subscription.status == SubscriptionStatus.PAST_DUE else "WARN",
                        code="COMMERCIAL_ACCESS_BLOCKED",
                        tenant_id=tenant.id,
                        tenant_slug=tenant.slug,
                        message=access.reason,
                        action="Inspect normalized subscription state and provider synchronization before manual changes.",
                    )
                )

        if subscription.billing_provider and subscription.external_subscription_id:
            provider_managed += 1
            if subscription.provider_sync_error:
                alerts.append(
                    SaaSOperationsAlert(
                        severity="WARN",
                        code="PROVIDER_SYNC_ERROR",
                        tenant_id=tenant.id,
                        tenant_slug=tenant.slug,
                        message=f"Last provider reconciliation error: {subscription.provider_sync_error[:300]}",
                        action="Run billing reconciliation and inspect provider connectivity/catalog mapping.",
                    )
                )
            synced_at = _utc(subscription.provider_synced_at)
            is_stale = synced_at is None or current - synced_at > stale_after
            if is_stale and subscription.status != SubscriptionStatus.CANCELED:
                stale_syncs += 1
                alerts.append(
                    SaaSOperationsAlert(
                        severity="WARN",
                        code="PROVIDER_SYNC_STALE",
                        tenant_id=tenant.id,
                        tenant_slug=tenant.slug,
                        message=(
                            "Provider-managed subscription has never completed a verified synchronization."
                            if synced_at is None
                            else f"Provider synchronization is stale; last success was {synced_at.isoformat()}."
                        ),
                        action="Run local pull reconciliation; public webhook ingress is not required.",
                    )
                )

    severity_rank = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
    alerts.sort(key=lambda item: (severity_rank.get(item.severity, 9), item.tenant_slug, item.code))
    return SaaSHealthSnapshot(
        total_tenants=len(tenants),
        self_hosted_tenants=sum(1 for tenant in tenants if tenant.id not in by_tenant),
        subscribed_tenants=sum(1 for tenant in tenants if tenant.id in by_tenant),
        active_access=active_access,
        grace_access=grace_access,
        blocked_access=blocked_access,
        provider_managed_subscriptions=provider_managed,
        stale_provider_syncs=stale_syncs,
        status_counts=status_counts,
        alerts=tuple(alerts),
    )


async def inspect_tenant_saas(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> TenantSaaSInspection | None:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at is not None:
        return None
    subscription = await session.scalar(
        select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
    )
    product = await resolve_product_entitlements(session, tenant_id=tenant_id)
    usage = await resolve_tenant_usage(session, tenant_id=tenant_id)
    entitlements = product.entitlements
    return TenantSaaSInspection(
        tenant=tenant,
        subscription=subscription,
        source=entitlements.source,
        plan_key=entitlements.plan_key,
        plan_name=entitlements.plan_name,
        configured_features=dict(entitlements.features),
        effective_features=dict(product.effective_features),
        access_state=product.access.state.value,
        access_allowed=product.access.allowed,
        access_reason=product.access.reason,
        grace_ends_at=product.access.grace_ends_at,
        usage_bots=usage.bots,
        usage_enabled_bots=usage.enabled_bots,
        usage_open_jobs=usage.open_provisioning_jobs,
        max_bots=entitlements.max_bots,
        max_enabled_bots=entitlements.max_enabled_bots,
        max_open_jobs=entitlements.max_open_provisioning_jobs,
    )
