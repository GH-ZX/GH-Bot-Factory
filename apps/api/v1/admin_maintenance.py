"""Customer-controlled maintenance; never a remote shell or tenant impersonation API."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_roles, require_staff_or_above
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.delivery.models import (
    CustomerRelease,
    CustomerUpdate,
    DiagnosticGrant,
    MaintenanceEvent,
    MaintenanceIssue,
    ReleaseIssue,
)
from packages.operations.models import SupportCase
from packages.operations.service import audit, utc
from packages.telegram.models import Bot
from packages.tenants.models import Role, Tenant

router = APIRouter(prefix="/admin/maintenance", tags=["customer-maintenance"])
support_router = APIRouter(prefix="/maintenance-access", tags=["consented-diagnostics"])
owner = require_roles(Role.OWNER)
IMAGE_PATTERN = r"^[a-z0-9][a-z0-9./:_-]{0,175}@sha256:[a-f0-9]{64}$"


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IssueCreate(Request):
    affected_version: str | None = Field(default=None, min_length=1, max_length=80)
    source_case_id: uuid.UUID | None = None
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=10, max_length=4000)


class IssueChange(Request):
    expected_version: int = Field(ge=1)
    body: str = Field(min_length=3, max_length=4000)
    status: Literal["OPEN", "DIAGNOSING", "FIX_AVAILABLE", "RESOLVED"]
    scope: Literal["UNDECIDED", "SHARED_CORE", "TENANT_CUSTOM"]
    fix_version: str | None = Field(default=None, min_length=1, max_length=80)


class GrantCreate(Request):
    issue_id: uuid.UUID
    hours: int = Field(default=24, ge=1, le=72)


class UpdateCreate(Request):
    release_id: uuid.UUID | None = None
    issue_id: uuid.UUID
    version_label: str = Field(min_length=1, max_length=80)
    image: str = Field(max_length=255, pattern=IMAGE_PATTERN)
    previous_image: str = Field(max_length=255, pattern=IMAGE_PATTERN)
    release_notes: str = Field(min_length=10, max_length=4000)
    migration_notes: str = Field(min_length=10, max_length=4000)


class UpdateChange(Request):
    expected_version: int = Field(ge=1)
    status: Literal["APPROVED", "BACKED_UP", "INSTALLED", "ROLLED_BACK", "CANCELLED"]
    backup_reference: str | None = Field(default=None, min_length=3, max_length=255)
    installed_image: str | None = Field(default=None, max_length=255, pattern=IMAGE_PATTERN)
    schema_revision: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")
    evidence: str = Field(min_length=10, max_length=4000)


def public(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name != "token_digest"}


def event(session, issue, actor, kind, body):
    session.add(MaintenanceEvent(tenant_id=issue.tenant_id, issue_id=issue.id,
                                 actor=actor, kind=kind, body=body))


async def owned(session, model, tenant_id, record_id, lock=False):
    query = select(model).where(model.tenant_id == tenant_id, model.id == record_id)
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, "Maintenance record not found.")
    return row


@router.get("")
async def overview(principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
                   session: AsyncSession = Depends(get_db_session)):
    result = {}
    for name, model in (("issues", MaintenanceIssue), ("updates", CustomerUpdate), ("grants", DiagnosticGrant)):
        rows = await session.scalars(select(model).where(model.tenant_id == principal.tenant_id)
                                    .order_by(model.created_at.desc()).limit(100))
        result[name] = [public(row) for row in rows]
    return result


@router.post("/issues", status_code=201)
async def create_issue(req: IssueCreate, principal: AuthenticatedPrincipal = Depends(owner),
                       session: AsyncSession = Depends(get_db_session)):
    if req.source_case_id:
        await owned(session, SupportCase, principal.tenant_id, req.source_case_id)
    issue = MaintenanceIssue(tenant_id=principal.tenant_id, **req.model_dump())
    session.add(issue)
    await session.flush()
    event(session, issue, str(principal.user_id), "REPORTED", req.description)
    audit(session, principal.tenant_id, principal.user_id, "maintenance.issue_created", issue)
    await session.commit()
    return public(issue)


@router.get("/issues/{issue_id}")
async def issue_history(issue_id: uuid.UUID, principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
                        session: AsyncSession = Depends(get_db_session)):
    issue = await owned(session, MaintenanceIssue, principal.tenant_id, issue_id)
    rows = await session.scalars(select(MaintenanceEvent).where(
        MaintenanceEvent.tenant_id == principal.tenant_id, MaintenanceEvent.issue_id == issue.id
    ).order_by(MaintenanceEvent.created_at.desc(), MaintenanceEvent.id).limit(200))
    return {**public(issue), "events": [public(row) for row in rows]}


@router.patch("/issues/{issue_id}")
async def change_issue(issue_id: uuid.UUID, req: IssueChange,
                       principal: AuthenticatedPrincipal = Depends(owner),
                       session: AsyncSession = Depends(get_db_session)):
    issue = await owned(session, MaintenanceIssue, principal.tenant_id, issue_id, lock=True)
    if issue.version != req.expected_version:
        raise HTTPException(409, "Issue changed. Refresh before saving.")
    if req.status in {"FIX_AVAILABLE", "RESOLVED"} and (not req.fix_version or req.scope == "UNDECIDED"):
        raise HTTPException(400, "Record a fix version and whether it is shared-core or tenant-specific.")
    issue.status, issue.scope, issue.fix_version = req.status, req.scope, req.fix_version
    issue.version += 1
    event(session, issue, str(principal.user_id), req.status, f"Scope: {req.scope}; fix version: {req.fix_version or 'unassigned'}\n{req.body}")
    audit(session, principal.tenant_id, principal.user_id, "maintenance.issue_updated", issue,
          {"status": req.status, "scope": req.scope, "fix_version": req.fix_version})
    await session.commit()
    return public(issue)


@router.post("/grants", status_code=201)
async def create_grant(req: GrantCreate, response: Response,
                       principal: AuthenticatedPrincipal = Depends(owner),
                       session: AsyncSession = Depends(get_db_session)):
    issue = await owned(session, MaintenanceIssue, principal.tenant_id, req.issue_id, lock=True)
    if issue.status == "RESOLVED":
        raise HTTPException(409, "Reopen the issue before granting diagnostic access.")
    active = await session.scalar(select(func.count()).select_from(DiagnosticGrant).where(
        DiagnosticGrant.tenant_id == principal.tenant_id, DiagnosticGrant.issue_id == issue.id,
        DiagnosticGrant.revoked_at.is_(None), DiagnosticGrant.expires_at > datetime.now(UTC)))
    if active >= 3:
        raise HTTPException(409, "Revoke an existing diagnostic grant before creating another.")
    token = secrets.token_urlsafe(32)
    grant = DiagnosticGrant(tenant_id=principal.tenant_id, issue_id=issue.id,
        token_digest=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(hours=req.hours), created_by=principal.user_id)
    session.add(grant)
    await session.flush()
    event(session, issue, str(principal.user_id), "ACCESS_GRANTED", f"Read-only diagnostics authorized until {grant.expires_at.isoformat()}.")
    audit(session, principal.tenant_id, principal.user_id, "maintenance.access_granted", grant)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {**public(grant), "access_token": token, "scope": "read_only_diagnostics"}


@router.post("/grants/{grant_id}/revoke")
async def revoke_grant(grant_id: uuid.UUID, principal: AuthenticatedPrincipal = Depends(owner),
                       session: AsyncSession = Depends(get_db_session)):
    grant = await owned(session, DiagnosticGrant, principal.tenant_id, grant_id, lock=True)
    if grant.revoked_at is None:
        grant.revoked_at = datetime.now(UTC)
        issue = await owned(session, MaintenanceIssue, principal.tenant_id, grant.issue_id)
        event(session, issue, str(principal.user_id), "ACCESS_REVOKED", "Diagnostic access revoked by owner.")
        audit(session, principal.tenant_id, principal.user_id, "maintenance.access_revoked", grant)
    await session.commit()
    return public(grant)


@support_router.get("/diagnostics")
async def diagnostics(response: Response, x_support_token: str = Header(min_length=40, max_length=128),
                      session: AsyncSession = Depends(get_db_session)):
    # Capability is a one-way digest, never a recoverable vault credential or query-string token.
    grant = await session.scalar(select(DiagnosticGrant).where(
        DiagnosticGrant.token_digest == hashlib.sha256(x_support_token.encode()).hexdigest()
    ).with_for_update())
    if not grant or grant.revoked_at or utc(grant.expires_at) <= datetime.now(UTC):
        raise HTTPException(401, "Support access is invalid, expired or revoked.")
    tenant = await session.scalar(select(Tenant).where(Tenant.id == grant.tenant_id, Tenant.is_active.is_(True), Tenant.deleted_at.is_(None)))
    if tenant is None:
        raise HTTPException(401, "Support access unavailable.")
    issue = await owned(session, MaintenanceIssue, grant.tenant_id, grant.issue_id)
    states = await session.execute(select(Bot.is_enabled, func.count()).where(
        Bot.tenant_id == grant.tenant_id).group_by(Bot.is_enabled))
    schema = list((await session.execute(text("SELECT version_num FROM alembic_version"))).scalars())
    # No raw logs, exception strings, usernames, configuration, credentials, orders, or wallet data.
    result = {"scope": "read_only_diagnostics", "issue_id": str(issue.id),
              "issue_status": issue.status, "schema_revisions": schema,
              "bots": [{"enabled": enabled, "count": count} for enabled, count in states],
              "collected_at": datetime.now(UTC).isoformat()}
    event(session, issue, f"grant:{grant.id}", "DIAGNOSTICS_READ", "Allowlisted installation diagnostics read.")
    audit(session, grant.tenant_id, None, "maintenance.diagnostics_read", grant)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/updates", status_code=201)
async def propose_update(req: UpdateCreate, principal: AuthenticatedPrincipal = Depends(owner),
                         session: AsyncSession = Depends(get_db_session)):
    issue = await owned(session, MaintenanceIssue, principal.tenant_id, req.issue_id)
    if req.image == req.previous_image:
        raise HTTPException(400, "New and previous image must differ.")
    if req.release_id:
        release = await owned(session, CustomerRelease, principal.tenant_id, req.release_id)
        linked = await session.scalar(select(ReleaseIssue.id).where(
            ReleaseIssue.tenant_id == principal.tenant_id, ReleaseIssue.issue_id == issue.id,
            ReleaseIssue.release_id == release.id))
        if not linked or any(getattr(req, key) != getattr(release, key)
                             for key in ("version_label", "image", "release_notes", "migration_notes")):
            raise HTTPException(400, "Select a release linked to this issue and use its recorded release details.")
    item = CustomerUpdate(tenant_id=principal.tenant_id, **req.model_dump())
    session.add(item)
    await session.flush()
    event(session, issue, str(principal.user_id), "UPDATE_PROPOSED", f"{req.version_label}: {req.release_notes}\nMigration: {req.migration_notes}")
    audit(session, principal.tenant_id, principal.user_id, "maintenance.update_proposed", item)
    await session.commit()
    return public(item)


@router.patch("/updates/{update_id}")
async def record_update(update_id: uuid.UUID, req: UpdateChange,
                        principal: AuthenticatedPrincipal = Depends(owner),
                        session: AsyncSession = Depends(get_db_session)):
    item = await owned(session, CustomerUpdate, principal.tenant_id, update_id, lock=True)
    transitions = {"PROPOSED": {"APPROVED", "CANCELLED"}, "APPROVED": {"BACKED_UP", "CANCELLED"},
                   "BACKED_UP": {"INSTALLED", "ROLLED_BACK", "CANCELLED"}, "INSTALLED": {"ROLLED_BACK"}}
    if item.version != req.expected_version or req.status not in transitions.get(item.status, set()):
        raise HTTPException(409, "Update changed or transition is not allowed. Refresh before saving.")
    if req.status == "BACKED_UP" and not req.backup_reference:
        raise HTTPException(400, "Record the database and vault backup reference before installation.")
    if req.status in {"INSTALLED", "ROLLED_BACK"}:
        expected_image = item.image if req.status == "INSTALLED" else item.previous_image
        if req.installed_image != expected_image or not req.schema_revision or not item.backup_reference:
            raise HTTPException(400, "Record the matching installed image, schema revision and existing backup evidence.")
    item.status = req.status
    item.evidence = req.evidence
    if req.installed_image:
        item.evidence += f"\nOwner-reported image: {req.installed_image}; schema: {req.schema_revision}"
    if req.status == "BACKED_UP":
        item.backup_reference = req.backup_reference
    item.version += 1
    issue = await owned(session, MaintenanceIssue, principal.tenant_id, item.issue_id)
    event(session, issue, str(principal.user_id), f"UPDATE_{req.status}",
          f"{item.version_label}: {item.evidence}")
    audit(session, principal.tenant_id, principal.user_id, "maintenance.update_recorded", item, {"status": req.status})
    await session.commit()
    return public(item)


class ReleaseCreate(Request):
    version_label: str = Field(min_length=1, max_length=80)
    image: str = Field(max_length=255, pattern=IMAGE_PATTERN)
    release_notes: str = Field(min_length=10, max_length=4000)
    migration_notes: str = Field(min_length=10, max_length=4000)
    rollback_notes: str = Field(min_length=10, max_length=4000)
    issue_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


@router.get("/releases/catalog")
async def release_catalog(principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
                          session: AsyncSession = Depends(get_db_session)):
    releases = (await session.scalars(select(CustomerRelease).where(
        CustomerRelease.tenant_id == principal.tenant_id
    ).order_by(CustomerRelease.created_at.desc()).limit(100))).all()
    links = (await session.scalars(select(ReleaseIssue).where(
        ReleaseIssue.tenant_id == principal.tenant_id,
        ReleaseIssue.release_id.in_([row.id for row in releases])
    ))).all() if releases else []
    return [{**public(row), "issue_ids": [link.issue_id for link in links if link.release_id == row.id]}
            for row in releases]


@router.post("/releases", status_code=201)
async def create_release(req: ReleaseCreate, principal: AuthenticatedPrincipal = Depends(owner),
                         session: AsyncSession = Depends(get_db_session)):
    issues = []
    # Deterministic lock order avoids deadlocks between overlapping releases.
    for issue_id in sorted(set(req.issue_ids)):
        issue = await owned(session, MaintenanceIssue, principal.tenant_id, issue_id, lock=True)
        if issue.scope == "UNDECIDED":
            raise HTTPException(400, "Classify each fix as shared-core or tenant-specific before linking a release.")
        issues.append(issue)
    release = CustomerRelease(tenant_id=principal.tenant_id, **req.model_dump(exclude={"issue_ids"}))
    session.add(release)
    try:
        await session.flush()
        for issue in issues:
            session.add(ReleaseIssue(tenant_id=principal.tenant_id, release_id=release.id, issue_id=issue.id))
            if issue.status != "RESOLVED":
                issue.status = "FIX_AVAILABLE"
                issue.fix_version = release.version_label
                issue.version += 1
            event(session, issue, str(principal.user_id), "RELEASE_LINKED",
                  f"Release {release.version_label} contains a documented fix. Installation and acceptance remain separate.")
        audit(session, principal.tenant_id, principal.user_id, "maintenance.release_recorded", release,
              {"issue_ids": [str(issue.id) for issue in issues], "image": req.image})
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "This release version already exists. Release records are immutable; use a new version.") from exc
    return {**public(release), "issue_ids": [issue.id for issue in issues]}
