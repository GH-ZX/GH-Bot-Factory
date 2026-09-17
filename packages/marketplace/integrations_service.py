from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.marketplace.integrations import list_integration_offerings
from packages.marketplace.models import (
    IntegrationLifecycle,
    IntegrationOfferingModel,
    TenantIntegrationEntitlement,
)
from packages.payments.models import PaymentMethodConfig
from packages.providers.models import Provider
from packages.saas.control_plane import append_platform_audit
from packages.tenants.models import Tenant


class IntegrationMarketplaceError(ValueError):
    """Raised when integration operations violate safety or business invariants."""


class IntegrationMarketplaceService:
    @classmethod
    async def ensure_baseline_offerings(cls, session: AsyncSession) -> None:
        """Seed baseline integration catalog if table is empty."""
        count = (await session.execute(select(IntegrationOfferingModel).limit(1))).scalars().first()
        if count is not None:
            return

        for offering in list_integration_offerings():
            model = IntegrationOfferingModel(
                key=offering.key,
                name=offering.name,
                category=offering.category.upper(),
                adapter_key=offering.key,
                description=offering.description,
                lifecycle=IntegrationLifecycle.ACTIVE,
                setup_fee=offering.setup_fee,
                monthly_fee=offering.monthly_fee,
                currency=offering.currency,
                required_credentials=list(offering.requirements),
                supported_templates=list(offering.supported_templates),
                features=list(offering.features),
                requirements=list(offering.requirements),
            )
            session.add(model)
        await session.flush()

    @classmethod
    async def list_offerings_for_tenant(
        cls,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        await cls.ensure_baseline_offerings(session)

        stmt = select(IntegrationOfferingModel).where(
            IntegrationOfferingModel.lifecycle.in_(
                [IntegrationLifecycle.ACTIVE, IntegrationLifecycle.SANDBOX_REVIEW, IntegrationLifecycle.DEPRECATED]
            )
        ).order_by(IntegrationOfferingModel.name.asc())
        offerings = (await session.execute(stmt)).scalars().all()

        entitled_keys: dict[str, str] = {}
        configured_keys: set[str] = set()

        if tenant_id:
            # 1. Fetch entitlements
            ent_stmt = select(TenantIntegrationEntitlement).where(
                TenantIntegrationEntitlement.tenant_id == tenant_id,
                TenantIntegrationEntitlement.is_enabled.is_(True),
            )
            entitlements = (await session.execute(ent_stmt)).scalars().all()
            for e in entitlements:
                entitled_keys[e.integration_key] = e.granted_by

            # 2. Check configured providers
            prov_stmt = select(Provider).where(
                Provider.tenant_id == tenant_id,
                Provider.is_enabled.is_(True),
            )
            providers = (await session.execute(prov_stmt)).scalars().all()
            for p in providers:
                # Map provider adapter or category to key
                if p.category.value == "NUMBER":
                    configured_keys.add("numbers-sms")
                elif p.category.value == "GIFT":
                    configured_keys.add("gift-cards-api")
                elif p.category.value == "ACCOUNT":
                    configured_keys.add("accounts-api")
                elif p.adapter_type == "HTTP_GENERIC":
                    configured_keys.add("custom-http-api")

            # 3. Check configured payment methods
            pay_stmt = select(PaymentMethodConfig).where(
                PaymentMethodConfig.tenant_id == tenant_id,
                PaymentMethodConfig.is_enabled.is_(True),
            )
            methods = (await session.execute(pay_stmt)).scalars().all()
            for m in methods:
                if "BINANCE" in m.code:
                    configured_keys.add("binance-pay")
                elif "USDT" in m.code or "CRYPTO" in m.code:
                    configured_keys.add("crypto-payments")

        results = []
        for o in offerings:
            is_entitled = o.key in entitled_keys
            is_configured = o.key in configured_keys

            if is_configured:
                status_label = "CONFIGURED"
            elif is_entitled:
                status_label = "ENTITLED"
            else:
                status_label = "LOCKED"

            results.append({
                "key": o.key,
                "name": o.name,
                "category": o.category,
                "adapter_key": o.adapter_key,
                "description": o.description,
                "lifecycle": o.lifecycle.value,
                "setup_fee": str(o.setup_fee),
                "monthly_fee": str(o.monthly_fee),
                "currency": o.currency,
                "required_credentials": o.required_credentials,
                "supported_templates": o.supported_templates,
                "features": o.features,
                "requirements": o.requirements,
                "docs_url": o.docs_url,
                "is_entitled": is_entitled,
                "granted_by": entitled_keys.get(o.key),
                "is_configured": is_configured,
                "status": status_label,
            })

        return results

    @classmethod
    async def grant_entitlement(
        cls,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        integration_key: str,
        granted_by: str = "OPERATOR",
        actor: str = "LOCAL_PLATFORM_TOKEN",
        ip_address: str | None = None,
    ) -> TenantIntegrationEntitlement:
        tenant = await session.get(Tenant, tenant_id)
        if not tenant or not tenant.is_active:
            raise IntegrationMarketplaceError(f"Tenant {tenant_id} not found or inactive.")

        norm_key = integration_key.strip().lower()
        stmt = select(TenantIntegrationEntitlement).where(
            TenantIntegrationEntitlement.tenant_id == tenant_id,
            TenantIntegrationEntitlement.integration_key == norm_key,
        )
        ent = (await session.execute(stmt)).scalar_one_or_none()

        if ent is None:
            ent = TenantIntegrationEntitlement(
                tenant_id=tenant_id,
                integration_key=norm_key,
                is_enabled=True,
                granted_by=granted_by,
                granted_at=datetime.now(UTC),
            )
            session.add(ent)
        else:
            ent.is_enabled = True
            ent.granted_by = granted_by
            ent.granted_at = datetime.now(UTC)

        await append_platform_audit(
            session,
            action="integration_entitlement.granted",
            resource_type="tenant_integration_entitlement",
            resource_id=f"{tenant_id}:{norm_key}",
            tenant_id=tenant_id,
            details={"integration_key": norm_key, "granted_by": granted_by},
            ip_address=ip_address,
            actor=actor,
        )

        await session.commit()
        await session.refresh(ent)
        return ent

    @classmethod
    async def revoke_entitlement(
        cls,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        integration_key: str,
        actor: str = "LOCAL_PLATFORM_TOKEN",
        ip_address: str | None = None,
    ) -> None:
        norm_key = integration_key.strip().lower()
        stmt = select(TenantIntegrationEntitlement).where(
            TenantIntegrationEntitlement.tenant_id == tenant_id,
            TenantIntegrationEntitlement.integration_key == norm_key,
        )
        ent = (await session.execute(stmt)).scalar_one_or_none()
        if ent:
            ent.is_enabled = False

        await append_platform_audit(
            session,
            action="integration_entitlement.revoked",
            resource_type="tenant_integration_entitlement",
            resource_id=f"{tenant_id}:{norm_key}",
            tenant_id=tenant_id,
            details={"integration_key": norm_key},
            ip_address=ip_address,
            actor=actor,
        )

        await session.commit()
