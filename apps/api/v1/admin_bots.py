from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_admin_or_owner, require_staff_or_above
from packages.commerce.economics_models import PricingTier
from packages.commerce.models import Product, ProductVariant
from packages.core.auth import AuthenticatedPrincipal
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.factory.business_profiles import (
    allowed_provider_categories,
    business_profile_from_config,
    parse_business_profile,
    validate_business_profile_for_tenant,
)
from packages.factory.models import BotProvisioningJob, BotProvisioningStatus
from packages.factory.provisioning import (
    AiogramTelegramIdentityVerifier,
    ProvisioningError,
    TelegramIdentityVerifier,
    normalize_expected_username,
    provisioning_fingerprint,
    reject_secret_material,
    validate_secret_ref,
)
from packages.factory.templates import (
    TemplateValidationError,
    build_template_config,
    list_bot_templates,
    template_metadata,
)
from packages.payments.models import PaymentMethodConfig
from packages.providers.models import Provider, ProviderProductMapping, ProviderRoutingStrategy
from packages.saas.product_entitlements import (
    FEATURE_CANARY_ROLLOUT,
    FEATURE_CUSTOM_BRANDING,
    FEATURE_RUNTIME_CONTROLS,
    CommercialAccessDenied,
    FeatureEntitlementDenied,
    require_commercial_access,
    require_feature_entitlement,
    resolve_commercial_access,
)
from packages.saas.service import EntitlementConfigurationError, resolve_tenant_entitlements
from packages.telegram.fleet_state import BotFleetStateStore
from packages.telegram.launch import resolve_tenant_public_url
from packages.telegram.models import Bot
from packages.telegram.secrets import SecretNotFoundError, SecretStorage, get_default_secret_storage
from packages.tenants.models import AuditLog, Tenant

router = APIRouter(prefix="/admin/bots", tags=["admin-bots"])
_SECRETISH = ("secret", "password", "token", "api_key", "apikey", "authorization", "credential")


