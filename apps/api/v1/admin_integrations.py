from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_admin_or_owner, require_staff_or_above
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.marketplace.integrations_service import IntegrationMarketplaceService
from packages.marketplace.models import TenantIntegrationEntitlement
from packages.payments.models import PaymentMethodConfig, PaymentMethodType, PaymentVerificationMode
from packages.providers.models import Provider, ProviderCategory
from packages.providers.service import upsert_provider_credential_value
from packages.tenants.models import AuditLog

router = APIRouter(prefix="/admin/integrations", tags=["admin-integrations"])


class IntegrationMarketplaceItem(BaseModel):
    key: str
    name: str
    category: str
    adapter_key: str
    description: str
    lifecycle: str
    setup_fee: str
    monthly_fee: str
    currency: str
    required_credentials: list[str]
    supported_templates: list[str]
    features: list[str]
    requirements: list[str]
    docs_url: str | None
    is_entitled: bool
    granted_by: str | None
    is_configured: bool
    status: str  # "CONFIGURED", "ENTITLED", "LOCKED"


class ConfigureIntegrationRequest(BaseModel):
    api_key: str | None = Field(default=None, description="Write-only credential token or key")
    display_name: str | None = Field(default=None, max_length=100)
    extra_settings: dict[str, Any] = Field(default_factory=dict)


class ConfigureIntegrationResponse(BaseModel):
    integration_key: str
    status: str
    message: str


@router.get("", response_model=list[IntegrationMarketplaceItem])
async def list_admin_integrations(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[IntegrationMarketplaceItem]:
    items = await IntegrationMarketplaceService.list_offerings_for_tenant(
        session, tenant_id=principal.tenant_id
    )
    return [IntegrationMarketplaceItem(**it) for it in items]


@router.post("/{integration_key}/configure", response_model=ConfigureIntegrationResponse)
async def configure_admin_integration(
    integration_key: str,
    payload: ConfigureIntegrationRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> ConfigureIntegrationResponse:
    norm_key = integration_key.strip().lower()
    tenant_id = principal.tenant_id

    # 1. Verify tenant holds active entitlement
    ent = (
        await session.execute(
            select(TenantIntegrationEntitlement).where(
                TenantIntegrationEntitlement.tenant_id == tenant_id,
                TenantIntegrationEntitlement.integration_key == norm_key,
                TenantIntegrationEntitlement.is_enabled.is_(True),
            )
        )
    ).scalar_one_or_none()

    if not ent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant is not entitled to configure integration '{norm_key}'. Contact the platform operator to unlock.",
        )

    # 2. Configure provider or payment method
    if norm_key in {"numbers-sms", "gift-cards-api", "accounts-api", "custom-http-api", "ventebot", "spider-service"}:
        cat_map = {
            "numbers-sms": ProviderCategory.NUMBER,
            "spider-service": ProviderCategory.NUMBER,
            "gift-cards-api": ProviderCategory.GIFT,
            "accounts-api": ProviderCategory.ACCOUNT,
            "ventebot": ProviderCategory.ACCOUNT,
            "custom-http-api": ProviderCategory.DIGITAL_PRODUCT,
        }
        type_map = {
            "ventebot": "VENTEBOT",
            "spider-service": "SPIDER_SERVICE",
            "custom-http-api": "HTTP_OPENAPI",
            "numbers-sms": "mock",
            "gift-cards-api": "mock",
            "accounts-api": "mock",
        }
        category = cat_map[norm_key]
        provider_type = type_map.get(norm_key, "mock")
        provider_name = (payload.display_name or norm_key.replace("-", " ").title()).strip()

        # Find or create Provider
        provider = (
            await session.execute(
                select(Provider).where(
                    Provider.tenant_id == tenant_id,
                    Provider.category == category,
                    Provider.provider_type == provider_type,
                )
            )
        ).scalars().first()

        if not provider:
            provider = Provider(
                tenant_id=tenant_id,
                name=provider_name,
                slug=f"{norm_key}-{uuid.uuid4().hex[:6]}",
                category=category,
                provider_type=provider_type,
                priority=100,
                is_enabled=True,
                metadata_json=payload.extra_settings,
            )
            session.add(provider)
            await session.flush()
        else:
            provider.name = provider_name
            provider.is_enabled = True
            if payload.extra_settings:
                provider.metadata_json = {**provider.metadata_json, **payload.extra_settings}
        # Store secret in SecretStorage
        if payload.api_key:
            await upsert_provider_credential_value(
                session,
                provider=provider,
                credential_type="API_KEY",
                secret_value=payload.api_key.strip(),
            )

    elif norm_key in {"crypto-payments", "binance-pay"}:
        code = "BINANCE_PAY" if norm_key == "binance-pay" else "USDT_TRON"
        display_name = (payload.display_name or ("Binance Pay" if norm_key == "binance-pay" else "TRON USDT")).strip()

        method = (
            await session.execute(
                select(PaymentMethodConfig).where(
                    PaymentMethodConfig.tenant_id == tenant_id,
                    PaymentMethodConfig.code == code,
                )
            )
        ).scalar_one_or_none()

        if not method:
            method = PaymentMethodConfig(
                tenant_id=tenant_id,
                code=code,
                display_name=display_name,
                method_type=PaymentMethodType.CRYPTO_GATEWAY,
                verification_mode=PaymentVerificationMode.MANUAL,
                is_enabled=True,
                settings_json=payload.extra_settings,
            )
            session.add(method)
            await session.flush()
        else:
            method.display_name = display_name
            method.is_enabled = True
            if payload.extra_settings:
                method.settings_json = {**method.settings_json, **payload.extra_settings}

    # Audit log
    audit = AuditLog(
        tenant_id=tenant_id,
        user_id=principal.user_id,
        action="tenant.integration_configured",
        resource_type="integration",
        resource_id=norm_key,
        details={"integration_key": norm_key},
    )
    session.add(audit)
    await session.commit()

    return ConfigureIntegrationResponse(
        integration_key=norm_key,
        status="CONFIGURED",
        message=f"Integration '{norm_key}' configured successfully.",
    )
