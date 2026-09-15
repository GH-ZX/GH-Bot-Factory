import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import require_admin_or_owner
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

router = APIRouter(prefix="/admin/members", tags=["admin-members"])

_ROLE_RANK = {
    Role.CUSTOMER: 0,
    Role.STAFF: 1,
    Role.MANAGER: 2,
    Role.ADMIN: 3,
    Role.OWNER: 4,
}
_PERMISSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._*-]{0,99}$")


class MemberResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    username: str | None
    email: str | None
    first_name: str | None
    last_name: str | None
    account_active: bool
    role: Role
    permissions: list[str]
    is_active: bool
    is_self: bool
    created_at: datetime
    updated_at: datetime


class MemberListResponse(BaseModel):
    members: list[MemberResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


class MemberUpdateRequest(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    permissions: list[str] | None = Field(default=None, max_length=50)


def _member_response(membership: Membership, principal: AuthenticatedPrincipal) -> MemberResponse:
    user = membership.user
    return MemberResponse(
        id=membership.id,
        user_id=membership.user_id,
        username=user.username,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        account_active=user.is_active,
        role=membership.role,
        permissions=list(membership.permissions or []),
        is_active=membership.is_active,
        is_self=membership.user_id == principal.user_id,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


def _validate_permissions(permissions: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in permissions:
        permission = raw.strip()
        if not _PERMISSION_RE.fullmatch(permission):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid permission identifier: {raw!r}.",
            )
        if permission not in normalized:
            normalized.append(permission)
    return normalized


def _enforce_role_boundary(actor_role: Role, target_role: Role, requested_role: Role | None) -> None:
    if actor_role == Role.OWNER:
        return
    if actor_role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins or owners can manage members.")
    if _ROLE_RANK[target_role] >= _ROLE_RANK[Role.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins cannot modify admin or owner memberships.",
        )
    if requested_role is not None and _ROLE_RANK[requested_role] >= _ROLE_RANK[Role.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner can grant admin or owner roles.",
        )


@router.get("", response_model=MemberListResponse)
async def list_members(
    q: str | None = Query(default=None, max_length=100),
    role: Role | None = None,
    active: bool | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> MemberListResponse:
    predicates = [Membership.tenant_id == principal.tenant_id]
    if role is not None:
        predicates.append(Membership.role == role)
    if active is not None:
        predicates.append(Membership.is_active.is_(active))
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        predicates.append(
            or_(
                User.username.ilike(pattern),
                User.email.ilike(pattern),
                User.first_name.ilike(pattern),
                User.last_name.ilike(pattern),
            )
        )

    count_stmt = select(func.count()).select_from(Membership).join(User).where(*predicates)
    total = int((await session.scalar(count_stmt)) or 0)
    stmt = (
        select(Membership)
        .join(User)
        .where(*predicates)
        .options(selectinload(Membership.user))
        .order_by(Membership.created_at.asc(), Membership.id.asc())
        .offset(offset)
        .limit(limit)
    )
    memberships = list((await session.execute(stmt)).scalars().all())
    next_offset = offset + limit if offset + len(memberships) < total else None
    return MemberListResponse(
        members=[_member_response(row, principal) for row in memberships],
        total=total,
        offset=offset,
        limit=limit,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.patch("/{membership_id}", response_model=MemberResponse)
async def update_member(
    membership_id: uuid.UUID,
    req: MemberUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> MemberResponse:
    # Serialize membership mutations per tenant so concurrent owner changes cannot
    # both pass the last-owner invariant.
    tenant = (
        await session.execute(
            select(Tenant).where(Tenant.id == principal.tenant_id).with_for_update()
        )
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found.")

    membership = (
        await session.execute(
            select(Membership)
            .where(
                Membership.id == membership_id,
                Membership.tenant_id == principal.tenant_id,
            )
            .options(selectinload(Membership.user))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")

    actor_role = next(iter(principal.roles))
    _enforce_role_boundary(actor_role, membership.role, req.role)

    requested_role = req.role if req.role is not None else membership.role
    requested_active = req.is_active if req.is_active is not None else membership.is_active
    removes_active_owner = (
        membership.role == Role.OWNER
        and membership.is_active
        and (requested_role != Role.OWNER or not requested_active)
    )
    if removes_active_owner:
        active_owner_count = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(Membership)
                    .where(
                        Membership.tenant_id == principal.tenant_id,
                        Membership.role == Role.OWNER,
                        Membership.is_active.is_(True),
                    )
                )
            )
            or 0
        )
        if active_owner_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The tenant must retain at least one active owner.",
            )

    old_role = membership.role
    old_active = membership.is_active
    old_permissions = list(membership.permissions or [])
    if req.role is not None:
        membership.role = req.role
    if req.is_active is not None:
        membership.is_active = req.is_active
    if req.permissions is not None:
        membership.permissions = _validate_permissions(req.permissions)

    changed_fields = []
    if membership.role != old_role:
        changed_fields.append("role")
    if membership.is_active != old_active:
        changed_fields.append("is_active")
    if list(membership.permissions or []) != old_permissions:
        changed_fields.append("permissions")

    if changed_fields:
        session.add(
            AuditLog(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="MEMBERSHIP_UPDATED",
                resource_type="membership",
                resource_id=str(membership.id),
                details={
                    "target_user_id": str(membership.user_id),
                    "fields": changed_fields,
                    "old_role": old_role.value,
                    "new_role": membership.role.value,
                    "old_active": old_active,
                    "new_active": membership.is_active,
                    "permissions_changed": "permissions" in changed_fields,
                },
            )
        )
        await session.commit()
    else:
        await session.rollback()

    refreshed = (
        await session.execute(
            select(Membership)
            .where(Membership.id == membership_id, Membership.tenant_id == principal.tenant_id)
            .options(selectinload(Membership.user))
        )
    ).scalar_one()
    return _member_response(refreshed, principal)