def _redact_config(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            if any(fragment in str(key).lower() for fragment in _SECRETISH):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _redact_config(nested)
        return result
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


class BotResponse(BaseModel):
    id: uuid.UUID
    telegram_bot_id: int
    username: str | None
    display_name: str
    is_enabled: bool
    config: dict[str, Any]
    token_configured: bool
    credential_status: str
    credential_version: int
    credential_verified_at: datetime | None
    credential_rotated_at: datetime | None
    credential_last_error_type: str | None
    runtime_revision: int
    release_channel: str
    template_key: str | None = None
    template_version: int | None = None
    business_profile: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class BotListResponse(BaseModel):
    bots: list[BotResponse]
    total: int


class FleetBotResponse(BotResponse):
    desired_state: str
    runtime_status: str
    runtime_detail: str | None = None
    runtime_observed_at: datetime | None = None


class BotFleetResponse(BaseModel):
    bots: list[FleetBotResponse]
    total: int


class BotCredentialRotateRequest(BaseModel):
    bot_token: SecretStr


class BotCredentialActionResponse(BaseModel):
    bot: BotResponse
    telegram_username: str | None
    action: str


class LaunchReadinessCheck(BaseModel):
    key: str
    label: str
    status: Literal["PASS", "WARN", "BLOCK"]
    detail: str
    action_view: str | None = None


class BotLaunchReadinessResponse(BaseModel):
    bot_id: uuid.UUID
    launchable: bool
    checks: list[LaunchReadinessCheck]


class TemplateGuidanceResponse(BaseModel):
    product_source: str
    what_you_can_sell: str
    delivery_experience: str
    operational_complexity: str
    setup_requirements: list[str] = Field(default_factory=list)
    example_business: str
    limitations: str
    supported_hosting: list[str] = Field(default_factory=list)


class BotTemplateResponse(BaseModel):
    key: str
    version: int
    name: str
    description: str
    recommended_for: str
    default_config: dict[str, Any]
    business_type: str = "GENERAL"
    provider_categories: list[str] = Field(default_factory=list)
    default_routing_strategy: str = "PRIORITY"
    guidance: TemplateGuidanceResponse | None = None

class BotTemplateListResponse(BaseModel):
    templates: list[BotTemplateResponse]


class BotWizardProviderOption(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    category: str
    health_status: str


class BotWizardPaymentMethodOption(BaseModel):
    id: uuid.UUID
    code: str
    display_name: str
    method_type: str
    provider_name: str | None
    flexible_deposits_enabled: bool = False
    auto_credit_enabled: bool = False


class BotWizardPricingTierOption(BaseModel):
    id: uuid.UUID
    code: str
    display_name: str
    is_default: bool


class BotWizardOptionsResponse(BaseModel):
    template_key: str
    business_type: str
    provider_categories: list[str]
    routing_strategies: list[str]
    default_routing_strategy: str
    providers: list[BotWizardProviderOption]
    payment_methods: list[BotWizardPaymentMethodOption]
    pricing_tiers: list[BotWizardPricingTierOption]


class BotProvisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token_secret_ref: str | None = Field(default=None, min_length=2, max_length=255)
    bot_token: SecretStr | None = None
    expected_username: str | None = Field(default=None, max_length=100)
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    is_enabled: bool = True
    template_key: str | None = Field(default=None, max_length=50)
    template_version: int | None = Field(default=None, ge=1, le=1000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    locale: str | None = Field(default=None, min_length=2, max_length=20)
    branding: dict[str, Any] = Field(default_factory=dict)
    enabled_modules: list[str] | None = None
    business_profile: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    max_attempts: int = Field(default=5, ge=1, le=10)

    @field_validator("token_secret_ref")
    @classmethod
    def validate_token_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return validate_secret_ref(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def validate_credential_input(self) -> BotProvisionRequest:
        has_ref = bool(self.token_secret_ref)
        has_token = bool(self.bot_token and self.bot_token.get_secret_value())
        if has_ref == has_token:
            raise ValueError("Provide exactly one of bot_token or token_secret_ref.")
        return self

    @field_validator("expected_username")
    @classmethod
    def normalize_username(cls, value: str | None) -> str | None:
        return normalize_expected_username(value)

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        try:
            reject_secret_material(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        json.dumps(value, sort_keys=True)
        return value


class BotConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    template_key: str = Field(min_length=2, max_length=50)
    template_version: int | None = Field(default=None, ge=1, le=1000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    locale: str | None = Field(default=None, min_length=2, max_length=20)
    branding: dict[str, Any] = Field(default_factory=dict)
    enabled_modules: list[str] | None = None
    business_profile: dict[str, Any] | None = None


def _resolved_request_config(req: BotProvisionRequest | BotConfigurationRequest) -> dict[str, Any]:
    if isinstance(req, BotProvisionRequest) and req.template_key is None:
        legacy = req.config or {}
        try:
            reject_secret_material(legacy)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return legacy
    if isinstance(req, BotProvisionRequest) and req.config is not None:
        raise HTTPException(
            status_code=422,
            detail="Use template fields or legacy config, not both in the same provisioning request.",
        )
    try:
        return build_template_config(
            template_key=req.template_key or "general-commerce",
            template_version=req.template_version,
            currency=req.currency,
            locale=req.locale,
            branding=req.branding,
            enabled_modules=req.enabled_modules,
            business_profile=req.business_profile,
        )
    except (TemplateValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _validate_resolved_business_profile(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    config: dict[str, Any],
) -> dict[str, Any]:
    try:
        profile = parse_business_profile(config.get("_business"))
        profile = await validate_business_profile_for_tenant(
            session, tenant_id=tenant_id, profile=profile
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    config["_business"] = profile.public_payload()
    return config


def _uses_custom_branding(
    req: BotProvisionRequest | BotConfigurationRequest,
    resolved_config: dict[str, Any],
) -> bool:
    if isinstance(req, BotProvisionRequest) and req.template_key is None:
        return bool(isinstance(req.config, dict) and req.config.get("branding"))
    try:
        baseline = build_template_config(
            template_key=req.template_key or "general-commerce",
            template_version=req.template_version,
            currency=req.currency,
            locale=req.locale,
            branding={},
            enabled_modules=req.enabled_modules,
        )
    except TemplateValidationError:
        return True
    return (resolved_config.get("branding") or {}) != (baseline.get("branding") or {})


class BotProvisionJobResponse(BaseModel):
    id: uuid.UUID
    status: BotProvisioningStatus
    bot_id: uuid.UUID | None
    expected_username: str | None
    requested_display_name: str | None
    desired_enabled: bool
    attempt_count: int
    max_attempts: int
    verified_telegram_bot_id: int | None
    verified_username: str | None
    last_error_code: str | None
    last_error_type: str | None
    token_configured: bool = True
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class BotProvisionJobListResponse(BaseModel):
    jobs: list[BotProvisionJobResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


class BotStateRequest(BaseModel):
    is_enabled: bool


class BotReleaseChannelRequest(BaseModel):
    release_channel: Literal["STABLE", "CANARY"]


async def _audit(
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | str,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            details=details or {},
        )
    )


def _secret_storage() -> SecretStorage:
    return get_default_secret_storage()


def _fleet_state_store() -> BotFleetStateStore:
    return BotFleetStateStore()


def _telegram_identity_verifier() -> TelegramIdentityVerifier:
    return AiogramTelegramIdentityVerifier()




async def _tenant_entitlements_or_503(session: AsyncSession, tenant_id: uuid.UUID):
    try:
        return await resolve_tenant_entitlements(session, tenant_id=tenant_id)
    except EntitlementConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant plan entitlements are invalid; contact the platform operator.",
        ) from exc


def _commercial_access_http_error(exc: CommercialAccessDenied) -> HTTPException:
    snapshot = exc.snapshot
    return HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "code": "SAAS_COMMERCIAL_ACCESS_BLOCKED",
            "message": str(exc),
            "subscription_status": snapshot.subscription_status.value if snapshot.subscription_status else None,
            "access_state": snapshot.state.value,
            "grace_ends_at": snapshot.grace_ends_at.isoformat() if snapshot.grace_ends_at else None,
        },
    )


async def _require_commercial_mutation(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    try:
        await require_commercial_access(session, tenant_id=tenant_id)
    except CommercialAccessDenied as exc:
        raise _commercial_access_http_error(exc) from exc


async def _require_product_feature(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    feature: str,
) -> None:
    try:
        await require_feature_entitlement(session, tenant_id=tenant_id, feature=feature)
    except CommercialAccessDenied as exc:
        raise _commercial_access_http_error(exc) from exc
    except FeatureEntitlementDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SAAS_FEATURE_NOT_ENTITLED",
                "message": str(exc),
                "feature": exc.feature,
            },
        ) from exc
    except EntitlementConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant plan entitlements are invalid; contact the platform operator.",
        ) from exc


async def _enforce_factory_capacity(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    desired_enabled: bool,
) -> None:
    await _require_commercial_mutation(session, tenant_id)
    entitlements = await _tenant_entitlements_or_503(session, tenant_id)
    bot_count = int(
        await session.scalar(
            select(func.count()).select_from(Bot).where(
                Bot.tenant_id == tenant_id,
                Bot.deleted_at.is_(None),
            )
        )
        or 0
    )
    if bot_count >= entitlements.max_bots:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tenant bot limit reached ({entitlements.max_bots}).",
        )

    open_jobs = int(
        await session.scalar(
            select(func.count()).select_from(BotProvisioningJob).where(
                BotProvisioningJob.tenant_id == tenant_id,
                BotProvisioningJob.status.in_(
                    [BotProvisioningStatus.PENDING, BotProvisioningStatus.RUNNING, BotProvisioningStatus.RETRY]
                ),
            )
        )
        or 0
    )
    if open_jobs >= entitlements.max_open_provisioning_jobs:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many open provisioning jobs ({entitlements.max_open_provisioning_jobs}).",
        )

    if desired_enabled:
        enabled_count = int(
            await session.scalar(
                select(func.count()).select_from(Bot).where(
                    Bot.tenant_id == tenant_id,
                    Bot.deleted_at.is_(None),
                    Bot.is_enabled.is_(True),
                )
            )
            or 0
        )
        if enabled_count >= entitlements.max_enabled_bots:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tenant enabled-bot limit reached ({entitlements.max_enabled_bots}).",
            )


