import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase7-3-member-management-test-jwt-secret-0123456789abcdef"


async def create_identity(
    session: AsyncSession,
    tenant: Tenant,
    role: Role,
    *,
    username: str | None = None,
) -> tuple[User, Membership, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=username or f"{role.value.lower()}_{uuid.uuid4().hex[:8]}",
        first_name=role.value.title(),
        is_active=True,
    )
    session.add(user)
    await session.flush()
    membership = Membership(
        tenant_id=tenant.id,
        user_id=user.id,
        role=role,
        permissions=[],
        is_active=True,
    )
    session.add(membership)
    await session.flush()
    token = AuthTokenService(secret_key=TEST_JWT_SECRET).issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[role],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )
    return user, membership, token


@pytest_asyncio.fixture
async def member_admin_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "session": db_session}
    app.dependency_overrides.clear()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_only_admin_or_owner_can_list_members_and_listing_is_tenant_scoped(
    member_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = member_admin_env["client"]
    session: AsyncSession = member_admin_env["session"]
    tenant = Tenant(name="Members A", slug=f"members-a-{uuid.uuid4().hex[:6]}", is_active=True)
    other = Tenant(name="Members B", slug=f"members-b-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, _, owner_token = await create_identity(session, tenant, Role.OWNER, username="visible_owner")
    _, _, staff_token = await create_identity(session, tenant, Role.STAFF, username="visible_staff")
    await create_identity(session, other, Role.OWNER, username="hidden_owner")
    await session.commit()

    forbidden = await client.get("/api/v1/admin/members", headers=auth(staff_token))
    assert forbidden.status_code == 403

    response = await client.get("/api/v1/admin/members", headers=auth(owner_token))
    assert response.status_code == 200, response.text
    usernames = {row["username"] for row in response.json()["members"]}
    assert usernames == {"visible_owner", "visible_staff"}
    assert "hidden_owner" not in response.text


async def test_admin_cannot_manage_peer_admin_or_grant_admin_owner_roles(
    member_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = member_admin_env["client"]
    session: AsyncSession = member_admin_env["session"]
    tenant = Tenant(name="Role Boundary", slug=f"roles-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    await create_identity(session, tenant, Role.OWNER)
    _, admin_membership, admin_token = await create_identity(session, tenant, Role.ADMIN)
    _, manager_membership, _ = await create_identity(session, tenant, Role.MANAGER)
    await session.commit()

    peer = await client.patch(
        f"/api/v1/admin/members/{admin_membership.id}",
        headers=auth(admin_token),
        json={"role": "MANAGER"},
    )
    assert peer.status_code == 403

    escalate = await client.patch(
        f"/api/v1/admin/members/{manager_membership.id}",
        headers=auth(admin_token),
        json={"role": "ADMIN"},
    )
    assert escalate.status_code == 403

    allowed = await client.patch(
        f"/api/v1/admin/members/{manager_membership.id}",
        headers=auth(admin_token),
        json={"role": "STAFF", "permissions": ["catalog:read", "orders:read"]},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["role"] == "STAFF"
    assert allowed.json()["permissions"] == ["catalog:read", "orders:read"]


async def test_last_active_owner_cannot_be_demoted_or_deactivated(
    member_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = member_admin_env["client"]
    session: AsyncSession = member_admin_env["session"]
    tenant = Tenant(name="Last Owner", slug=f"last-owner-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    _, owner_membership, owner_token = await create_identity(session, tenant, Role.OWNER)
    await session.commit()

    deactivate = await client.patch(
        f"/api/v1/admin/members/{owner_membership.id}",
        headers=auth(owner_token),
        json={"is_active": False},
    )
    assert deactivate.status_code == 409

    demote = await client.patch(
        f"/api/v1/admin/members/{owner_membership.id}",
        headers=auth(owner_token),
        json={"role": "ADMIN"},
    )
    assert demote.status_code == 409


async def test_owner_can_manage_roles_and_deactivation_takes_effect_immediately(
    member_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = member_admin_env["client"]
    session: AsyncSession = member_admin_env["session"]
    tenant = Tenant(name="Owner Ops", slug=f"owner-ops-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    owner, _, owner_token = await create_identity(session, tenant, Role.OWNER)
    _, manager_membership, manager_token = await create_identity(session, tenant, Role.MANAGER)
    await session.commit()

    promoted = await client.patch(
        f"/api/v1/admin/members/{manager_membership.id}",
        headers=auth(owner_token),
        json={"role": "ADMIN"},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "ADMIN"

    deactivated = await client.patch(
        f"/api/v1/admin/members/{manager_membership.id}",
        headers=auth(owner_token),
        json={"is_active": False},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    stale_session = await client.get("/api/v1/admin/bootstrap", headers=auth(manager_token))
    assert stale_session.status_code == 403

    audit_rows = list(
        (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant.id,
                    AuditLog.user_id == owner.id,
                    AuditLog.action == "MEMBERSHIP_UPDATED",
                    AuditLog.resource_id == str(manager_membership.id),
                )
            )
        ).scalars().all()
    )
    assert len(audit_rows) == 2


async def test_membership_mutation_rejects_foreign_tenant_and_invalid_permissions(
    member_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = member_admin_env["client"]
    session: AsyncSession = member_admin_env["session"]
    tenant = Tenant(name="Member Scope", slug=f"member-scope-{uuid.uuid4().hex[:6]}", is_active=True)
    other = Tenant(name="Foreign Scope", slug=f"foreign-scope-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    await create_identity(session, tenant, Role.OWNER)
    _, _, admin_token = await create_identity(session, tenant, Role.ADMIN)
    _, local_membership, _ = await create_identity(session, tenant, Role.STAFF)
    _, foreign_membership, _ = await create_identity(session, other, Role.STAFF)
    await session.commit()

    foreign = await client.patch(
        f"/api/v1/admin/members/{foreign_membership.id}",
        headers=auth(admin_token),
        json={"role": "CUSTOMER"},
    )
    assert foreign.status_code == 404

    invalid = await client.patch(
        f"/api/v1/admin/members/{local_membership.id}",
        headers=auth(admin_token),
        json={"permissions": ["orders:read", "bad permission value"]},
    )
    assert invalid.status_code == 422
