from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from packages.core.config import settings


@dataclass(frozen=True, slots=True)
class PlatformOperator:
    actor: str = "LOCAL_PLATFORM_TOKEN"


def require_platform_operator(
    token: str | None = Header(default=None, alias="X-GHBF-Platform-Token"),
) -> PlatformOperator:
    """Authenticate the installation-level control plane independently of tenant RBAC."""

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
