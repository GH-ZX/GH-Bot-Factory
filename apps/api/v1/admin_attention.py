"""Actionable tenant observations and expiring, audited acknowledgements."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_admin_or_owner, require_staff_or_above
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.operations.attention import attention_snapshot
from packages.operations.service import audit
from packages.tenants.models import Tenant

router = APIRouter(prefix='/admin/attention', tags=['operational-attention'])


@router.get('')
async def overview(principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
                   session: AsyncSession = Depends(get_db_session)):
    return await attention_snapshot(session, principal.tenant_id)


class Acknowledge(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=1, max_length=100)
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.post('/acknowledge')
async def acknowledge(req: Acknowledge, principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
                      session: AsyncSession = Depends(get_db_session)):
    tenant = await session.scalar(select(Tenant).where(Tenant.id == principal.tenant_id).with_for_update())
    snapshot = await attention_snapshot(session, principal.tenant_id)
    if not any(row['key'] == req.key and row['fingerprint'] == req.fingerprint for row in snapshot['alerts']):
        raise HTTPException(409, 'Observation changed or resolved. Refresh before acknowledging.')
    now = datetime.now(UTC)
    settings = dict(tenant.settings or {})
    acks = {key: value for key, value in settings.get('_attention_acknowledgements', {}).items()
            if value.get('until', '') > now.isoformat()}
    # This is UI attention state, never financial resolution authority.
    if len(acks) >= 250 and req.key not in acks:
        raise HTTPException(409, 'Acknowledgement limit reached; existing entries expire within 24 hours.')
    acks[req.key] = {'fingerprint': req.fingerprint, 'until': (now + timedelta(hours=24)).isoformat()}
    settings['_attention_acknowledgements'] = acks
    tenant.settings = settings
    audit(session, principal.tenant_id, principal.user_id, 'operations.attention_acknowledged', tenant,
          {'key': req.key, 'fingerprint': req.fingerprint})
    await session.commit()
    return {'acknowledged': True, 'resolved': False}
