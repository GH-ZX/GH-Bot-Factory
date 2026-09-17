from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Category, Product, ProductVariant
from packages.marketplace.models import (
    CommercialQuote,
    DeploymentHandoff,
    HandoffStatus,
    LicenseType,
)
from packages.payments.models import PaymentMethodConfig
from packages.providers.models import Provider, ProviderCredential, ProviderProductMapping
from packages.saas.control_plane import append_platform_audit
from packages.telegram.models import Bot
from packages.telegram.secrets import SecretNotFoundError, get_default_secret_storage
from packages.tenants.models import Membership, Tenant, User


class HandoffError(ValueError):
    """Raised when deployment handoff operations violate safety or business invariants."""


class DeploymentHandoffService:
    @staticmethod
    def generate_license_key(year: int | None = None) -> str:
        yr = year or datetime.now(UTC).year
        part1 = secrets.token_hex(3).upper()
        part2 = secrets.token_hex(3).upper()
        return f"LIC-GHBF-{yr}-{part1}-{part2}"

    @classmethod
    async def create_handoff(
        cls,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        license_type: LicenseType = LicenseType.DEDICATED_DEPLOYMENT,
        licensed_to: str,
        licensed_domain: str | None = None,
        support_plan: str | None = None,
        quote_id: uuid.UUID | None = None,
        handoff_notes: str | None = None,
        actor: str = "LOCAL_PLATFORM_TOKEN",
        ip_address: str | None = None,
    ) -> DeploymentHandoff:
        tenant = await session.get(Tenant, tenant_id)
        if not tenant or not tenant.is_active:
            raise HandoffError(f"Tenant {tenant_id} not found or inactive.")

        if quote_id:
            quote = await session.get(CommercialQuote, quote_id)
            if not quote:
                raise HandoffError(f"Commercial quote {quote_id} not found.")

        license_key = cls.generate_license_key()

        handoff = DeploymentHandoff(
            tenant_id=tenant_id,
            quote_id=quote_id,
            license_type=license_type,
            license_key=license_key,
            licensed_to=licensed_to.strip(),
            licensed_domain=licensed_domain.strip().lower() if licensed_domain else None,
            version_tag="v0.1.0-phase14.5",
            status=HandoffStatus.PREPARING,
            support_plan=support_plan.strip() if support_plan else None,
            handoff_notes=handoff_notes.strip() if handoff_notes else None,
        )
        session.add(handoff)

        await append_platform_audit(
            session,
            action="handoff.created",
            resource_type="deployment_handoff",
            resource_id=license_key,
            tenant_id=tenant_id,
            details={
                "license_type": license_type.value,
                "licensed_to": handoff.licensed_to,
                "licensed_domain": handoff.licensed_domain,
            },
            ip_address=ip_address,
            actor=actor,
        )

        await session.commit()
        await session.refresh(handoff)
        return handoff

    @classmethod
    async def generate_single_tenant_export_bundle(
        cls,
        session: AsyncSession,
        *,
        handoff_id: uuid.UUID,
        output_dir: Path | None = None,
        actor: str = "LOCAL_PLATFORM_TOKEN",
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        handoff = await session.get(DeploymentHandoff, handoff_id)
        if not handoff:
            raise HandoffError(f"Deployment handoff {handoff_id} not found.")

        tenant = await session.get(Tenant, handoff.tenant_id)
        if not tenant:
            raise HandoffError("Tenant record missing.")

        # 1. Gather isolated tenant-scoped data
        users = (
            await session.execute(
                select(User, Membership)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.tenant_id == tenant.id)
            )
        ).all()

        bots = (
            await session.execute(select(Bot).where(Bot.tenant_id == tenant.id))
        ).scalars().all()

        categories = (
            await session.execute(select(Category).where(Category.tenant_id == tenant.id))
        ).scalars().all()

        products = (
            await session.execute(select(Product).where(Product.tenant_id == tenant.id))
        ).scalars().all()

        variants = (
            await session.execute(
                select(ProductVariant)
                .join(Product, Product.id == ProductVariant.product_id)
                .where(Product.tenant_id == tenant.id)
            )
        ).scalars().all()

        payments = (
            await session.execute(
                select(PaymentMethodConfig).where(PaymentMethodConfig.tenant_id == tenant.id)
            )
        ).scalars().all()

        providers = (
            await session.execute(select(Provider).where(Provider.tenant_id == tenant.id))
        ).scalars().all()

        mappings = (
            await session.execute(
                select(ProviderProductMapping).where(ProviderProductMapping.tenant_id == tenant.id)
            )
        ).scalars().all()

        # Extract tenant credentials safely from SecretStorage
        credentials = (
            await session.execute(
                select(ProviderCredential).where(ProviderCredential.tenant_id == tenant.id)
            )
        ).scalars().all()

        secret_storage = get_default_secret_storage()
        extracted_secrets: dict[str, str] = {}
        for cred in credentials:
            try:
                secret_val = await secret_storage.get_secret(cred.secret_ref)
                extracted_secrets[cred.credential_type] = secret_val
            except (SecretNotFoundError, KeyError, OSError):
                extracted_secrets[cred.credential_type] = "<UNCONFIGURED_IN_VAULT>"

        # 2. Build sanitized bundle
        bundle_data = {
            "version": "ghbf-standalone-v1",
            "license": {
                "key": handoff.license_key,
                "type": handoff.license_type.value,
                "licensed_to": handoff.licensed_to,
                "licensed_domain": handoff.licensed_domain,
                "version_tag": handoff.version_tag,
                "exported_at": datetime.now(UTC).isoformat(),
            },
            "tenant": {
                "id": str(tenant.id),
                "name": tenant.name,
                "slug": tenant.slug,
                "settings": tenant.settings,
            },
            "members": [
                {
                    "username": u.username,
                    "first_name": u.first_name,
                    "role": m.role.value,
                    "permissions": m.permissions,
                }
                for u, m in users
            ],
            "bots": [
                {
                    "username": b.username,
                    "display_name": b.display_name,
                    "config": b.config,
                    "token_secret_ref": b.token_secret_ref,
                }
                for b in bots
            ],
            "catalog": {
                "categories": [
                    {"id": str(c.id), "name": c.name, "slug": c.slug}
                    for c in categories
                ],
                "products": [
                    {"id": str(p.id), "title": p.title, "description": p.description, "category_id": str(p.category_id) if p.category_id else None}
                    for p in products
                ],
                "variants": [
                    {"id": str(v.id), "product_id": str(v.product_id), "sku": v.sku, "price": str(v.price), "currency": v.currency, "stock_quantity": v.stock_quantity}
                    for v in variants
                ],
            },
            "payments": [
                {
                    "code": m.code,
                    "display_name": m.display_name,
                    "method_type": m.method_type.value,
                    "verification_mode": m.verification_mode.value,
                    "is_enabled": m.is_enabled,
                }
                for m in payments
            ],
            "providers": [
                {
                    "name": pr.name,
                    "category": pr.category.value,
                    "provider_type": pr.provider_type,
                    "priority": pr.priority,
                    "is_enabled": pr.is_enabled,
                }
                for pr in providers
            ],
            "provider_credentials": extracted_secrets,
            "product_mappings": [
                {
                    "product_id": str(pm.product_id),
                    "external_product_id": pm.external_product_id,
                    "cost_price": str(pm.cost_price),
                    "cost_currency": pm.cost_currency,
                }
                for pm in mappings
            ],
        }

        # 3. Serialize and compute checksum
        raw_json = json.dumps(bundle_data, indent=2, sort_keys=True).encode("utf-8")
        checksum = hashlib.sha256(raw_json).hexdigest()

        # 4. Write artifact
        root = output_dir or (Path(__file__).resolve().parents[2] / "artifacts" / "handoffs")
        target_dir = root / f"{tenant.slug}-{str(handoff.id)[:8]}"
        target_dir.mkdir(parents=True, exist_ok=True)

        bundle_path = target_dir / "bundle.json"
        bundle_path.write_bytes(raw_json)

        manifest_path = target_dir / "manifest.json"
        manifest_data = {
            "format": "ghbf-single-tenant-handoff-v1",
            "license_key": handoff.license_key,
            "license_type": handoff.license_type.value,
            "licensed_to": handoff.licensed_to,
            "tenant_id": str(tenant.id),
            "tenant_slug": tenant.slug,
            "version_tag": handoff.version_tag,
            "checksum_sha256": checksum,
            "created_at": datetime.now(UTC).isoformat(),
        }
        manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

        # Write standalone compose template
        compose_path = target_dir / "docker-compose.standalone.yml"
        compose_path.write_text(
            f"""# GH-Bot-Factory Standalone Single-Tenant Deployment
# Licensed to: {handoff.licensed_to} ({handoff.license_key})
services:
  api:
    image: gh-bot-factory:standalone-{handoff.version_tag}
    ports:
      - "8010:8010"
    env_file: .env
    depends_on:
      - postgres
      - redis
  bot-runtime:
    image: gh-bot-factory:standalone-{handoff.version_tag}
    command: ["python", "-m", "apps.bot_runtime.main"]
    env_file: .env
    depends_on:
      - postgres
      - redis
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: bot_standalone
      POSTGRES_USER: bot_user
      POSTGRES_PASSWORD: ${{DB_PASSWORD:-change_me_securely}}
  redis:
    image: redis:7-alpine
""",
            encoding="utf-8",
        )

        # 5. Update handoff record
        handoff.status = HandoffStatus.EXPORTED
        handoff.export_checksum = checksum
        handoff.export_artifact_path = str(bundle_path)

        await append_platform_audit(
            session,
            action="handoff.bundle_generated",
            resource_type="deployment_handoff",
            resource_id=handoff.license_key,
            tenant_id=tenant.id,
            details={
                "checksum_sha256": checksum,
                "artifact_path": str(bundle_path),
            },
            ip_address=ip_address,
            actor=actor,
        )

        await session.commit()
        await session.refresh(handoff)

        return {
            "handoff_id": str(handoff.id),
            "license_key": handoff.license_key,
            "tenant_slug": tenant.slug,
            "checksum_sha256": checksum,
            "artifact_dir": str(target_dir),
            "bundle_file": str(bundle_path),
        }

    @classmethod
    async def deactivate_managed_runtime(
        cls,
        session: AsyncSession,
        *,
        handoff_id: uuid.UUID,
        actor: str = "LOCAL_PLATFORM_TOKEN",
        ip_address: str | None = None,
    ) -> DeploymentHandoff:
        handoff = await session.get(DeploymentHandoff, handoff_id)
        if not handoff:
            raise HandoffError(f"Deployment handoff {handoff_id} not found.")

        # Deactivate all bots on managed factory to prevent Telegram HTTP 409 conflict
        bots = (
            await session.execute(select(Bot).where(Bot.tenant_id == handoff.tenant_id))
        ).scalars().all()

        for bot in bots:
            bot.is_enabled = False
            bot.runtime_revision += 1

        now = datetime.now(UTC)
        handoff.runtime_deactivated = True
        handoff.runtime_deactivated_at = now
        handoff.status = HandoffStatus.HANDED_OFF
        handoff.handed_off_at = now

        await append_platform_audit(
            session,
            action="handoff.runtime_deactivated",
            resource_type="deployment_handoff",
            resource_id=handoff.license_key,
            tenant_id=handoff.tenant_id,
            details={
                "bots_deactivated": len(bots),
                "deactivated_at": now.isoformat(),
            },
            ip_address=ip_address,
            actor=actor,
        )

        await session.commit()
        await session.refresh(handoff)
        return handoff
