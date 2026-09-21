import uuid
from asyncio import to_thread
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service, require_staff_or_above
from packages.core.auth import (
    AuthenticatedPrincipal,
    AuthSource,
    AuthTokenService,
    hash_password,
    verify_password,
)
from packages.core.database import get_db_session
from packages.core.exceptions import TenantAccessViolationError
from packages.payments.exceptions import (
    MiniAppAuthError,
    MiniAppDataTamperedError,
    MiniAppExpiredError,
    MiniAppSignatureInvalidError,
)
from packages.telegram.admin_login import AdminLoginError, AdminLoginService
from packages.telegram.miniapp import TelegramMiniAppAuthService
from packages.telegram.models import Bot
from packages.telegram.secrets import SecretStorage, get_default_secret_storage
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

STAFF_ROLES = {Role.STAFF, Role.MANAGER, Role.ADMIN, Role.OWNER}

router = APIRouter(prefix="/auth", tags=["auth"])


class AdminCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: SecretStr = Field(min_length=32, max_length=32)


class AdminCodeResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600


def get_admin_login_service() -> AdminLoginService:
    return AdminLoginService()


@router.post("/admin-code", response_model=AdminCodeResponse)
async def authenticate_admin_code(
    req: AdminCodeRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    service: AdminLoginService = Depends(get_admin_login_service),
    token_service: AuthTokenService = Depends(get_auth_token_service),
) -> AdminCodeResponse:
    try:
        user, membership, bot = await service.consume(session, req.code.get_secret_value())
    except AdminLoginError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RedisError as exc:
        raise HTTPException(
            status_code=503, detail="Sign-in is temporarily unavailable. Try again shortly."
        ) from exc
    response.headers["Cache-Control"] = "no-store"
    return AdminCodeResponse(
        access_token=token_service.issue_access_token(
            user_id=user.id,
            tenant_id=membership.tenant_id,
            roles=[membership.role],
            source=AuthSource.SESSION,
            token_version=user.token_version,
            expires_in_seconds=3600,
            extra_claims={"bot_id": str(bot.id)} if bot else {},
        )
    )


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=1, max_length=255)
    tenant_slug: str | None = Field(default=None, max_length=100)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: str


_DUMMY_HASH = (
    "pbkdf2:sha256:100000$0123456789abcdef0123456789abcdef$"
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)


@router.post("/login", response_model=LoginResponse)
async def login_with_password(
    req: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    token_service: AuthTokenService = Depends(get_auth_token_service),
) -> LoginResponse:
    raw_username = req.username.strip().lstrip("@")
    if not raw_username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    candidates = (
        (
            await session.execute(
                select(User)
                .where(
                    func.lower(User.username) == raw_username.lower(),
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
                .limit(21)
            )
        )
        .scalars()
        .all()
    )

    # User identity lookup is an authentication boundary, never tenant authority.
    # Bound expensive verification and fail closed if identities are ambiguous.
    if len(candidates) > 20:
        await to_thread(verify_password, req.password.get_secret_value(), _DUMMY_HASH)
        raise HTTPException(401, "Invalid username or password.")
    verified_users = []
    for candidate in candidates:
        valid = await to_thread(
            verify_password,
            req.password.get_secret_value(),
            candidate.hashed_password or _DUMMY_HASH,
        )
        if candidate.hashed_password and valid:
            verified_users.append(candidate)
    if not verified_users:
        if not candidates:
            await to_thread(verify_password, req.password.get_secret_value(), _DUMMY_HASH)
        raise HTTPException(401, "Invalid username or password.")

    query = (
        select(Membership, Tenant)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(
            Membership.user_id.in_([u.id for u in verified_users]),
            Membership.is_active.is_(True),
            Membership.role.in_(STAFF_ROLES),
            Tenant.is_active.is_(True),
            Tenant.deleted_at.is_(None),
        )
    )
    if req.tenant_slug:
        query = query.where(Tenant.slug == req.tenant_slug.strip().lower())

    results = (await session.execute(query)).all()
    if not results:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is not enabled for this user account.",
        )

    if len(results) != 1:
        raise HTTPException(
            409,
            "Enter your store address to choose a single account. If this persists, contact your installation owner.",
        )
    membership, tenant = results[0]
    user = next(u for u in verified_users if u.id == membership.user_id)

    bot = (
        (
            await session.execute(
                select(Bot)
                .where(
                    Bot.tenant_id == tenant.id,
                    Bot.is_enabled.is_(True),
                    Bot.deleted_at.is_(None),
                )
                .order_by(Bot.created_at.desc())
            )
        )
        .scalars()
        .first()
    )

    response.headers["Cache-Control"] = "no-store"
    access_token = token_service.issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[membership.role],
        source=AuthSource.SESSION,
        token_version=user.token_version,
        expires_in_seconds=3600,
        extra_claims={"login_method": "password", **({"bot_id": str(bot.id)} if bot else {})},
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=3600,
        tenant_id=tenant.id,
        user_id=user.id,
        role=membership.role.value if isinstance(membership.role, Role) else str(membership.role),
    )


class PasswordUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr | None = Field(default=None, max_length=255)
    new_password: SecretStr = Field(min_length=12, max_length=255)


@router.get("/account")
async def account_details(
    response: Response,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
):
    user = await session.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            User.id == principal.user_id,
            Membership.tenant_id == principal.tenant_id,
            Membership.is_active.is_(True),
        )
    )
    tenant = await session.scalar(select(Tenant).where(Tenant.id == principal.tenant_id))
    response.headers["Cache-Control"] = "no-store"
    return {
        "username": user.username,
        "has_password": bool(user.hashed_password),
        "tenant_slug": tenant.slug,
    }


@router.put("/account/password", status_code=204)
async def update_own_password(
    req: PasswordUpdateRequest,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
):
    user = await session.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            User.id == principal.user_id,
            User.is_active.is_(True),
            Membership.tenant_id == principal.tenant_id,
            Membership.is_active.is_(True),
        )
        .with_for_update(of=User)
        .execution_options(populate_existing=True)
    )
    if not user or user.token_version != principal.token_version:
        raise HTTPException(401, "Sign in again before changing your password.")
    if not user.username:
        raise HTTPException(409, "A username is required for password sign-in.")
    if user.hashed_password:
        current = req.current_password.get_secret_value() if req.current_password else ""
        if not await to_thread(verify_password, current, user.hashed_password):
            raise HTTPException(400, "Current password is incorrect.")
    user.hashed_password = await to_thread(hash_password, req.new_password.get_secret_value())
    user.token_version += 1
    session.add(
        AuditLog(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="auth.password_changed",
            resource_type="user",
            resource_id=str(user.id),
            details={"sessions_revoked": True},
        )
    )
    await session.commit()
    response.headers["Cache-Control"] = "no-store"


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
        (
            tenant,
            user,
            user_payload,
        ) = await TelegramMiniAppAuthService.authenticate_and_resolve_tenant(
            session=session,
            raw_init_data=req.init_data,
            bot_id=req.bot_id,
            secret_storage=secret_storage,
            expected_tenant_id=expected_tenant_id,
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
