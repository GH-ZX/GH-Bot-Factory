"""Single-use browser login grants issued to authenticated Telegram staff."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import settings
from packages.telegram.models import Bot
from packages.tenants.models import Membership, Role, Tenant, User

LOGIN_TTL_SECONDS = 300
STAFF_ROLES = {Role.STAFF, Role.MANAGER, Role.ADMIN, Role.OWNER}


class AdminLoginError(ValueError):
    """The grant is expired, consumed, or no longer authorized."""


class AdminLoginService:
    def __init__(self, redis_client=None) -> None:
        self.redis_client = redis_client

    @staticmethod
    def _key(code: str) -> str:
        return "ghbf:admin-login:" + hashlib.sha256(code.encode()).hexdigest()

    async def _redis_call(self, operation: str, *args, **kwargs):
        client = self.redis_client or Redis.from_url(
            settings.redis_url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3
        )
        try:
            return await getattr(client, operation)(*args, **kwargs)
        finally:
            if self.redis_client is None:
                await client.aclose()

    @staticmethod
    async def _identity(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, bot_id: uuid.UUID):
        row = (
            await session.execute(
                select(User, Membership, Bot)
                .join(Membership, Membership.user_id == User.id)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .join(Bot, Bot.tenant_id == Tenant.id)
                .where(
                    Tenant.id == tenant_id, Tenant.is_active.is_(True),
                    User.id == user_id, User.is_active.is_(True),
                    Membership.tenant_id == tenant_id, Membership.is_active.is_(True),
                    Membership.role.in_(STAFF_ROLES),
                    Bot.id == bot_id, Bot.tenant_id == tenant_id,
                    Bot.is_enabled.is_(True), Bot.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        if row is None:
            raise AdminLoginError("Code is invalid or expired. Send /admin privately to your bot for a new code.")
        return row

    async def issue(self, session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, bot_id: uuid.UUID) -> str:
        user, _membership, _bot = await self._identity(session, tenant_id, user_id, bot_id)
        code = secrets.token_urlsafe(24)
        payload = json.dumps({
            "tenant_id": str(tenant_id), "user_id": str(user_id), "bot_id": str(bot_id),
            "token_version": user.token_version,
        })
        await self._redis_call("set", self._key(code), payload, ex=LOGIN_TTL_SECONDS)
        return code

    async def consume(self, session: AsyncSession, code: str):
        error = "Code is invalid or expired. Send /admin privately to your bot for a new code."
        if not re.fullmatch(r"[A-Za-z0-9_-]{32}", code):
            raise AdminLoginError(error)
        payload = await self._redis_call("getdel", self._key(code))
        if payload is None:
            raise AdminLoginError(error)
        try:
            data = json.loads(payload)
            tenant_id, user_id, bot_id = (uuid.UUID(data[key]) for key in ("tenant_id", "user_id", "bot_id"))
            version = data["token_version"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AdminLoginError(error) from exc
        user, membership, bot = await self._identity(session, tenant_id, user_id, bot_id)
        if user.token_version != version:
            raise AdminLoginError(error)
        return user, membership, bot
