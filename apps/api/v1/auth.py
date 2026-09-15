import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.database import get_db_session
from packages.core.exceptions import TenantAccessViolationError
from packages.payments.exceptions import (
    MiniAppAuthError,
    MiniAppDataTamperedError,
    MiniAppExpiredError,
    MiniAppSignatureInvalidError,
)
from packages.telegram.miniapp import TelegramMiniAppAuthService
from packages.telegram.secrets import EnvSecretStorage, SecretStorage

router = APIRouter(prefix="/auth", tags=["auth"])


class TelegramMiniAppAuthRequest(BaseModel):
    init_data: str
    bot_id: uuid.UUID
    expected_tenant_id: uuid.UUID | None = None


class TelegramMiniAppAuthResponse(BaseModel):
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    telegram_user: dict[str, Any]


def get_secret_storage() -> SecretStorage:
    return EnvSecretStorage()


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
) -> TelegramMiniAppAuthResponse:
    """Authenticates Telegram WebApp initData, cryptographically verifies HMAC signature, and resolves Tenant context.

    Prevents client-side tenant spoofing.
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
        return TelegramMiniAppAuthResponse(
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
