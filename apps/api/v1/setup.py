from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import settings
from packages.core.database import get_db_session
from packages.factory.templates import list_bot_templates
from packages.setup.service import SetupError, install_first_tenant, is_initialized
from packages.telegram.secrets import SecretStorage, get_default_secret_storage

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupStatusResponse(BaseModel):
    initialized: bool
    ready_for_setup: bool
    setup_code_required: bool = True
    local_secret_vault_enabled: bool
    templates: list[dict]


class FirstRunSetupRequest(BaseModel):
    setup_code: SecretStr
    tenant_slug: str = Field(min_length=3, max_length=100)
    tenant_name: str = Field(min_length=1, max_length=255)
    owner_telegram_id: int = Field(gt=0)
    owner_username: str | None = Field(default=None, max_length=100)
    bot_token: SecretStr
    expected_bot_username: str | None = Field(default=None, max_length=100)
    bot_display_name: str = Field(min_length=1, max_length=100)
    template_key: str = Field(default="general-commerce", max_length=50)
    public_base_url: str | None = Field(default=None, max_length=500)


class FirstRunSetupResponse(BaseModel):
    status: str = "configured"
    tenant_id: str
    owner_user_id: str
    bot_id: str
    telegram_bot_id: int
    telegram_username: str | None
    runtime_reconciliation_seconds: float


def _require_setup_code(submitted: str) -> None:
    configured = settings.setup_code or ""
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SETUP_CODE is not configured. Use scripts/easy_start.py to initialize local configuration.",
        )
    if not hmac.compare_digest(submitted.encode("utf-8"), configured.encode("utf-8")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Setup code is invalid.")


def _secret_storage() -> SecretStorage:
    return get_default_secret_storage()


@router.get("/status", response_model=SetupStatusResponse)
async def setup_status(session: AsyncSession = Depends(get_db_session)) -> SetupStatusResponse:
    initialized = await is_initialized(session)
    return SetupStatusResponse(
        initialized=initialized,
        ready_for_setup=(not initialized and bool(settings.setup_code) and settings.local_secret_vault_enabled),
        local_secret_vault_enabled=settings.local_secret_vault_enabled,
        templates=[template.public_payload() for template in list_bot_templates()],
    )


@router.post("/initialize", response_model=FirstRunSetupResponse)
async def initialize_installation(
    req: FirstRunSetupRequest,
    session: AsyncSession = Depends(get_db_session),
    secret_storage: SecretStorage = Depends(_secret_storage),
) -> FirstRunSetupResponse:
    _require_setup_code(req.setup_code.get_secret_value())
    if await is_initialized(session):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Installation is already initialized.")

    try:
        result = await install_first_tenant(
            session=session,
            secret_storage=secret_storage,
            bot_token=req.bot_token.get_secret_value(),
            tenant_slug=req.tenant_slug,
            tenant_name=req.tenant_name,
            owner_telegram_id=req.owner_telegram_id,
            owner_username=req.owner_username,
            bot_display_name=req.bot_display_name,
            expected_bot_username=req.expected_bot_username,
            template_key=req.template_key,
            public_base_url=req.public_base_url,
        )
    except SetupError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc

    return FirstRunSetupResponse(
        tenant_id=str(result.tenant_id),
        owner_user_id=str(result.owner_user_id),
        bot_id=str(result.bot_id),
        telegram_bot_id=result.telegram_bot_id,
        telegram_username=result.telegram_username,
        runtime_reconciliation_seconds=settings.bot_runtime_reconcile_seconds,
    )
