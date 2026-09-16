from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.saas.models import SubscriptionStatus, TenantSubscription
from packages.saas.service import EntitlementSnapshot, resolve_tenant_entitlements

FEATURE_CUSTOM_BRANDING = "custom_branding"
FEATURE_CANARY_ROLLOUT = "canary_rollout"
FEATURE_RUNTIME_CONTROLS = "runtime_controls"

PRODUCT_FEATURES: dict[str, str] = {
    FEATURE_CUSTOM_BRANDING: "Custom per-bot branding",
    FEATURE_CANARY_ROLLOUT: "Bot runtime canary release channels",
    FEATURE_RUNTIME_CONTROLS: "Manual bot runtime restart controls",
}


class CommercialAccessState(str, enum.Enum):
    SELF_HOSTED = "SELF_HOSTED"
    ACTIVE = "ACTIVE"
    GRACE = "GRACE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class CommercialAccessSnapshot:
    state: CommercialAccessState
    allowed: bool
    reason: str
    subscription_status: SubscriptionStatus | None
    grace_ends_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProductEntitlementSnapshot:
    entitlements: EntitlementSnapshot
    access: CommercialAccessSnapshot
    effective_features: dict[str, bool]


class CommercialAccessDenied(RuntimeError):
    def __init__(self, snapshot: CommercialAccessSnapshot) -> None:
        self.snapshot = snapshot
        super().__init__(snapshot.reason)


class FeatureEntitlementDenied(RuntimeError):
    def __init__(self, feature: str) -> None:
        self.feature = feature
        super().__init__(f"Feature '{feature}' is not included in the tenant's SaaS plan.")


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def commercial_access_for_subscription(
    subscription: TenantSubscription | None,
    *,
    now: datetime | None = None,
) -> CommercialAccessSnapshot:
    """Resolve commercial access without silently disabling self-hosted installs.

    No subscription means the installation is using the explicit self-hosted fallback and
    remains fully usable. Provider-managed SaaS subscriptions are fail-closed once their
    normalized status is no longer commercially valid.
    """

    current = _utc(now) or datetime.now(UTC)
    if subscription is None:
        return CommercialAccessSnapshot(
            state=CommercialAccessState.SELF_HOSTED,
            allowed=True,
            reason="Self-hosted tenant has no commercial subscription gate.",
            subscription_status=None,
            grace_ends_at=None,
        )

    if subscription.status in {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING}:
        return CommercialAccessSnapshot(
            state=CommercialAccessState.ACTIVE,
            allowed=True,
            reason=f"Subscription status {subscription.status.value} is commercially active.",
            subscription_status=subscription.status,
            grace_ends_at=_utc(subscription.grace_ends_at),
        )

    if subscription.status == SubscriptionStatus.PAST_DUE:
        grace_end = _utc(subscription.grace_ends_at)
        if grace_end is not None and current < grace_end:
            return CommercialAccessSnapshot(
                state=CommercialAccessState.GRACE,
                allowed=True,
                reason="Subscription is past due but remains inside the configured grace period.",
                subscription_status=subscription.status,
                grace_ends_at=grace_end,
            )
        return CommercialAccessSnapshot(
            state=CommercialAccessState.BLOCKED,
            allowed=False,
            reason=(
                "Subscription past-due grace period has ended."
                if grace_end is not None
                else "Subscription is past due and has no verified grace deadline."
            ),
            subscription_status=subscription.status,
            grace_ends_at=grace_end,
        )

    return CommercialAccessSnapshot(
        state=CommercialAccessState.BLOCKED,
        allowed=False,
        reason=f"Subscription status {subscription.status.value} does not allow commercial mutations.",
        subscription_status=subscription.status,
        grace_ends_at=_utc(subscription.grace_ends_at),
    )


async def resolve_commercial_access(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    now: datetime | None = None,
) -> CommercialAccessSnapshot:
    subscription = await session.scalar(
        select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
    )
    return commercial_access_for_subscription(subscription, now=now)


def effective_feature_flags(
    entitlements: EntitlementSnapshot,
    access: CommercialAccessSnapshot,
) -> dict[str, bool]:
    """Return server-authoritative effective feature flags.

    Self-hosted operation predates SaaS feature flags and therefore keeps all known product
    features available. Subscribed tenants must both own the feature in their plan and have
    currently allowed commercial access.
    """

    configured = dict(entitlements.features)
    keys = set(PRODUCT_FEATURES) | set(configured)
    if entitlements.source == "SELF_HOSTED_DEFAULTS":
        return {key: True for key in sorted(keys)}
    return {
        key: bool(configured.get(key, False)) and access.allowed
        for key in sorted(keys)
    }


async def resolve_product_entitlements(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    now: datetime | None = None,
) -> ProductEntitlementSnapshot:
    entitlements = await resolve_tenant_entitlements(session, tenant_id=tenant_id)
    access = await resolve_commercial_access(session, tenant_id=tenant_id, now=now)
    return ProductEntitlementSnapshot(
        entitlements=entitlements,
        access=access,
        effective_features=effective_feature_flags(entitlements, access),
    )


async def require_commercial_access(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    now: datetime | None = None,
) -> CommercialAccessSnapshot:
    access = await resolve_commercial_access(session, tenant_id=tenant_id, now=now)
    if not access.allowed:
        raise CommercialAccessDenied(access)
    return access


async def require_feature_entitlement(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    feature: str,
    now: datetime | None = None,
) -> ProductEntitlementSnapshot:
    snapshot = await resolve_product_entitlements(session, tenant_id=tenant_id, now=now)
    if not snapshot.access.allowed:
        raise CommercialAccessDenied(snapshot.access)
    if snapshot.entitlements.source != "SELF_HOSTED_DEFAULTS" and not snapshot.effective_features.get(feature, False):
        raise FeatureEntitlementDenied(feature)
    return snapshot
