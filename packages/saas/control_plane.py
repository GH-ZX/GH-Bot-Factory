from __future__ import annotations

import enum
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.saas.models import (
    BillingEvent,
    BillingEventStatus,
    BillingInterval,
    PlatformAuditLog,
    SaaSPlan,
    SaaSPlanPrice,
    SubscriptionStatus,
    TenantSubscription,
)
from packages.saas.service import (
    EntitlementConfigurationError,
    merge_entitlements,
    validate_entitlement_document,
)
from packages.tenants.models import Tenant

_PLAN_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
_SECRETISH = (
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
)


class PlatformControlPlaneError(RuntimeError):
    code = "CONTROL_PLANE_ERROR"


class PlatformNotFoundError(PlatformControlPlaneError):
    code = "NOT_FOUND"


class PlatformConflictError(PlatformControlPlaneError):
    code = "CONFLICT"


class PlatformValidationError(PlatformControlPlaneError):
    code = "VALIDATION_ERROR"


class BillingEventConflictError(PlatformConflictError):
    code = "BILLING_EVENT_CONFLICT"


@dataclass(frozen=True, slots=True)
class BillingConvergenceResult:
    event: BillingEvent
    subscription: TenantSubscription
    duplicate: bool


def normalize_plan_key(value: str) -> str:
    key = value.strip().lower()
    if not _PLAN_KEY_RE.fullmatch(key):
        raise PlatformValidationError(
            "Plan key must be 3-64 lowercase characters using letters, digits, and internal hyphens."
        )
    return key


