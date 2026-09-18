from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import settings
from packages.marketplace.models import (
    CommercialQuote,
    DeploymentHandoff,
    HandoffStatus,
    LicenseType,
    QuoteStatus,
)
from packages.marketplace.tenant_bundle import FORMAT, BundleError, encrypt, snapshot, write_private
from packages.saas.control_plane import append_platform_audit
from packages.telegram.models import Bot
from packages.telegram.secrets import get_default_secret_storage
from packages.tenants.models import Tenant


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
            if not quote or quote.tenant_id != tenant_id or quote.status != QuoteStatus.ACCEPTED:
                raise HandoffError("Handoff requires an accepted quote belonging to this tenant.")

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
        passphrase: str,
        confirm_quiesced: bool = False,
        output_dir: Path | None = None,
        actor: str = "LOCAL_PLATFORM_TOKEN",
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        if not confirm_quiesced:
            raise HandoffError("Pause source writes and workers before export, then confirm the source is quiesced.")
        handoff = await session.get(DeploymentHandoff, handoff_id)
        if not handoff or handoff.status == HandoffStatus.CANCELLED:
            raise HandoffError("Deployment handoff is missing or cancelled.")
        try:
            async with AsyncSession(bind=session.bind) as snapshot_session:
                if snapshot_session.bind.dialect.name == "postgresql":
                    await snapshot_session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                payload = await snapshot(snapshot_session, handoff.tenant_id, get_default_secret_storage())
            payload["license"] = {"key": handoff.license_key, "type": handoff.license_type.value}
            ciphertext = encrypt(payload, passphrase)
        except BundleError as exc:
            raise HandoffError(str(exc)) from exc
        checksum = hashlib.sha256(ciphertext).hexdigest()
        root = output_dir or Path(settings.handoff_export_dir)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        target_dir = root / f"{handoff.id}-{uuid.uuid4().hex}"
        target_dir.mkdir(mode=0o700)
        bundle_path = target_dir / "tenant.ghbf.enc"
        write_private(bundle_path, ciphertext)
        write_private(target_dir / "manifest.json", json.dumps({
            "format": FORMAT, "checksum_sha256": checksum,
            "schema": payload["schema"], "tenant_id": payload["tenant_id"],
            "row_counts": {name: len(rows) for name, rows in payload["tables"].items()},
        }, indent=2).encode())
        handoff.status = HandoffStatus.EXPORTED
        handoff.export_checksum = checksum
        handoff.export_artifact_path = str(bundle_path)
        await append_platform_audit(
            session, action="handoff.bundle_generated", resource_type="deployment_handoff",
            resource_id=handoff.license_key, tenant_id=handoff.tenant_id,
            details={"checksum_sha256": checksum, "format": FORMAT},
            ip_address=ip_address, actor=actor,
        )
        await session.commit()
        return {"handoff_id": str(handoff.id), "license_key": handoff.license_key,
                "tenant_slug": payload["tables"]["tenants"][0]["slug"],
                "checksum_sha256": checksum, "artifact_dir": str(target_dir), "bundle_file": str(bundle_path)}

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
            if bot.is_enabled:
                bot.is_enabled = False
                bot.runtime_revision += 1

        now = datetime.now(UTC)
        handoff.runtime_deactivated = True
        handoff.runtime_deactivated_at = now
        # Desired-state disable is not proof of observed runtime stop or successful destination restore.
        # Keep EXPORTED if an artifact exists; never claim completed handoff here.
        if not handoff.export_artifact_path:
            handoff.status = HandoffStatus.READY_FOR_EXPORT

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
