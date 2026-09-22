"""Owner-reported installation/backup evidence, separate from observed health."""
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_roles, require_staff_or_above
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.operations.service import audit
from packages.tenants.models import Role, Tenant

router = APIRouter(prefix='/admin/installation', tags=['installation-evidence'])
owner = require_roles(Role.OWNER)


class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_version: int = Field(ge=0)
    image: str = Field(pattern=r'^[a-z0-9][a-z0-9./:_-]{0,175}@sha256:[a-f0-9]{64}$')
    release_version: str = Field(min_length=1, max_length=80)
    backup_status: Literal['NOT_RECORDED', 'COMPLETED', 'FAILED']
    backup_reference: str = Field(default='', max_length=255)
    database_backup_sha256: str = Field(default='', pattern=r'^(?:[a-f0-9]{64})?$')
    vault_backup_sha256: str = Field(default='', pattern=r'^(?:[a-f0-9]{64})?$')
    backup_completed_at: datetime | None = None
    restore_reference: str = Field(default='', max_length=255)


def evidence(tenant):
    return (tenant.settings or {}).get('_installation_evidence', {'version': 0, 'backup_status': 'NOT_RECORDED'})


@router.get('')
async def read(principal: AuthenticatedPrincipal = Depends(require_staff_or_above), session: AsyncSession = Depends(get_db_session)):
    tenant = await session.scalar(select(Tenant).where(Tenant.id == principal.tenant_id))
    schema = list((await session.execute(text('SELECT version_num FROM alembic_version'))).scalars())
    return {'reported': evidence(tenant), 'observed_schema_revisions': schema,
            'evidence_kind': 'owner_reported', 'acceptance': 'not_assessed'}


@router.put('')
async def save(req: Evidence, principal: AuthenticatedPrincipal = Depends(owner),
               session: AsyncSession = Depends(get_db_session)):
    tenant = await session.scalar(select(Tenant).where(Tenant.id == principal.tenant_id).with_for_update())
    if evidence(tenant).get('version', 0) != req.expected_version:
        raise HTTPException(409, 'Evidence changed. Reload before saving.')
    if req.backup_status == 'COMPLETED':
        if not all((req.backup_reference, req.database_backup_sha256, req.vault_backup_sha256, req.backup_completed_at)):
            raise HTTPException(400, 'Record the database and vault backup checksums, reference and completion time.')
        if req.backup_completed_at.tzinfo is None or req.backup_completed_at > datetime.now(UTC):
            raise HTTPException(400, 'Use an actual backup time with timezone, not a future timestamp.')
    tenant.settings = {**(tenant.settings or {}), '_installation_evidence': {
        **req.model_dump(mode='json', exclude={'expected_version'}),
        'version': req.expected_version + 1, 'recorded_at': datetime.now(UTC).isoformat(),
    }}
    audit(session, principal.tenant_id, principal.user_id, 'installation.evidence_recorded', tenant,
          {'version': req.expected_version + 1, 'backup_status': req.backup_status, 'evidence_kind': 'owner_reported'})
    await session.commit()
    return {'reported': evidence(tenant), 'evidence_kind': 'owner_reported'}


@router.get('/support-report')
async def support_report(response: Response, principal: AuthenticatedPrincipal = Depends(owner),
                         session: AsyncSession = Depends(get_db_session)):
    tenant = await session.scalar(select(Tenant).where(Tenant.id == principal.tenant_id))
    saved = evidence(tenant)
    # Deliberately omit free-text evidence, customer identities, domains and backup locations.
    schema = list((await session.execute(text('SELECT version_num FROM alembic_version'))).scalars())
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Disposition'] = 'attachment; filename="support-diagnostics.json"'
    audit(session, principal.tenant_id, principal.user_id, 'maintenance.report_downloaded', tenant)
    await session.commit()
    return {'format': 'ghbf-safe-diagnostics-v1', 'collected_at': datetime.now(UTC).isoformat(),
            'observed_schema_revisions': schema, 'reported_image': saved.get('image'),
            'backup_status': saved.get('backup_status', 'NOT_RECORDED'),
            'evidence_kind': 'owner_reported_except_schema', 'acceptance': 'not_assessed'}
