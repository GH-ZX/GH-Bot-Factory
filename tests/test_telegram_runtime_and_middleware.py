from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, Message, Update
from aiogram.types import User as AiogramUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.exceptions import InsufficientFundsError, InvalidStateTransitionError
from packages.telegram.context import TenantContext
from packages.telegram.middleware.correlation import CorrelationMiddleware
from packages.telegram.middleware.error import ErrorHandlingMiddleware
from packages.telegram.middleware.tenant import TenantResolutionMiddleware
from packages.telegram.models import Bot
from packages.telegram.runtime import BotRuntimeManager
from packages.telegram.secrets import EnvSecretStorage, mask_token
from packages.tenants.models import Tenant


@pytest.mark.asyncio
async def test_secret_storage_and_token_masking():
    storage = EnvSecretStorage({"STORE_TOKEN": "123456789:ABCdefGHIjklmnOPQ"})
    token = await storage.get_secret("STORE_TOKEN")
    assert token == "123456789:ABCdefGHIjklmnOPQ"

    masked = mask_token(token)
    assert masked == "123456789:***...nOPQ"
    assert "ABCdefGHI" not in masked


@pytest.mark.asyncio
async def test_correlation_middleware():
    middleware = CorrelationMiddleware()
    event = MagicMock(spec=Update)
    data = {}

    async def sample_handler(e, d):
        return d.get("correlation_id")

    res = await middleware(sample_handler, event, data)
    assert res is not None
    assert len(res) == 36  # UUID4 string length


@pytest.mark.asyncio
async def test_tenant_resolution_middleware_and_user_provisioning(
    db_session: AsyncSession,
):
    tenant = Tenant(name="Middleware Test Store", slug="mid-store")
    db_session.add(tenant)
    await db_session.flush()

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=888999111,
        username="mid_bot",
        display_name="Middleware Bot",
        token_secret_ref="SECRET_REF",
        is_enabled=True,
    )
    db_session.add(bot)
    await db_session.commit()

    session_factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    middleware = TenantResolutionMiddleware(
        bot_id=bot.id,
        session_factory=session_factory,
    )

    # Construct mock Aiogram Message
    aiogram_user = AiogramUser(
        id=9991122,
        is_bot=False,
        first_name="CustomerJohn",
        username="c_john",
    )
    chat = Chat(id=9991122, type="private")
    message = MagicMock(spec=Message)
    message.from_user = aiogram_user
    message.chat = chat

    data = {"correlation_id": "test-cid-123"}

    async def dummy_handler(evt, d):
        ctx: TenantContext = d["tenant_context"]
        return ctx

    ctx = await middleware(dummy_handler, message, data)
    assert ctx is not None
    assert ctx.tenant_id == tenant.id
    assert ctx.bot_id == bot.id
    assert ctx.telegram_user_id == 9991122
    assert ctx.user_id is not None
    assert ctx.correlation_id == "test-cid-123"


@pytest.mark.asyncio
async def test_tenant_resolution_disabled_bot_safely_rejected(
    db_session: AsyncSession,
):
    tenant = Tenant(name="Disabled Store", slug="dis-store")
    db_session.add(tenant)
    await db_session.flush()

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=123000,
        username="disabled_bot",
        display_name="Disabled Bot",
        token_secret_ref="SEC_DIS",
        is_enabled=False,
    )
    db_session.add(bot)
    await db_session.commit()

    session_factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    middleware = TenantResolutionMiddleware(
        bot_id=bot.id,
        session_factory=session_factory,
    )

    message = MagicMock(spec=Message)
    message.from_user = AiogramUser(id=1, is_bot=False, first_name="User")
    message.answer = AsyncMock()

    data = {"correlation_id": "cid-disabled"}
    dummy_handler = AsyncMock()

    result = await middleware(dummy_handler, message, data)
    assert result is None
    dummy_handler.assert_not_called()
    message.answer.assert_called_once()


@pytest.mark.asyncio
async def test_error_handling_middleware_catches_domain_exceptions():
    middleware = ErrorHandlingMiddleware()
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    data = {"correlation_id": "test-err-cid"}

    # 1. InsufficientFundsError
    async def handler_funds(e, d):
        raise InsufficientFundsError("Not enough cash")

    await middleware(handler_funds, message, data)
    message.answer.assert_called_with("⚠️ Insufficient wallet balance to complete this transaction.", parse_mode="HTML")

    # 2. InvalidStateTransitionError
    message.answer.reset_mock()

    async def handler_state(e, d):
        raise InvalidStateTransitionError("Bad transition")

    await middleware(handler_state, message, data)
    message.answer.assert_called_with("⚠️ This action is not allowed for the order's current status.", parse_mode="HTML")


@pytest.mark.asyncio
async def test_runtime_manager_health_status(db_session: AsyncSession):
    secret_store = EnvSecretStorage({"MOCK_TOKEN": "123:ABC"})
    manager = BotRuntimeManager(
        secret_storage=secret_store,
        session_factory=async_sessionmaker(bind=db_session.bind, class_=AsyncSession),
    )

    health = manager.get_health_status()
    assert health["status"] == "idle"
    assert health["active_bots_count"] == 0
    assert "running_bots" in health
    assert "startup_failures" in health
