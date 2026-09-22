"""Operator-owned handoff planning. Checklist entries never claim a real deployment occurred."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.platform_deps import PlatformOperator, require_platform_operator
from packages.commerce.models import Product
from packages.core.database import get_db_session
from packages.delivery.models import MaintenanceIssue
from packages.marketplace.models import (
    CommercialQuote,
    DeploymentHandoff,
    HandoffStatus,
    QuoteStatus,
)
from packages.payments.models import PaymentMethodConfig
from packages.saas.control_plane import append_platform_audit
from packages.saas.models import PlatformAuditLog
from packages.telegram.models import Bot
from packages.tenants.models import Membership, Role, Tenant

router = APIRouter(prefix="/platform/delivery", tags=["delivery-center"])
IMAGE = r"^(?:[a-z0-9][a-z0-9./:_-]{0,175}@sha256:[a-f0-9]{64})?$"


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=0)
    image: str = Field(default="", max_length=255, pattern=IMAGE)
    release_version: str = Field(default="", max_length=40)
    report_language: Literal["en", "ar"] = "en"
    hosting: Literal["supabase", "postgresql"] = "supabase"
    destination_label: str = Field(default="", max_length=200)
    scope_approved: bool = False
    owner_access_prepared: bool = False
    database_prepared: bool = False
    domain_prepared: bool = False
    release_notes: str = Field(default="", max_length=4000)
    installation_notes: str = Field(default="", max_length=4000)
    acceptance_reference: str = Field(default="", max_length=500)
    receipt_reference: str = Field(default="", max_length=500)


async def handoff(session, handoff_id, lock=False):
    query = select(DeploymentHandoff).where(DeploymentHandoff.id == handoff_id)
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, "Handoff not found.")
    return row


async def detail(session, item):
    tenant_id = item.tenant_id
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    counts = {}
    for name, model, filters in (
        ("bots", Bot, [Bot.deleted_at.is_(None), Bot.credential_status == "VERIFIED"]),
        ("products", Product, [Product.deleted_at.is_(None), Product.is_active.is_(True)]),
        ("payments", PaymentMethodConfig, [PaymentMethodConfig.is_enabled.is_(True)]),
        ("owners", Membership, [Membership.role == Role.OWNER, Membership.is_active.is_(True)]),
    ):
        counts[name] = await session.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id, *filters))
    quote = await session.scalar(select(CommercialQuote).where(CommercialQuote.id == item.quote_id, CommercialQuote.tenant_id == tenant_id)) if item.quote_id else None
    history = (await session.scalars(select(PlatformAuditLog).where(
        PlatformAuditLog.tenant_id == tenant_id, PlatformAuditLog.resource_type == "deployment_handoff",
        PlatformAuditLog.resource_id.in_([str(item.id), item.license_key])
    ).order_by(PlatformAuditLog.created_at.desc()).limit(30))).all()
    plan = item.delivery_details or {}
    checks = [
        ("scope", "Scope approved", bool(quote and quote.status == QuoteStatus.ACCEPTED) or bool(plan.get("scope_approved"))),
        ("owner", "Customer owner account", counts["owners"] > 0 and bool(plan.get("owner_access_prepared"))),
        ("bot", "Customer bot credential verified", counts["bots"] > 0),
        ("catalog", "Active products configured", counts["products"] > 0),
        ("payments", "Payment method configured", counts["payments"] > 0),
        ("image", "Release image selected", bool(plan.get("image") and plan.get("release_version"))),
        ("database", "Destination database prepared", bool(plan.get("database_prepared"))),
        ("domain", "Destination HTTPS prepared", bool(plan.get("domain_prepared"))),
        ("export", "Encrypted export generated", bool(item.export_checksum)),
    ]
    return {"history": [{"action": row.action, "created_at": row.created_at} for row in history],
            "id": item.id, "tenant_id": tenant_id, "tenant_name": tenant.name if tenant else item.licensed_to,
            "licensed_to": item.licensed_to, "status": item.status.value,
            "version": plan.get("version", 0), "plan": {k: v for k, v in plan.items() if k != "version"},
            "scope": quote.scope_snapshot if quote else {}, "counts": counts,
            "checks": [{"key": key, "label": label, "complete": complete} for key, label, complete in checks],
            "configuration_ready": all(complete for _, _, complete in checks),
            "export_checksum": item.export_checksum, "handed_off_at": item.handed_off_at,
            "runtime_deactivated": item.runtime_deactivated,
            "acceptance_recorded": bool(plan.get("acceptance_reference")),
            "receipt_recorded": bool(plan.get("receipt_reference"))}


@router.get("/workspace/overview")
async def workspace(_: PlatformOperator = Depends(require_platform_operator),
                    session: AsyncSession = Depends(get_db_session)):
    # Installation authority may enumerate projects; every related query remains tenant-bound.
    rows = (await session.scalars(select(DeploymentHandoff).order_by(
        DeploymentHandoff.updated_at.desc(), DeploymentHandoff.id).limit(100))).all()
    tenant_ids = {row.tenant_id for row in rows}
    issues = dict((await session.execute(select(MaintenanceIssue.tenant_id, func.count()).where(
        MaintenanceIssue.tenant_id.in_(tenant_ids), MaintenanceIssue.status != "RESOLVED"
    ).group_by(MaintenanceIssue.tenant_id))).all()) if tenant_ids else {}
    return {"projects": [{"id": row.id, "licensed_to": row.licensed_to,
        "licensed_domain": row.licensed_domain, "status": row.status.value,
        "version_tag": row.version_tag, "created_at": row.created_at,
        "release_selected": bool((row.delivery_details or {}).get("image")),
        "snapshot_generated": bool(row.export_checksum),
        "acceptance_recorded": bool((row.delivery_details or {}).get("acceptance_reference")),
        "receipt_recorded": row.status == HandoffStatus.HANDED_OFF,
        "open_issues": issues.get(row.tenant_id, 0),
        "evidence_kind": "operator_reported", "production_qualified": False,
    } for row in rows], "limit": 100}


@router.get("/{handoff_id}")
async def get_delivery(handoff_id: uuid.UUID, _: PlatformOperator = Depends(require_platform_operator),
                       session: AsyncSession = Depends(get_db_session)):
    return await detail(session, await handoff(session, handoff_id))


@router.put("/{handoff_id}")
async def save_delivery(handoff_id: uuid.UUID, req: Plan,
                        operator: PlatformOperator = Depends(require_platform_operator),
                        session: AsyncSession = Depends(get_db_session)):
    item = await handoff(session, handoff_id, lock=True)
    if item.status in {HandoffStatus.HANDED_OFF, HandoffStatus.CANCELLED}:
        raise HTTPException(409, "Completed or cancelled handoffs cannot be edited.")
    if (item.delivery_details or {}).get("version", 0) != req.expected_version:
        raise HTTPException(409, "Delivery plan changed. Reload before saving.")
    item.delivery_details = {**req.model_dump(exclude={"expected_version"}), "version": req.expected_version + 1}
    if req.release_version:
        item.version_tag = req.release_version
    await append_platform_audit(session, action="handoff.plan_saved", resource_type="deployment_handoff",
        resource_id=str(item.id), tenant_id=item.tenant_id, actor=operator.actor,
        details={"version": req.expected_version + 1, "image": req.image, "report_language": req.report_language})
    await session.commit()
    return await detail(session, item)


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    confirm_customer_received: Literal[True]


@router.post("/{handoff_id}/complete")
async def complete_delivery(handoff_id: uuid.UUID, req: Receipt,
                            operator: PlatformOperator = Depends(require_platform_operator),
                            session: AsyncSession = Depends(get_db_session)):
    item = await handoff(session, handoff_id, lock=True)
    if item.status == HandoffStatus.HANDED_OFF:
        return await detail(session, item)
    current = await detail(session, item)
    if item.status == HandoffStatus.CANCELLED or current["version"] != req.expected_version:
        raise HTTPException(409, "Handoff changed. Reload before completing.")
    if not (current["configuration_ready"] and current["acceptance_recorded"] and current["receipt_recorded"] and item.runtime_deactivated):
        raise HTTPException(409, "Finish configuration, record acceptance and customer receipt, and stop the source runtime first.")
    item.status = HandoffStatus.HANDED_OFF
    item.handed_off_at = datetime.now(UTC)
    await append_platform_audit(session, action="handoff.receipt_recorded", resource_type="deployment_handoff",
        resource_id=str(item.id), tenant_id=item.tenant_id, actor=operator.actor,
        details={"version": current["version"], "evidence_kind": "operator_reported"})
    await session.commit()
    return await detail(session, item)
