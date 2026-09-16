from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.system_models import SystemInstallState
from packages.factory.provisioning import (
    AiogramTelegramIdentityVerifier,
    TelegramIdentityVerifier,
    normalize_expected_username,
)
from packages.factory.templates import build_template_config
from packages.telegram.models import Bot
from packages.telegram.secrets import SecretStorage
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,98}[a-z0-9]$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


class SetupError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class SetupResult:
    tenant_id: uuid.UUID
    owner_user_id: uuid.UUID
    bot_id: uuid.UUID
    telegram_bot_id: int
    telegram_username: str | None


def normalize_public_base_url(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise SetupError("PUBLIC_URL_INVALID", "Public URL must be a valid HTTPS origin.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SetupError(
            "PUBLIC_URL_INVALID",
            "Public URL must not contain credentials, query parameters, or fragments.",
        )
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", "")).rstrip("/")


def _public_urls(base: str | None) -> dict[str, str]:
    if not base:
        return {}
    return {
        "miniapp_public_url": f"{base}/miniapp/",
        "admin_public_url": f"{base}/admin/",
    }


async def is_initialized(session: AsyncSession) -> bool:
    state = await session.get(SystemInstallState, 1)
    if state is not None and state.is_initialized:
        return True
    tenant_count = await session.scalar(select(func.count(Tenant.id)))
    return bool(tenant_count)


async def install_first_tenant(
    *,
    session: AsyncSession,
    secret_storage: SecretStorage,
    bot_token: str,
    tenant_slug: str,
    tenant_name: str,
    owner_telegram_id: int,
    owner_username: str | None,
    bot_display_name: str,
    expected_bot_username: str | None,
    template_key: str,
    public_base_url: str | None,
    verifier: TelegramIdentityVerifier | None = None,
) -> SetupResult:
    slug = tenant_slug.strip().lower()
    if not _SLUG_RE.fullmatch(slug):
        raise SetupError("TENANT_SLUG_INVALID", "Tenant slug must use lowercase letters, numbers, and hyphens.")
    name = tenant_name.strip()
    if not name or len(name) > 255:
        raise SetupError("TENANT_NAME_INVALID", "Tenant name is required and must be at most 255 characters.")
    if owner_telegram_id <= 0:
        raise SetupError("OWNER_ID_INVALID", "Owner Telegram user ID must be a positive integer.")
    normalized_owner_username = owner_username.strip().lstrip("@") if owner_username else None
    if normalized_owner_username and not _USERNAME_RE.fullmatch(normalized_owner_username):
        raise SetupError("OWNER_USERNAME_INVALID", "Owner Telegram username is invalid.")
    display_name = bot_display_name.strip()
    if not display_name or len(display_name) > 100:
        raise SetupError("BOT_DISPLAY_NAME_INVALID", "Bot display name is required and must be at most 100 characters.")
    if not bot_token.strip():
        raise SetupError("BOT_TOKEN_REQUIRED", "Telegram Bot token is required.")

    public_base = normalize_public_base_url(public_base_url)
    identity_verifier = verifier or AiogramTelegramIdentityVerifier()
    try:
        identity = await identity_verifier.verify(bot_token.strip())
    except Exception as exc:
        code = getattr(exc, "code", "TELEGRAM_VERIFICATION_FAILED")
        raise SetupError(code, "Telegram could not verify this bot token.", status_code=422) from exc

    expected = normalize_expected_username(expected_bot_username)
    actual = normalize_expected_username(identity.username)
    if expected and expected != actual:
        raise SetupError(
            "BOT_USERNAME_MISMATCH",
            f"Verified bot is @{actual or 'unknown'}, not @{expected}.",
            status_code=422,
        )

    config = build_template_config(template_key=template_key)
    secret_ref = f"GHBF_VAULT_TELEGRAM_{identity.telegram_bot_id}"
    secret_written = False

    try:
        state = (
            await session.execute(
                select(SystemInstallState).where(SystemInstallState.id == 1).with_for_update()
            )
        ).scalar_one_or_none()
        if state is None:
            state = SystemInstallState(id=1, is_initialized=False)
            session.add(state)
            await session.flush()

        tenant_count = await session.scalar(select(func.count(Tenant.id)))
        if state.is_initialized or tenant_count:
            raise SetupError("ALREADY_INITIALIZED", "This installation has already been initialized.", status_code=409)

        await secret_storage.set_secret(secret_ref, bot_token.strip())
        secret_written = True

        tenant = Tenant(
            name=name,
            slug=slug,
            is_active=True,
            settings=_public_urls(public_base),
        )
        session.add(tenant)
        await session.flush()

        owner = User(
            telegram_id=owner_telegram_id,
            username=normalized_owner_username,
            is_active=True,
        )
        session.add(owner)
        await session.flush()

        session.add(
            Membership(
                tenant_id=tenant.id,
                user_id=owner.id,
                role=Role.OWNER,
                permissions=[],
                is_active=True,
            )
        )

        bot = Bot(
            tenant_id=tenant.id,
            telegram_bot_id=identity.telegram_bot_id,
            username=identity.username,
            display_name=display_name,
            token_secret_ref=secret_ref,
            credential_status="VERIFIED",
            credential_verified_at=datetime.now(UTC),
            is_enabled=True,
            config=config,
        )
        session.add(bot)
        await session.flush()

        session.add(
            AuditLog(
                tenant_id=tenant.id,
                user_id=owner.id,
                action="FIRST_RUN_WEB_SETUP_COMPLETED",
                resource_type="BOT",
                resource_id=str(bot.id),
                details={
                    "telegram_bot_id": identity.telegram_bot_id,
                    "username": identity.username,
                    "template_key": template_key,
                    "public_url_configured": bool(public_base),
                },
            )
        )
        state.is_initialized = True
        state.initialized_at = datetime.now(UTC)
        state.tenant_id = tenant.id
        await session.commit()
        return SetupResult(
            tenant_id=tenant.id,
            owner_user_id=owner.id,
            bot_id=bot.id,
            telegram_bot_id=identity.telegram_bot_id,
            telegram_username=identity.username,
        )
    except Exception:
        await session.rollback()
        if secret_written:
            try:
                await secret_storage.delete_secret(secret_ref)
            except Exception:  # noqa: BLE001 - preserve original setup failure during vault cleanup
                logging.getLogger(__name__).warning("Setup rollback could not remove the staged secret.")
        raise
