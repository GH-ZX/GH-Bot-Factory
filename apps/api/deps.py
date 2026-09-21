import uuid

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth import (
    AuthenticatedPrincipal,
    AuthSource,
    AuthTokenService,
    TokenExpiredError,
    TokenInvalidSignatureError,
    TokenMalformedError,
)
from packages.core.database import get_db_session
from packages.tenants.models import Membership, Role, Tenant, User


def get_auth_token_service() -> AuthTokenService:
    return AuthTokenService()


async def get_current_principal(
    authorization: str | None = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_db_session),
    token_service: AuthTokenService = Depends(get_auth_token_service),
) -> AuthenticatedPrincipal:
    """Authenticates API requests via Bearer access token.

    Rejects unauthenticated callers, invalid tokens, expired tokens, revoked tokens,
    deactivated users, and inactive tenants. Produces an authoritative AuthenticatedPrincipal.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization scheme. Expected 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_token = authorization.removeprefix("Bearer ").strip()
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty access token provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = token_service.verify_access_token(raw_token)
    except TokenExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except TokenInvalidSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token signature is invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except TokenMalformedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = uuid.UUID(payload["sub"])
    tenant_id = uuid.UUID(payload["tenant_id"])

    # 1. Verify User exists and is active
    user = await session.get(User, user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated or not found.",
        )

    # 2. Verify token version (session revocation check)
    token_version = payload.get("token_version", 1)
    if hasattr(user, "token_version") and token_version != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has been revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Verify Tenant exists and is active
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active or tenant.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant is inactive or deleted.",
        )

    # 4. Verify tenant membership
    membership_stmt = select(Membership).where(
        Membership.tenant_id == tenant_id,
        Membership.user_id == user_id,
    )
    membership = (await session.execute(membership_stmt)).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of the specified tenant.",
        )
    if not membership.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User membership in the specified tenant is inactive.",
        )

    roles = frozenset([membership.role])
    permissions = frozenset(membership.permissions or [])
    source = AuthSource(payload.get("source", AuthSource.SESSION.value))
    bot_id_raw = payload.get("bot_id")
    try:
        bot_id = uuid.UUID(str(bot_id_raw)) if bot_id_raw else None
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token contains an invalid bot_id claim.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return AuthenticatedPrincipal(
        user_id=user.id,
        tenant_id=tenant.id,
        source=source,
        roles=roles,
        permissions=permissions,
        token_version=getattr(user, "token_version", 1),
        bot_id=bot_id,
    )


def require_roles(*allowed_roles: Role):
    """Enforces that the authenticated principal possesses at least one of the allowed roles."""

    async def role_checker(
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
    ) -> AuthenticatedPrincipal:
        if not any(r in principal.roles for r in allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires one of roles: {[r.value for r in allowed_roles]}",
            )
        return principal

    return role_checker


async def require_staff_or_above(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> AuthenticatedPrincipal:
    """Enforces that the authenticated principal is STAFF, MANAGER, ADMIN, or OWNER."""
    if not principal.is_staff_or_above():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation requires privileged staff, admin, or owner role.",
        )
    return principal


async def require_manager_or_above(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> AuthenticatedPrincipal:
    """Enforces catalog/configuration mutation privileges for MANAGER, ADMIN, or OWNER."""
    if not principal.roles.intersection({Role.MANAGER, Role.ADMIN, Role.OWNER}):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation requires manager, admin, or owner role.",
        )
    return principal


async def require_admin_or_owner(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> AuthenticatedPrincipal:
    """Enforces high-risk operational mutation privileges for ADMIN or OWNER."""
    if not principal.roles.intersection({Role.ADMIN, Role.OWNER}):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation requires admin or owner role.",
        )
    return principal