def _bot_response(bot: Bot) -> BotResponse:
    template_key, template_version = template_metadata(bot.config)
    return BotResponse(
        id=bot.id,
        telegram_bot_id=bot.telegram_bot_id,
        username=bot.username,
        display_name=bot.display_name,
        is_enabled=bot.is_enabled,
        config=_redact_config(bot.config or {}),
        token_configured=bool(bot.token_secret_ref),
        credential_status=bot.credential_status,
        credential_version=bot.credential_version,
        credential_verified_at=bot.credential_verified_at,
        credential_rotated_at=bot.credential_rotated_at,
        credential_last_error_type=bot.credential_last_error_type,
        runtime_revision=bot.runtime_revision,
        release_channel=bot.release_channel,
        template_key=template_key,
        template_version=template_version,
        business_profile=business_profile_from_config(bot.config).public_payload(),
        created_at=bot.created_at,
        updated_at=bot.updated_at,
    )


@router.get("/templates", response_model=BotTemplateListResponse)
async def get_templates(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
) -> BotTemplateListResponse:
    del principal
    return BotTemplateListResponse(
        templates=[BotTemplateResponse(**template.public_payload()) for template in list_bot_templates()]
    )


@router.get("/wizard/options", response_model=BotWizardOptionsResponse)
async def get_bot_wizard_options(
    template_key: str = Query("reseller-hub", min_length=2, max_length=50),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> BotWizardOptionsResponse:
    try:
        template = next(item for item in list_bot_templates() if item.key == template_key)
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="Bot template not found.") from exc
    categories = set(template.provider_categories) or allowed_provider_categories(template.business_type)
    providers = list(
        (
            await session.execute(
                select(Provider).where(
                    Provider.tenant_id == principal.tenant_id,
                    Provider.is_enabled.is_(True),
                    Provider.category.in_(categories),
                ).order_by(Provider.category.asc(), Provider.priority.asc(), Provider.name.asc())
            )
        ).scalars().all()
    )
    methods = list(
        (
            await session.execute(
                select(PaymentMethodConfig).where(
                    PaymentMethodConfig.tenant_id == principal.tenant_id,
                    PaymentMethodConfig.is_enabled.is_(True),
                ).order_by(PaymentMethodConfig.display_name.asc())
            )
        ).scalars().all()
    )
    tiers = list(
        (
            await session.execute(
                select(PricingTier).where(
                    PricingTier.tenant_id == principal.tenant_id,
                    PricingTier.is_active.is_(True),
                ).order_by(PricingTier.priority.asc(), PricingTier.display_name.asc())
            )
        ).scalars().all()
    )
    return BotWizardOptionsResponse(
        template_key=template.key,
        business_type=template.business_type.value,
        provider_categories=[item.value for item in template.provider_categories],
        routing_strategies=[item.value for item in ProviderRoutingStrategy],
        default_routing_strategy=template.default_routing_strategy.value,
        providers=[
            BotWizardProviderOption(
                id=row.id,
                name=row.name,
                slug=row.slug,
                category=row.category.value,
                health_status=row.health_status.value,
            )
            for row in providers
        ],
        payment_methods=[
            BotWizardPaymentMethodOption(
                id=row.id,
                code=row.code,
                display_name=row.display_name,
                method_type=row.method_type.value,
                provider_name=row.provider_name,
                flexible_deposits_enabled=bool((row.settings_json or {}).get("flexible_deposits_enabled", False)),
                auto_credit_enabled=bool(row.auto_credit_enabled),
            )
            for row in methods
        ],
        pricing_tiers=[
            BotWizardPricingTierOption(
                id=row.id, code=row.code, display_name=row.display_name, is_default=row.is_default
            )
            for row in tiers
        ],
    )


