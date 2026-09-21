from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service, get_current_principal
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.core.system_models import SystemInstallState


@dataclass(frozen=True, slots=True)
class PlatformOperator:
    actor: str = "LOCAL_PLATFORM_TOKEN"


async def require_platform_operator(
    token: str | None = Header(default=None, alias="X-GHBF-Platform-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    session: AsyncSession = Depends(get_db_session),
) -> PlatformOperator:
    """Authenticate the installation-level control plane independently of tenant RBAC."""

    if authorization and token is None:
        token_service = get_auth_token_service()
        principal = await get_current_principal(authorization, session, token_service)
        payload = token_service.verify_access_token(authorization.removeprefix("Bearer ").strip())
        state = await session.get(SystemInstallState, 1)
        if (state is None or not state.is_initialized
                or state.operator_user_id != principal.user_id
                or state.tenant_id != principal.tenant_id
                or payload.get("login_method") != "password"):
            raise HTTPException(403, "Factory owner access is required.")
        return PlatformOperator(actor=f"WEB_OPERATOR:{principal.user_id}")

    configured = (settings.platform_admin_token or "").strip()
    if len(configured.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform control plane is not configured.",
        )
    submitted = (token or "").strip()
    if not submitted or not hmac.compare_digest(
        submitted.encode("utf-8"), configured.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid platform operator credential.",
        )
    return PlatformOperator()