def _ensure_safe_metadata(value: Any, *, path: str = "metadata") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(fragment in lowered for fragment in _SECRETISH):
                raise PlatformValidationError(
                    f"Refusing secret-like platform metadata field at {path}.{key_text}."
                )
            _ensure_safe_metadata(nested, path=f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _ensure_safe_metadata(nested, path=f"{path}[{index}]")


def _validate_plan_state(*, is_active: bool, is_public: bool) -> None:
    if is_public and not is_active:
        raise PlatformValidationError("A public plan must also be active.")


def validate_plan_entitlements(entitlements: dict[str, Any]) -> None:
    try:
        validate_entitlement_document(entitlements)
    except EntitlementConfigurationError as exc:
        raise PlatformValidationError(str(exc)) from exc


def validate_subscription_overrides(
    plan: SaaSPlan | dict[str, Any], overrides: dict[str, Any]
) -> None:
    plan_entitlements = plan.entitlements if isinstance(plan, SaaSPlan) else plan
    try:
        validate_entitlement_document(merge_entitlements(plan_entitlements or {}, overrides or {}))
    except EntitlementConfigurationError as exc:
        raise PlatformValidationError(str(exc)) from exc


async def create_plan(
    session: AsyncSession,
    *,
    key: str,
    name: str,
    description: str | None,
    is_active: bool,
    is_public: bool,
    entitlements: dict[str, Any],
    metadata_json: dict[str, Any],
) -> SaaSPlan:
    normalized_key = normalize_plan_key(key)
    normalized_name = name.strip()
    if not normalized_name:
        raise PlatformValidationError("Plan name cannot be blank.")
    validate_plan_entitlements(entitlements)
    _validate_plan_state(is_active=is_active, is_public=is_public)
    _ensure_safe_metadata(metadata_json)

    existing = await session.scalar(select(SaaSPlan).where(SaaSPlan.key == normalized_key))
    if existing is not None:
        raise PlatformConflictError(f"Plan key '{normalized_key}' already exists.")

    plan = SaaSPlan(
        key=normalized_key,
        name=normalized_name,
        description=description.strip() if description else None,
        is_active=is_active,
        is_public=is_public,
        entitlements=dict(entitlements),
        metadata_json=dict(metadata_json),
    )
    session.add(plan)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise PlatformConflictError(f"Plan key '{normalized_key}' already exists.") from exc
    return plan


async def update_plan(
    session: AsyncSession,
    *,
    plan_id: uuid.UUID,
    name: str | None = None,
    description: str | None = None,
    description_supplied: bool = False,
    is_active: bool | None = None,
    is_public: bool | None = None,
    entitlements: dict[str, Any] | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> SaaSPlan:
    plan = (
        await session.execute(select(SaaSPlan).where(SaaSPlan.id == plan_id).with_for_update())
    ).scalar_one_or_none()
    if plan is None:
        raise PlatformNotFoundError("SaaS plan was not found.")

    next_active = plan.is_active if is_active is None else is_active
    next_public = plan.is_public if is_public is None else is_public
    _validate_plan_state(is_active=next_active, is_public=next_public)

    if name is not None:
        normalized_name = name.strip()
        if not normalized_name:
            raise PlatformValidationError("Plan name cannot be blank.")
        plan.name = normalized_name
    if description_supplied:
        plan.description = description.strip() if description else None
    if entitlements is not None:
        validate_plan_entitlements(entitlements)
        # Do not silently make existing tenant overrides invalid.
        subscriptions = (
            await session.execute(
                select(TenantSubscription).where(TenantSubscription.plan_id == plan.id)
            )
        ).scalars().all()
        for subscription in subscriptions:
            validate_subscription_overrides(entitlements, subscription.entitlement_overrides or {})
        plan.entitlements = dict(entitlements)
    if metadata_json is not None:
        _ensure_safe_metadata(metadata_json)
        plan.metadata_json = dict(metadata_json)
    plan.is_active = next_active
    plan.is_public = next_public
    await session.flush()
    return plan


async def get_plan_by_key(
    session: AsyncSession,
    *,
    plan_key: str,
    require_active: bool = False,
) -> SaaSPlan:
    key = normalize_plan_key(plan_key)
    plan = await session.scalar(select(SaaSPlan).where(SaaSPlan.key == key))
    if plan is None:
        raise PlatformNotFoundError(f"Plan '{key}' was not found.")
    if require_active and not plan.is_active:
        raise PlatformValidationError(f"Plan '{key}' is retired and cannot be assigned to new subscriptions.")
    return plan


async def create_plan_price(
    session: AsyncSession,
    *,
    plan_id: uuid.UUID,
    provider: str,
    external_price_id: str,
    currency: str,
    unit_amount_minor: int,
    interval: BillingInterval,
    interval_count: int = 1,
    is_active: bool = True,
    metadata_json: dict[str, Any] | None = None,
) -> SaaSPlanPrice:
    plan = await session.get(SaaSPlan, plan_id)
    if plan is None:
        raise PlatformNotFoundError("SaaS plan was not found.")
    normalized_provider = provider.strip().lower()
    normalized_external = external_price_id.strip()
    normalized_currency = currency.strip().upper()
    if not normalized_provider or not normalized_external:
        raise PlatformValidationError("Billing provider and external price id are required.")
    if len(normalized_currency) != 3 or not normalized_currency.isalpha():
        raise PlatformValidationError("Billing currency must be a three-letter code.")
    if unit_amount_minor < 0:
        raise PlatformValidationError("Billing amount cannot be negative.")
    if interval_count < 1 or interval_count > 36:
        raise PlatformValidationError("Billing interval count must be between 1 and 36.")
    metadata = dict(metadata_json or {})
    _ensure_safe_metadata(metadata, path="price_metadata")
    price = SaaSPlanPrice(
        plan_id=plan.id,
        provider=normalized_provider,
        external_price_id=normalized_external,
        currency=normalized_currency,
        unit_amount_minor=unit_amount_minor,
        interval=interval,
        interval_count=interval_count,
        is_active=is_active,
        metadata_json=metadata,
    )
    session.add(price)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise PlatformConflictError(
            "That provider price id or plan/provider billing slot is already configured."
        ) from exc
    return price


async def update_plan_price(
    session: AsyncSession,
    *,
    price_id: uuid.UUID,
    is_active: bool | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> SaaSPlanPrice:
    price = (
        await session.execute(
            select(SaaSPlanPrice).where(SaaSPlanPrice.id == price_id).with_for_update()
        )
    ).scalar_one_or_none()
    if price is None:
        raise PlatformNotFoundError("SaaS plan price was not found.")
    if is_active is not None:
        price.is_active = is_active
    if metadata_json is not None:
        _ensure_safe_metadata(metadata_json, path="price_metadata")
        price.metadata_json = dict(metadata_json)
    await session.flush()
    return price


async def upsert_subscription(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    plan_key: str,
    status: SubscriptionStatus,
    billing_provider: str | None = None,
    external_customer_id: str | None = None,
    external_subscription_id: str | None = None,
    current_period_start: datetime | None = None,
    current_period_end: datetime | None = None,
    trial_ends_at: datetime | None = None,
    grace_ends_at: datetime | None = None,
    cancel_at_period_end: bool = False,
    entitlement_overrides: dict[str, Any] | None = None,
    billing_metadata: dict[str, Any] | None = None,
) -> TenantSubscription:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at is not None:
        raise PlatformNotFoundError("Tenant was not found.")

    subscription = (
        await session.execute(
            select(TenantSubscription)
            .where(TenantSubscription.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    plan = await get_plan_by_key(session, plan_key=plan_key, require_active=False)
    if not plan.is_active and (subscription is None or subscription.plan_id != plan.id):
        raise PlatformValidationError(
            f"Plan '{plan.key}' is retired and cannot be assigned to new subscriptions."
        )

    overrides = dict(entitlement_overrides or {})
    metadata = dict(billing_metadata or {})
    validate_subscription_overrides(plan, overrides)
    _ensure_safe_metadata(metadata, path="billing_metadata")

    provider = billing_provider.strip().lower() if billing_provider else None
    external_customer = external_customer_id.strip() if external_customer_id else None
    external_subscription = external_subscription_id.strip() if external_subscription_id else None
    if external_subscription and not provider:
        raise PlatformValidationError(
            "billing_provider is required when external_subscription_id is configured."
        )
    if current_period_start and current_period_end and current_period_start > current_period_end:
        raise PlatformValidationError("current_period_start cannot be later than current_period_end.")

    if subscription is None:
        subscription = TenantSubscription(tenant_id=tenant_id, plan_id=plan.id)
        session.add(subscription)

    subscription.plan_id = plan.id
    subscription.status = status
    subscription.billing_provider = provider
    subscription.external_customer_id = external_customer
    subscription.external_subscription_id = external_subscription
    subscription.current_period_start = current_period_start
    subscription.current_period_end = current_period_end
    subscription.trial_ends_at = trial_ends_at
    subscription.grace_ends_at = grace_ends_at
    subscription.cancel_at_period_end = cancel_at_period_end
    subscription.entitlement_overrides = overrides
    subscription.billing_metadata = metadata
    try:
        await session.flush()
    except IntegrityError as exc:
        raise PlatformConflictError(
            "The external billing subscription identifier is already assigned."
        ) from exc
    return subscription


async def clear_subscription(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> TenantSubscription:
    subscription = (
        await session.execute(
            select(TenantSubscription)
            .options(selectinload(TenantSubscription.plan))
            .where(TenantSubscription.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if subscription is None:
        raise PlatformNotFoundError("Tenant does not have a SaaS subscription.")
    await session.delete(subscription)
    await session.flush()
    return subscription


def _canonical_billing_value(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _canonical_billing_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_billing_value(nested) for nested in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    return value


def _billing_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_billing_value(payload),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def converge_billing_event(
    session: AsyncSession,
    *,
    provider: str,
    external_event_id: str,
    event_type: str,
    tenant_id: uuid.UUID,
    plan_key: str,
    status: SubscriptionStatus,
    external_customer_id: str | None = None,
    external_subscription_id: str | None = None,
    current_period_start: datetime | None = None,
    current_period_end: datetime | None = None,
    trial_ends_at: datetime | None = None,
    grace_ends_at: datetime | None = None,
    cancel_at_period_end: bool = False,
    entitlement_overrides: dict[str, Any] | None = None,
    billing_metadata: dict[str, Any] | None = None,
    event_metadata: dict[str, Any] | None = None,
) -> BillingConvergenceResult:
    """Idempotently converge a normalized signed-provider event into subscription state.

    This function is the future webhook adapter target. It never accepts or stores raw
    provider payloads, secrets, or browser checkout state.
    """

    normalized_provider = provider.strip().lower()
    normalized_event_id = external_event_id.strip()
    normalized_event_type = event_type.strip()
    if not normalized_provider or not normalized_event_id or not normalized_event_type:
        raise PlatformValidationError("Billing provider, external event id, and event type are required.")
    safe_event_metadata = dict(event_metadata or {})
    _ensure_safe_metadata(safe_event_metadata, path="event_metadata")

    fingerprint_payload = {
        "provider": normalized_provider,
        "external_event_id": normalized_event_id,
        "event_type": normalized_event_type,
        "tenant_id": str(tenant_id),
        "plan_key": normalize_plan_key(plan_key),
        "status": status.value,
        "external_customer_id": external_customer_id,
        "external_subscription_id": external_subscription_id,
        "current_period_start": current_period_start,
        "current_period_end": current_period_end,
        "trial_ends_at": trial_ends_at,
        "grace_ends_at": grace_ends_at,
        "cancel_at_period_end": cancel_at_period_end,
        "entitlement_overrides": entitlement_overrides or {},
        "billing_metadata": billing_metadata or {},
        "event_metadata": safe_event_metadata,
    }
    payload_sha256 = _billing_fingerprint(fingerprint_payload)

    existing = (
        await session.execute(
            select(BillingEvent).where(
                BillingEvent.provider == normalized_provider,
                BillingEvent.external_event_id == normalized_event_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.payload_sha256 != payload_sha256:
            raise BillingEventConflictError(
                "Billing event id was reused with a different normalized payload."
            )
        subscription = (
            await session.execute(
                select(TenantSubscription)
                .options(selectinload(TenantSubscription.plan))
                .where(TenantSubscription.id == existing.subscription_id)
            )
        ).scalar_one_or_none()
        if subscription is None:
            raise PlatformConflictError("Recorded billing event references a missing subscription.")
        return BillingConvergenceResult(event=existing, subscription=subscription, duplicate=True)

    event = BillingEvent(
        provider=normalized_provider,
        external_event_id=normalized_event_id,
        event_type=normalized_event_type,
        payload_sha256=payload_sha256,
        status=BillingEventStatus.RECEIVED,
        tenant_id=tenant_id,
        event_metadata=safe_event_metadata,
        attempts=1,
    )
    try:
        async with session.begin_nested():
            session.add(event)
            await session.flush()
    except IntegrityError:
        # Another request may have won the unique constraint after our initial read.
        # The savepoint keeps the outer transaction usable so we can converge safely.
        concurrent = (
            await session.execute(
                select(BillingEvent).where(
                    BillingEvent.provider == normalized_provider,
                    BillingEvent.external_event_id == normalized_event_id,
                )
            )
        ).scalar_one_or_none()
        if concurrent is None:
            raise PlatformConflictError("Billing event is already being processed; retry safely.")
        if concurrent.payload_sha256 != payload_sha256:
            raise BillingEventConflictError(
                "Billing event id was reused with a different normalized payload."
            )
        subscription = (
            await session.execute(
                select(TenantSubscription)
                .options(selectinload(TenantSubscription.plan))
                .where(TenantSubscription.id == concurrent.subscription_id)
            )
        ).scalar_one_or_none()
        if subscription is None:
            raise PlatformConflictError("Billing event is still being processed; retry safely.")
        return BillingConvergenceResult(event=concurrent, subscription=subscription, duplicate=True)

    try:
        async with session.begin_nested():
            subscription = await upsert_subscription(
                session,
                tenant_id=tenant_id,
                plan_key=plan_key,
                status=status,
                billing_provider=normalized_provider,
                external_customer_id=external_customer_id,
                external_subscription_id=external_subscription_id,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
                trial_ends_at=trial_ends_at,
                grace_ends_at=grace_ends_at,
                cancel_at_period_end=cancel_at_period_end,
                entitlement_overrides=entitlement_overrides,
                billing_metadata=billing_metadata,
            )
    except Exception as exc:
        event.status = BillingEventStatus.FAILED
        event.last_error = str(exc)[:1000]
        event.processed_at = datetime.now(UTC)
        await session.flush()
        raise

    event.status = BillingEventStatus.APPLIED
    event.subscription_id = subscription.id
    event.processed_at = datetime.now(UTC)
    event.last_error = None
    await session.flush()
    return BillingConvergenceResult(event=event, subscription=subscription, duplicate=False)


async def append_platform_audit(
    session: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    tenant_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    actor: str = "LOCAL_PLATFORM_TOKEN",
) -> PlatformAuditLog:
    safe_details = dict(details or {})
    _ensure_safe_metadata(safe_details, path="audit_details")
    record = PlatformAuditLog(
        actor=actor[:120],
        action=action[:100],
        resource_type=resource_type[:100],
        resource_id=resource_id[:100],
        tenant_id=tenant_id,
        details=safe_details,
        ip_address=ip_address,
    )
    session.add(record)
    await session.flush()
    return record


async def platform_counts(session: AsyncSession) -> dict[str, int]:
    tenant_count = int(await session.scalar(select(func.count()).select_from(Tenant)) or 0)
    active_tenants = int(
        await session.scalar(
            select(func.count()).select_from(Tenant).where(
                Tenant.deleted_at.is_(None), Tenant.is_active.is_(True)
            )
        )
        or 0
    )
    plans = int(await session.scalar(select(func.count()).select_from(SaaSPlan)) or 0)
    active_plans = int(
        await session.scalar(select(func.count()).select_from(SaaSPlan).where(SaaSPlan.is_active.is_(True)))
        or 0
    )
    subscriptions = int(await session.scalar(select(func.count()).select_from(TenantSubscription)) or 0)
    billing_events = int(await session.scalar(select(func.count()).select_from(BillingEvent)) or 0)
    return {
        "tenants": tenant_count,
        "active_tenants": active_tenants,
        "plans": plans,
        "active_plans": active_plans,
        "subscriptions": subscriptions,
        "billing_events": billing_events,
    }