@router.get("", response_model=BotListResponse)
async def list_bots(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> BotListResponse:
    rows = list(
        (
            await session.execute(
                select(Bot)
                .where(Bot.tenant_id == principal.tenant_id, Bot.deleted_at.is_(None))
                .order_by(Bot.created_at.desc())
            )
        ).scalars().all()
    )
    return BotListResponse(bots=[_bot_response(row) for row in rows], total=len(rows))


@router.get("/fleet", response_model=BotFleetResponse)
async def get_bot_fleet(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
    fleet_store: BotFleetStateStore = Depends(_fleet_state_store),
) -> BotFleetResponse:
    rows = list(
        (
            await session.execute(
                select(Bot)
                .where(Bot.tenant_id == principal.tenant_id, Bot.deleted_at.is_(None))
                .order_by(Bot.created_at.desc())
            )
        ).scalars().all()
    )
    observed = await fleet_store.read_many([row.id for row in rows])
    bots: list[FleetBotResponse] = []
    for row in rows:
        base = _bot_response(row).model_dump()
        state = observed.get(row.id, {})
        raw_observed_at = state.get("observed_at")
        try:
            observed_at = datetime.fromisoformat(raw_observed_at) if raw_observed_at else None
        except (TypeError, ValueError):
            observed_at = None
        runtime_status = str(state.get("status") or ("OFFLINE" if row.is_enabled else "DISABLED"))
        bots.append(
            FleetBotResponse(
                **base,
                desired_state="ENABLED" if row.is_enabled else "DISABLED",
                runtime_status=runtime_status,
                runtime_detail=state.get("detail"),
                runtime_observed_at=observed_at,
            )
        )
    return BotFleetResponse(bots=bots, total=len(bots))


@router.get("/{bot_id}/launch-readiness", response_model=BotLaunchReadinessResponse)
async def get_bot_launch_readiness(
    bot_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
    fleet_store: BotFleetStateStore = Depends(_fleet_state_store),
) -> BotLaunchReadinessResponse:
    bot = await session.get(Bot, bot_id)
    if bot is None or bot.tenant_id != principal.tenant_id or bot.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found.")
    tenant = await session.get(Tenant, principal.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found.")

    observed = (await fleet_store.read_many([bot.id])).get(bot.id, {})
    runtime_status = str(observed.get("status") or ("OFFLINE" if bot.is_enabled else "DISABLED"))
    miniapp_url = resolve_tenant_public_url(tenant.settings, kind="miniapp", fallback=settings.miniapp_public_url)
    admin_url = resolve_tenant_public_url(tenant.settings, kind="admin", fallback=settings.admin_public_url)
    commercial_access = await resolve_commercial_access(session, tenant_id=principal.tenant_id)
    business_profile = business_profile_from_config(bot.config)
    compatible_categories = set(allowed_provider_categories(business_profile.business_type))

    active_products = int(
        await session.scalar(
            select(func.count()).select_from(Product).where(
                Product.tenant_id == principal.tenant_id,
                Product.deleted_at.is_(None),
                Product.is_active.is_(True),
            )
        )
        or 0
    )
    active_variants = int(
        await session.scalar(
            select(func.count())
            .select_from(ProductVariant)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(
                Product.tenant_id == principal.tenant_id,
                Product.deleted_at.is_(None),
                Product.is_active.is_(True),
                ProductVariant.deleted_at.is_(None),
                ProductVariant.is_active.is_(True),
            )
        )
        or 0
    )
    provider_filters = [
        Provider.tenant_id == principal.tenant_id,
        Provider.is_enabled.is_(True),
        Provider.category.in_(compatible_categories),
    ]
    if business_profile.provider_ids:
        provider_filters.append(Provider.id.in_(business_profile.provider_ids))
    enabled_providers = int(
        await session.scalar(select(func.count()).select_from(Provider).where(*provider_filters)) or 0
    )
    enabled_mappings = int(
        await session.scalar(
            select(func.count()).select_from(ProviderProductMapping).where(
                ProviderProductMapping.tenant_id == principal.tenant_id,
                ProviderProductMapping.is_enabled.is_(True),
            )
        )
        or 0
    )
    payment_filters = [
        PaymentMethodConfig.tenant_id == principal.tenant_id,
        PaymentMethodConfig.is_enabled.is_(True),
    ]
    if business_profile.payment_method_ids:
        payment_filters.append(PaymentMethodConfig.id.in_(business_profile.payment_method_ids))
    payment_configs = int(
        await session.scalar(
            select(func.count()).select_from(PaymentMethodConfig).where(*payment_filters)
        )
        or 0
    )

    checks = [
        LaunchReadinessCheck(
            key="commercial_access", label="Commercial access",
            status="PASS" if commercial_access.allowed else "BLOCK",
            detail=commercial_access.reason, action_view="plan",
        ),
        LaunchReadinessCheck(
            key="credential", label="Telegram credential",
            status="PASS" if bot.credential_status == "VERIFIED" else "BLOCK",
            detail=f"Credential status: {bot.credential_status}.", action_view="bots",
        ),
        LaunchReadinessCheck(
            key="runtime", label="Bot runtime",
            status="PASS" if runtime_status == "RUNNING" else "BLOCK",
            detail=f"Observed runtime state: {runtime_status}.", action_view="bots",
        ),
        LaunchReadinessCheck(
            key="miniapp_url", label="Public Mini App HTTPS",
            status="PASS" if miniapp_url and miniapp_url.startswith("https://") else "BLOCK",
            detail=miniapp_url or "No public Mini App URL configured.", action_view="bots",
        ),
        LaunchReadinessCheck(
            key="admin_url", label="Admin HTTPS",
            status="PASS" if admin_url and admin_url.startswith("https://") else "WARN",
            detail=admin_url or "No public Admin URL configured.", action_view="bots",
        ),
        LaunchReadinessCheck(
            key="catalog", label="Sellable catalog",
            status="PASS" if active_products > 0 and active_variants > 0 else "BLOCK",
            detail=f"{active_products} active product(s), {active_variants} active variant(s).", action_view="products",
        ),
        LaunchReadinessCheck(
            key="business_profile", label="Business profile",
            status="PASS",
            detail=(
                f"{business_profile.business_type.value} · {(business_profile.routing_strategy.value if business_profile.routing_strategy else 'INHERIT')} routing · "
                f"{len(business_profile.provider_ids) if business_profile.provider_ids else 'all compatible'} provider selection · "
                f"{len(business_profile.payment_method_ids) if business_profile.payment_method_ids else 'all enabled'} payment selection."
            ),
            action_view="bots",
        ),
        LaunchReadinessCheck(
            key="fulfillment", label="Fulfillment routing",
            status="PASS" if enabled_providers > 0 and enabled_mappings > 0 else "BLOCK",
            detail=f"{enabled_providers} enabled provider(s), {enabled_mappings} enabled mapping(s).", action_view="providers",
        ),
        LaunchReadinessCheck(
            key="payments", label="Customer funding",
            status="PASS" if payment_configs > 0 else "WARN",
            detail=(f"{payment_configs} enabled payment method(s) allowed for this bot." if payment_configs else "No enabled payment provider; wallet checkout can only use pre-funded balances."),
            action_view="providers",
        ),
    ]
    return BotLaunchReadinessResponse(
        bot_id=bot.id,
        launchable=not any(check.status == "BLOCK" for check in checks),
        checks=checks,
    )


@router.post("/provision", response_model=BotProvisionJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def request_bot_provisioning(
    req: BotProvisionRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=100),
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
    secret_storage: SecretStorage = Depends(_secret_storage),
) -> BotProvisionJobResponse:
    resolved_config = await _validate_resolved_business_profile(
        session, tenant_id=principal.tenant_id, config=_resolved_request_config(req)
    )
    direct_token = req.bot_token.get_secret_value() if req.bot_token is not None else None
    if direct_token:
        ref_digest = hashlib.sha256(f"{principal.tenant_id}:{idempotency_key}".encode()).hexdigest()[:32].upper()
        token_secret_ref = f"GHBF_VAULT_BOT_{ref_digest}"
        credential_fingerprint = hashlib.sha256(direct_token.encode("utf-8")).hexdigest()
    else:
        token_secret_ref = req.token_secret_ref or ""
        credential_fingerprint = None

    fingerprint = provisioning_fingerprint(
        token_secret_ref=token_secret_ref,
        expected_username=req.expected_username,
        requested_display_name=req.display_name,
        desired_enabled=req.is_enabled,
        desired_config=resolved_config,
        credential_fingerprint=credential_fingerprint,
    )
    existing = (
        await session.execute(
            select(BotProvisioningJob).where(
                BotProvisioningJob.tenant_id == principal.tenant_id,
                BotProvisioningJob.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key was already used for a different provisioning request.",
            )
        if direct_token:
            await secret_storage.set_secret(token_secret_ref, direct_token)
        return BotProvisionJobResponse.model_validate(existing)

    if _uses_custom_branding(req, resolved_config):
        await _require_product_feature(session, principal.tenant_id, FEATURE_CUSTOM_BRANDING)

    await _enforce_factory_capacity(
        session,
        tenant_id=principal.tenant_id,
        desired_enabled=req.is_enabled,
    )

    job = BotProvisioningJob(
        tenant_id=principal.tenant_id,
        requested_by_user_id=principal.user_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        token_secret_ref=token_secret_ref,
        expected_username=req.expected_username,
        requested_display_name=req.display_name.strip() if req.display_name else None,
        desired_enabled=req.is_enabled,
        desired_config=resolved_config,
        max_attempts=req.max_attempts,
        status=BotProvisioningStatus.PENDING,
    )
    session.add(job)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raced = (
            await session.execute(
                select(BotProvisioningJob).where(
                    BotProvisioningJob.tenant_id == principal.tenant_id,
                    BotProvisioningJob.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if raced is None or raced.request_fingerprint != fingerprint:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Provisioning request conflict.")
        if direct_token:
            await secret_storage.set_secret(token_secret_ref, direct_token)
        return BotProvisionJobResponse.model_validate(raced)

    if direct_token:
        await secret_storage.set_secret(token_secret_ref, direct_token)

    await _audit(
        session,
        principal,
        action="BOT_PROVISIONING_REQUESTED",
        resource_type="BOT_PROVISIONING_JOB",
        resource_id=job.id,
        details={
            "expected_username": req.expected_username,
            "desired_enabled": req.is_enabled,
            "request_fingerprint": fingerprint,
            "template": template_metadata(resolved_config)[0],
            "template_version": template_metadata(resolved_config)[1],
        },
    )
    return BotProvisionJobResponse.model_validate(job)


@router.get("/jobs", response_model=BotProvisionJobListResponse)
async def list_provisioning_jobs(
    job_status: BotProvisioningStatus | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> BotProvisionJobListResponse:
    conditions = [BotProvisioningJob.tenant_id == principal.tenant_id]
    if job_status is not None:
        conditions.append(BotProvisioningJob.status == job_status)
    total = int(await session.scalar(select(func.count()).select_from(BotProvisioningJob).where(*conditions)) or 0)
    rows = list(
        (
            await session.execute(
                select(BotProvisioningJob)
                .where(*conditions)
                .order_by(BotProvisioningJob.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
    )
    next_offset = offset + len(rows) if offset + len(rows) < total else None
    return BotProvisionJobListResponse(
        jobs=[BotProvisionJobResponse.model_validate(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.post("/jobs/{job_id}/retry", response_model=BotProvisionJobResponse)
async def retry_provisioning_job(
    job_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> BotProvisionJobResponse:
    job = await session.get(BotProvisioningJob, job_id, with_for_update=True)
    if job is None or job.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provisioning job not found.")
    if job.status != BotProvisioningStatus.FAILED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only FAILED jobs can be retried manually.")
    await _require_commercial_mutation(session, principal.tenant_id)
    job.status = BotProvisioningStatus.PENDING
    job.attempt_count = 0
    job.next_attempt_at = None
    job.locked_at = None
    job.lease_expires_at = None
    job.completed_at = None
    job.last_error_code = None
    job.last_error_type = None
    await _audit(
        session,
        principal,
        action="BOT_PROVISIONING_RETRIED",
        resource_type="BOT_PROVISIONING_JOB",
        resource_id=job.id,
    )
    await session.flush()
    return BotProvisionJobResponse.model_validate(job)


@router.post("/jobs/{job_id}/cancel", response_model=BotProvisionJobResponse)
async def cancel_provisioning_job(
    job_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> BotProvisionJobResponse:
    job = await session.get(BotProvisioningJob, job_id, with_for_update=True)
    if job is None or job.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provisioning job not found.")
    if job.status not in {BotProvisioningStatus.PENDING, BotProvisioningStatus.RETRY}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only PENDING or RETRY jobs can be cancelled safely.",
        )
    job.status = BotProvisioningStatus.CANCELLED
    job.completed_at = datetime.now(UTC)
    job.next_attempt_at = None
    await _audit(
        session,
        principal,
        action="BOT_PROVISIONING_CANCELLED",
        resource_type="BOT_PROVISIONING_JOB",
        resource_id=job.id,
    )
    await session.flush()
    return BotProvisionJobResponse.model_validate(job)


@router.post("/{bot_id}/credentials/verify", response_model=BotCredentialActionResponse)
async def verify_bot_credential(
    bot_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
    secret_storage: SecretStorage = Depends(_secret_storage),
    verifier: TelegramIdentityVerifier = Depends(_telegram_identity_verifier),
) -> BotCredentialActionResponse:
    bot = await session.get(Bot, bot_id, with_for_update=True)
    if bot is None or bot.tenant_id != principal.tenant_id or bot.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found.")
    try:
        token = await secret_storage.get_secret(bot.token_secret_ref)
        identity = await verifier.verify(token)
        if identity.telegram_bot_id != bot.telegram_bot_id:
            bot.credential_status = "INVALID"
            bot.credential_last_error_type = "TelegramIdentityMismatch"
            await _audit(
                session, principal, action="BOT_CREDENTIAL_VERIFY_FAILED", resource_type="BOT", resource_id=bot.id,
                details={"error_type": "TelegramIdentityMismatch"},
            )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Stored credential resolves to a different Telegram bot identity.",
            )
    except HTTPException:
        raise
    except SecretNotFoundError as exc:
        bot.credential_status = "MISSING"
        bot.credential_last_error_type = type(exc).__name__
        await _audit(
            session, principal, action="BOT_CREDENTIAL_VERIFY_FAILED", resource_type="BOT", resource_id=bot.id,
            details={"error_type": type(exc).__name__},
        )
        await session.commit()
        raise HTTPException(status_code=409, detail="Bot credential is missing from secret storage.") from exc
    except ProvisioningError as exc:
        bot.credential_status = "DEGRADED" if exc.retryable else "INVALID"
        bot.credential_last_error_type = exc.code
        await _audit(
            session, principal, action="BOT_CREDENTIAL_VERIFY_FAILED", resource_type="BOT", resource_id=bot.id,
            details={"error_type": exc.code, "retryable": exc.retryable},
        )
        await session.commit()
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE if exc.retryable else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=http_status, detail="Bot credential verification failed.") from exc
    except Exception as exc:
        bot.credential_status = "DEGRADED"
        bot.credential_last_error_type = type(exc).__name__
        await _audit(
            session, principal, action="BOT_CREDENTIAL_VERIFY_FAILED", resource_type="BOT", resource_id=bot.id,
            details={"error_type": type(exc).__name__},
        )
        await session.commit()
        raise HTTPException(status_code=503, detail="Bot credential verification temporarily failed.") from exc

    bot.credential_status = "VERIFIED"
    bot.credential_verified_at = datetime.now(UTC)
    bot.credential_last_error_type = None
    await _audit(
        session, principal, action="BOT_CREDENTIAL_VERIFIED", resource_type="BOT", resource_id=bot.id,
        details={"telegram_bot_id": bot.telegram_bot_id, "username": identity.username},
    )
    await session.flush()
    return BotCredentialActionResponse(bot=_bot_response(bot), telegram_username=identity.username, action="VERIFIED")


@router.post("/{bot_id}/credentials/rotate", response_model=BotCredentialActionResponse)
async def rotate_bot_credential(
    bot_id: uuid.UUID,
    req: BotCredentialRotateRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
    secret_storage: SecretStorage = Depends(_secret_storage),
    verifier: TelegramIdentityVerifier = Depends(_telegram_identity_verifier),
) -> BotCredentialActionResponse:
    bot = await session.get(Bot, bot_id, with_for_update=True)
    if bot is None or bot.tenant_id != principal.tenant_id or bot.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found.")
    new_token = req.bot_token.get_secret_value().strip()
    try:
        identity = await verifier.verify(new_token)
    except ProvisioningError as exc:
        raise HTTPException(status_code=422, detail=f"Telegram rejected the new credential ({exc.code}).") from exc
    if identity.telegram_bot_id != bot.telegram_bot_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="New credential belongs to a different Telegram bot. Rotation was not applied.",
        )

    await secret_storage.set_secret(bot.token_secret_ref, new_token)
    bot.username = identity.username
    bot.credential_version += 1
    bot.credential_status = "VERIFIED"
    bot.credential_verified_at = datetime.now(UTC)
    bot.credential_rotated_at = datetime.now(UTC)
    bot.credential_last_error_type = None
    await _audit(
        session, principal, action="BOT_CREDENTIAL_ROTATED", resource_type="BOT", resource_id=bot.id,
        details={"credential_version": bot.credential_version, "telegram_bot_id": bot.telegram_bot_id},
    )
    await session.flush()
    return BotCredentialActionResponse(bot=_bot_response(bot), telegram_username=identity.username, action="ROTATED")


@router.post("/{bot_id}/runtime/restart", response_model=BotResponse)
async def restart_bot_runtime(
    bot_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> BotResponse:
    bot = await session.get(Bot, bot_id, with_for_update=True)
    if bot is None or bot.tenant_id != principal.tenant_id or bot.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found.")
    await _require_product_feature(session, principal.tenant_id, FEATURE_RUNTIME_CONTROLS)
    if not bot.is_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Enable the bot before requesting a runtime restart.")
    bot.runtime_revision += 1
    await _audit(
        session, principal, action="BOT_RUNTIME_RESTART_REQUESTED", resource_type="BOT", resource_id=bot.id,
        details={"runtime_revision": bot.runtime_revision},
    )
    await session.flush()
    return _bot_response(bot)


@router.patch("/{bot_id}/configuration", response_model=BotResponse)
async def update_bot_configuration(
    bot_id: uuid.UUID,
    req: BotConfigurationRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> BotResponse:
    bot = await session.get(Bot, bot_id, with_for_update=True)
    if bot is None or bot.tenant_id != principal.tenant_id or bot.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found.")
    resolved_config = await _validate_resolved_business_profile(
        session, tenant_id=principal.tenant_id, config=_resolved_request_config(req)
    )
    previous_branding = (bot.config or {}).get("branding") or {}
    next_branding = resolved_config.get("branding") or {}
    if _uses_custom_branding(req, resolved_config) and next_branding != previous_branding:
        await _require_product_feature(session, principal.tenant_id, FEATURE_CUSTOM_BRANDING)
    previous_template = template_metadata(bot.config)
    next_template = template_metadata(resolved_config)
    bot.config = resolved_config
    if req.display_name is not None:
        bot.display_name = req.display_name.strip()
    await _audit(
        session,
        principal,
        action="BOT_CONFIGURATION_UPDATED",
        resource_type="BOT",
        resource_id=bot.id,
        details={
            "previous_template": previous_template[0],
            "previous_template_version": previous_template[1],
            "template": next_template[0],
            "template_version": next_template[1],
            "branding_keys": sorted(req.branding),
            "enabled_modules": resolved_config.get("enabled_modules", []),
        },
    )
    await session.flush()
    return _bot_response(bot)


@router.patch("/{bot_id}/release-channel", response_model=BotResponse)
async def set_bot_release_channel(
    bot_id: uuid.UUID,
    req: BotReleaseChannelRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> BotResponse:
    bot = await session.get(Bot, bot_id, with_for_update=True)
    if bot is None or bot.tenant_id != principal.tenant_id or bot.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found.")
    previous = bot.release_channel
    if previous != req.release_channel:
        if req.release_channel == "CANARY":
            await _require_product_feature(session, principal.tenant_id, FEATURE_CANARY_ROLLOUT)
        bot.release_channel = req.release_channel
        bot.runtime_revision += 1
        await _audit(
            session, principal, action="BOT_RELEASE_CHANNEL_CHANGED", resource_type="BOT", resource_id=bot.id,
            details={"previous": previous, "next": req.release_channel, "runtime_revision": bot.runtime_revision},
        )
        await session.flush()
    return _bot_response(bot)


@router.patch("/{bot_id}/state", response_model=BotResponse)
async def set_bot_state(
    bot_id: uuid.UUID,
    req: BotStateRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> BotResponse:
    bot = await session.get(Bot, bot_id, with_for_update=True)
    if bot is None or bot.tenant_id != principal.tenant_id or bot.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found.")
    if req.is_enabled and not bot.is_enabled:
        await _require_commercial_mutation(session, principal.tenant_id)
        enabled_count = int(
            await session.scalar(
                select(func.count()).select_from(Bot).where(
                    Bot.tenant_id == principal.tenant_id,
                    Bot.deleted_at.is_(None),
                    Bot.is_enabled.is_(True),
                    Bot.id != bot.id,
                )
            )
            or 0
        )
        entitlements = await _tenant_entitlements_or_503(session, principal.tenant_id)
        if enabled_count >= entitlements.max_enabled_bots:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tenant enabled-bot limit reached ({entitlements.max_enabled_bots}).",
            )
    bot.is_enabled = req.is_enabled
    await _audit(
        session,
        principal,
        action="BOT_ENABLED" if req.is_enabled else "BOT_DISABLED",
        resource_type="BOT",
        resource_id=bot.id,
        details={"telegram_bot_id": bot.telegram_bot_id, "username": bot.username},
    )
    await session.flush()
    return _bot_response(bot)
