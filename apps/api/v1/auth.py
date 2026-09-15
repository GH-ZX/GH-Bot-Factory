import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.core.exceptions import TenantAccessViolationError
from packages.payments.exceptions import (
    MiniAppAuthError,
    MiniAppDataTamperedError,
    MiniAppExpiredError,
    MiniAppSignatureInvalidError,
)
from packages.telegram.miniapp import TelegramMiniAppAuthService
from packages.telegram.secrets import SecretStorage, get_default_secret_storage
from packages.tenants.models import Membership, Role

router = APIRouter(prefix="/auth", tags=["auth"])


class TelegramMiniAppAuthRequest(BaseModel):
    init_data: str
    bot_id: uuid.UUID
    expected_tenant_id: uuid.UUID | None = None


class TelegramMiniAppAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    telegram_user: dict[str, Any]


def get_secret_storage() -> SecretStorage:
    return get_default_secret_storage()


@router.post(
    "/telegram-miniapp",
    response_model=TelegramMiniAppAuthResponse,
    status_code=status.HTTP_200_OK,
)
async def authenticate_telegram_miniapp(
    req: TelegramMiniAppAuthRequest,
    x_tenant_id: uuid.UUID | None = Header(None, alias="X-Tenant-ID"),
    session: AsyncSession = Depends(get_db_session),
    secret_storage: SecretStorage = Depends(get_secret_storage),
    token_service: AuthTokenService = Depends(get_auth_token_service),
) -> TelegramMiniAppAuthResponse:
    """Authenticates Telegram WebApp initData, cryptographically verifies HMAC signature,

    resolves authoritative Tenant/User context, and issues a short-lived Bearer access token.
    """
    try:
        expected_tenant_id = req.expected_tenant_id or x_tenant_id
        tenant, user, user_payload = (
            await TelegramMiniAppAuthService.authenticate_and_resolve_tenant(
                session=session,
                raw_init_data=req.init_data,
                bot_id=req.bot_id,
                secret_storage=secret_storage,
                expected_tenant_id=expected_tenant_id,
            )
        )

        # Resolve role from Membership
        membership_stmt = select(Membership).where(
            Membership.tenant_id == tenant.id,
            Membership.user_id == user.id,
        )
        membership = (await session.execute(membership_stmt)).scalar_one_or_none()
        role = membership.role if membership is not None else Role.CUSTOMER

        # Issue signed JWT access token
        token_version = getattr(user, "token_version", 1)
        access_token = token_service.issue_access_token(
            user_id=user.id,
            tenant_id=tenant.id,
            roles=[role],
            source=AuthSource.TELEGRAM_MINIAPP,
            token_version=token_version,
            expires_in_seconds=3600,
            extra_claims={"bot_id": str(req.bot_id)},
        )

        return TelegramMiniAppAuthResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=3600,
            tenant_id=tenant.id,
            user_id=user.id,
            telegram_user=user_payload,
        )
    except (MiniAppSignatureInvalidError, MiniAppExpiredError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except MiniAppDataTamperedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TenantAccessViolationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except MiniAppAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
