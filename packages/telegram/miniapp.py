import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.exceptions import (
    TenantAccessViolationError,
)
from packages.payments.exceptions import (
    MiniAppAuthError,
    MiniAppDataTamperedError,
    MiniAppExpiredError,
    MiniAppSignatureInvalidError,
)
from packages.telegram.models import Bot, TenantTelegramUser
from packages.telegram.secrets import SecretStorage
from packages.tenants.models import Membership, Role, Tenant, User

logger = logging.getLogger("telegram.miniapp")


class TelegramMiniAppAuthService:
    """Server-side authentication and tenant-scoping service for Telegram Mini Apps."""

    @staticmethod
    def validate_init_data(
        raw_init_data: str,
        bot_token: str,
        max_age_seconds: int = 86400,
        current_time: int | None = None,
    ) -> dict[str, Any]:
        """Validates the cryptographic HMAC-SHA256 signature and freshness of Telegram WebApp initData.

        Telegram validation algorithm:
        1. Parse the query string into key-value pairs.
        2. Remove the 'hash' parameter.
        3. Sort the remaining parameters alphabetically by key.
        4. Join them with newlines: 'k1=v1\nk2=v2...'.
        5. Secret key = HMAC_SHA256("WebAppData", bot_token).
        6. Computed hash = HMAC_SHA256(secret_key, data_check_string).
        7. Verify computed hash matches received hash.
        8. Validate auth_date freshness.
        """
        if not raw_init_data:
            raise MiniAppSignatureInvalidError("initData cannot be empty.")

        try:
            parsed_params = dict(urllib.parse.parse_qsl(raw_init_data, keep_blank_values=True))
        except Exception as exc:
            raise MiniAppDataTamperedError("Failed to parse initData query string.") from exc

        received_hash = parsed_params.pop("hash", None)
        if not received_hash:
            raise MiniAppSignatureInvalidError("Missing 'hash' parameter in initData.")

        # Sort alphabetically and format as 'key=value' separated by newline
        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(parsed_params.items())
        )

        # Telegram secret key formula
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            raise MiniAppSignatureInvalidError("Cryptographic verification failed: hash mismatch.")

        # Validate freshness via auth_date
        auth_date_str = parsed_params.get("auth_date")
        if not auth_date_str:
            raise MiniAppDataTamperedError("Missing 'auth_date' parameter in initData.")

        try:
            auth_date = int(auth_date_str)
        except ValueError as exc:
            raise MiniAppDataTamperedError("Invalid non-integer 'auth_date' parameter.") from exc

        now = current_time if current_time is not None else int(time.time())
        if now - auth_date > max_age_seconds:
            raise MiniAppExpiredError(
                f"Telegram initData has expired (auth_date={auth_date}, current={now}, max_age={max_age_seconds}s)."
            )
        if auth_date > now + 300:
            raise MiniAppDataTamperedError("Telegram initData auth_date cannot be in the future.")

        # Parse user JSON if present
        result_data: dict[str, Any] = dict(parsed_params)
        user_raw = parsed_params.get("user")
        if user_raw:
            try:
                user_json = json.loads(user_raw)
                if not isinstance(user_json, dict) or "id" not in user_json:
                    raise MiniAppDataTamperedError("Malformed user payload in initData.")
                result_data["user"] = user_json
            except json.JSONDecodeError as exc:
                raise MiniAppDataTamperedError("Corrupted user JSON in initData.") from exc

        return result_data

    @classmethod
    async def authenticate_and_resolve_tenant(
        cls,
        session: AsyncSession,
        raw_init_data: str,
        bot_id: uuid.UUID,
        secret_storage: SecretStorage,
        expected_tenant_id: uuid.UUID | None = None,
        max_age_seconds: int = 86400,
    ) -> tuple[Tenant, User, dict[str, Any]]:
        """Validates initData, resolves the owning Tenant from the authoritative Bot record, and provisions the customer.

        Enforces that the client NEVER supplies the tenant_id authoritatively.
        """
        # Load Bot record
        bot = await session.get(Bot, bot_id)
        if bot is None or not bot.is_enabled:
            raise MiniAppAuthError(f"Telegram Bot {bot_id} not found or disabled.")

        tenant_id = bot.tenant_id

        # Enforce tenant isolation if context specified
        if expected_tenant_id is not None and expected_tenant_id != tenant_id:
            raise TenantAccessViolationError(
                f"Bot {bot_id} belongs to tenant {tenant_id}, but request requested tenant {expected_tenant_id}."
            )

        # Retrieve bot token securely
        bot_token = await secret_storage.get_secret(bot.token_secret_ref)

        # Validate signature & payload
        validated_data = cls.validate_init_data(
            raw_init_data=raw_init_data,
            bot_token=bot_token,
            max_age_seconds=max_age_seconds,
        )

        user_payload = validated_data.get("user")
        if not user_payload or "id" not in user_payload:
            raise MiniAppDataTamperedError("Missing user identity in validated initData.")

        telegram_user_id = user_payload["id"]

        # Look up or provision TenantTelegramUser and User
        binding_stmt = select(TenantTelegramUser).where(
            TenantTelegramUser.tenant_id == tenant_id,
            TenantTelegramUser.bot_id == bot_id,
            TenantTelegramUser.telegram_user_id == telegram_user_id,
        )
        binding = (await session.execute(binding_stmt)).scalar_one_or_none()

        if binding is not None:
            user = await session.get(User, binding.user_id)
            if user is None:
                raise MiniAppAuthError("Linked user record not found.")
        else:
            # Check if User exists by telegram_id
            user_stmt = select(User).where(User.telegram_id == telegram_user_id)
            user = (await session.execute(user_stmt)).scalar_one_or_none()
            if user is None:
                user = User(
                    telegram_id=telegram_user_id,
                    username=user_payload.get("username"),
                    first_name=user_payload.get("first_name"),
                    last_name=user_payload.get("last_name"),
                    is_active=True,
                )
                session.add(user)
                await session.flush()

            # Create binding
            binding = TenantTelegramUser(
                tenant_id=tenant_id,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                user_id=user.id,
                telegram_username=user_payload.get("username"),
                first_name=user_payload.get("first_name"),
                last_name=user_payload.get("last_name"),
                language_code=user_payload.get("language_code"),
            )
            session.add(binding)

            # Ensure customer membership in tenant
            membership_stmt = select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user.id,
            )
            membership = (await session.execute(membership_stmt)).scalar_one_or_none()
            if membership is None:
                membership = Membership(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    role=Role.CUSTOMER,
                    permissions=[],
                )
                session.add(membership)

            await session.flush()

        if not user.is_active:
            raise TenantAccessViolationError(f"User {user.id} account is deactivated.")

        tenant = await session.get(Tenant, tenant_id)
        if tenant is None or not tenant.is_active:
            raise TenantAccessViolationError(f"Tenant {tenant_id} is inactive or deleted.")

        return tenant, user, user_payload
