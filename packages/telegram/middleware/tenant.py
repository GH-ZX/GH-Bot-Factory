import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from packages.core.database import async_session_factory
from packages.telegram.context import TenantContext
from packages.telegram.models import Bot, TenantTelegramUser
from packages.tenants.models import Membership, Role, User

logger = logging.getLogger("telegram.tenant")


class TenantResolutionMiddleware(BaseMiddleware):
    """Resolves Bot identity, Tenant context, and provisions/maps isolated Telegram users per tenant."""

    def __init__(
        self,
        bot_id: uuid.UUID,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.bot_id = bot_id
        self.session_factory = session_factory or async_session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        correlation_id = data.get("correlation_id", str(uuid.uuid4()))

        # Extract Telegram User from event if present
        from_user = getattr(event, "from_user", None)
        if from_user is None and hasattr(event, "message") and getattr(event, "message", None):
            from_user = getattr(event.message, "from_user", None)

        async with self.session_factory() as session:
            data["db_session"] = session

            # 1. Resolve Bot and Tenant
            stmt = (
                select(Bot)
                .where(Bot.id == self.bot_id)
                .options(selectinload(Bot.tenant))
            )
            result = await session.execute(stmt)
            bot_record = result.scalar_one_or_none()

            if bot_record is None:
                logger.error(
                    "Bot record not found for bot_id=%s [correlation_id=%s]",
                    self.bot_id,
                    correlation_id,
                )
                return None

            if not bot_record.is_enabled:
                logger.warning(
                    "Received update for disabled bot=%s (id=%s) [correlation_id=%s]",
                    bot_record.username,
                    self.bot_id,
                    correlation_id,
                )
                if isinstance(event, Message):
                    await event.answer("⚠️ This bot is currently disabled by administrator.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⚠️ This bot is currently disabled.", show_alert=True)
                return None

            tenant = bot_record.tenant
            user_id: uuid.UUID | None = None
            telegram_user_id = from_user.id if from_user else 0

            # 2. Resolve or provision tenant-scoped Telegram user binding
            if from_user is not None:
                stmt_user = select(TenantTelegramUser).where(
                    TenantTelegramUser.tenant_id == tenant.id,
                    TenantTelegramUser.bot_id == bot_record.id,
                    TenantTelegramUser.telegram_user_id == from_user.id,
                )
                res_user = await session.execute(stmt_user)
                user_binding = res_user.scalar_one_or_none()

                if user_binding is None:
                    sibling_stmt = (
                        select(TenantTelegramUser)
                        .where(
                            TenantTelegramUser.tenant_id == tenant.id,
                            TenantTelegramUser.telegram_user_id == from_user.id,
                        )
                        .limit(1)
                    )
                    sibling = (await session.execute(sibling_stmt)).scalar_one_or_none()
                    user = await session.get(User, sibling.user_id) if sibling is not None else None
                    if user is None:
                        user_stmt = select(User).where(User.telegram_id == from_user.id)
                        user = (await session.execute(user_stmt)).scalar_one_or_none()
                    if user is None:
                        user = User(
                            telegram_id=from_user.id,
                            username=from_user.username,
                            first_name=from_user.first_name,
                            last_name=from_user.last_name,
                            is_active=True,
                        )
                        session.add(user)
                        await session.flush()
                    elif user.telegram_id is None:
                        user.telegram_id = from_user.id

                    membership_stmt = select(Membership).where(
                        Membership.tenant_id == tenant.id,
                        Membership.user_id == user.id,
                    )
                    membership = (await session.execute(membership_stmt)).scalar_one_or_none()
                    if membership is None:
                        membership = Membership(
                            tenant_id=tenant.id,
                            user_id=user.id,
                            role=Role.CUSTOMER,
                            permissions=["catalog:read", "orders:create", "wallet:use"],
                        )
                        session.add(membership)

                    user_binding = TenantTelegramUser(
                        tenant_id=tenant.id,
                        bot_id=bot_record.id,
                        telegram_user_id=from_user.id,
                        user_id=user.id,
                        telegram_username=from_user.username,
                        first_name=from_user.first_name,
                        last_name=from_user.last_name,
                        language_code=from_user.language_code,
                    )
                    session.add(user_binding)
                    await session.commit()
                else:
                    if user_binding.is_blocked:
                        logger.warning(
                            "Blocked user %s attempted update on bot %s",
                            from_user.id,
                            bot_record.id,
                        )
                        return None

                user_id = user_binding.user_id

            # 3. Assemble explicit TenantContext
            tenant_context = TenantContext(
                bot_id=bot_record.id,
                tenant_id=tenant.id,
                telegram_bot_id=bot_record.telegram_bot_id,
                telegram_user_id=telegram_user_id,
                user_id=user_id,
                correlation_id=correlation_id,
                tenant_name=tenant.name,
                display_name=bot_record.display_name,
                bot_username=bot_record.username,
                language_code=from_user.language_code if from_user else "en",
                config=bot_record.config or {},
                tenant_settings=tenant.settings or {},
            )
            data["tenant_context"] = tenant_context

            # Continue through handler pipeline
            return await handler(event, data)
