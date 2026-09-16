from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.core.config import settings
from packages.factory.models import BotProvisioningJob, BotProvisioningStatus
from packages.saas.models import TenantSubscription
from packages.telegram.models import Bot


class EntitlementConfigurationError(RuntimeError):
    """Raised when a persisted SaaS plan contains unsafe or incomplete limits."""


@dataclass(frozen=True, slots=True)
class EntitlementSnapshot:
    source: str
    plan_key: str | None
    plan_name: str | None
    subscription_status: str | None
    max_bots: int
    max_enabled_bots: int
    max_open_provisioning_jobs: int
    features: dict[str, bool]


@dataclass(frozen=True, slots=True)
class TenantUsageSnapshot:
    bots: int
    enabled_bots: int
    open_provisioning_jobs: int


def merge_entitlements(plan: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(plan or {})
    for key, value in (overrides or {}).items():
        if key == "features" and isinstance(value, dict):
            current = merged.get("features")
            feature_map = dict(current) if isinstance(current, dict) else {}
            feature_map.update(value)
            merged["features"] = feature_map
        else:
            merged[key] = value
    return merged


def _required_positive_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EntitlementConfigurationError(
            f"SaaS entitlement '{key}' must be a positive integer for subscribed tenants."
        )
    return value


def _feature_flags(data: dict[str, Any]) -> dict[str, bool]:
    raw = data.get("features") or {}
    if not isinstance(raw, dict):
        raise EntitlementConfigurationError("SaaS entitlement 'features' must be an object.")
    result: dict[str, bool] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, bool):
            raise EntitlementConfigurationError(
                "SaaS feature flags must map non-empty string keys to booleans."
            )
        result[key] = value
    return result


def validate_entitlement_document(data: dict[str, Any]) -> None:
    """Validate a complete effective entitlement document.

    Plan definitions must contain all current hard capacity keys. Tenant override
    objects may be partial, but callers must merge them over the plan before calling
    this function.
    """

    if not isinstance(data, dict):
        raise EntitlementConfigurationError("SaaS entitlements must be an object.")
    max_bots = _required_positive_int(data, "max_bots")
    max_enabled_bots = _required_positive_int(data, "max_enabled_bots")
    _required_positive_int(data, "max_open_provisioning_jobs")
    if max_enabled_bots > max_bots:
        raise EntitlementConfigurationError(
            "SaaS entitlement 'max_enabled_bots' cannot exceed 'max_bots'."
        )
    _feature_flags(data)


async def resolve_tenant_entitlements(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> EntitlementSnapshot:
    """Resolve authoritative tenant limits.

    Existing self-hosted tenants with no SaaS subscription retain the Phase 8
    environment-configured limits. Once a tenant has a subscription row, its plan
    becomes authoritative and malformed plan limits fail closed.

    An inactive plan means "retired from new assignment", not "revoke existing
    subscribers". Existing subscriptions therefore continue resolving against a
    retired plan until the platform control plane moves or clears them.
    """

    subscription = (
        await session.execute(
            select(TenantSubscription)
            .options(selectinload(TenantSubscription.plan))
            .where(TenantSubscription.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()

    if subscription is None:
        return EntitlementSnapshot(
            source="SELF_HOSTED_DEFAULTS",
            plan_key=None,
            plan_name="Self-hosted",
            subscription_status=None,
            max_bots=settings.factory_max_bots_per_tenant,
            max_enabled_bots=settings.factory_max_enabled_bots_per_tenant,
            max_open_provisioning_jobs=settings.factory_max_open_provisioning_jobs_per_tenant,
            features={},
        )

    plan = subscription.plan
    if plan is None:
        raise EntitlementConfigurationError("Tenant subscription references a missing SaaS plan.")

    merged = merge_entitlements(plan.entitlements or {}, subscription.entitlement_overrides or {})
    validate_entitlement_document(merged)

    return EntitlementSnapshot(
        source="SAAS_PLAN",
        plan_key=plan.key,
        plan_name=plan.name,
        subscription_status=subscription.status.value,
        max_bots=_required_positive_int(merged, "max_bots"),
        max_enabled_bots=_required_positive_int(merged, "max_enabled_bots"),
        max_open_provisioning_jobs=_required_positive_int(merged, "max_open_provisioning_jobs"),
        features=_feature_flags(merged),
    )


async def resolve_tenant_usage(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> TenantUsageSnapshot:
    bots = int(
        await session.scalar(
            select(func.count()).select_from(Bot).where(
                Bot.tenant_id == tenant_id,
                Bot.deleted_at.is_(None),
            )
        )
        or 0
    )
    enabled_bots = int(
        await session.scalar(
            select(func.count()).select_from(Bot).where(
                Bot.tenant_id == tenant_id,
                Bot.deleted_at.is_(None),
                Bot.is_enabled.is_(True),
            )
        )
        or 0
    )
    open_provisioning_jobs = int(
        await session.scalar(
            select(func.count()).select_from(BotProvisioningJob).where(
                BotProvisioningJob.tenant_id == tenant_id,
                BotProvisioningJob.status.in_(
                    [
                        BotProvisioningStatus.PENDING,
                        BotProvisioningStatus.RUNNING,
                        BotProvisioningStatus.RETRY,
                    ]
                ),
            )
        )
        or 0
    )
    return TenantUsageSnapshot(
        bots=bots,
        enabled_bots=enabled_bots,
        open_provisioning_jobs=open_provisioning_jobs,
    )
