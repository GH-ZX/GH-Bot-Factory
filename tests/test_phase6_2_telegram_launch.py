import hashlib
import hmac
import json
import time
import urllib.parse
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.telegram.launch import build_admin_url, build_miniapp_url
from packages.telegram.miniapp import TelegramMiniAppAuthService
from packages.telegram.models import Bot, TenantTelegramUser
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio


def make_init_data(bot_token: str, telegram_user_id: int) -> str:
    params = {
        "auth_date": str(int(time.time())),
        "query_id": "phase6-2-query",
        "user": json.dumps(
            {
                "id": telegram_user_id,
                "first_name": "Launch",
                "username": "launch_user",
            },
            separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urllib.parse.urlencode(params)


async def test_build_miniapp_url_requires_https_and_injects_bot_id() -> None:
    bot_id = uuid.uuid4()
    url = build_miniapp_url("https://shop.example.com/miniapp/?campaign=fall", bot_id)
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query))

    assert parsed.scheme == "https"
    assert parsed.path == "/miniapp/"
    assert query == {"campaign": "fall", "bot_id": str(bot_id)}

    with pytest.raises(ValueError, match="HTTPS"):
        build_miniapp_url("http://shop.example.com/miniapp/", bot_id)

    with pytest.raises(ValueError, match="credentials"):
        build_miniapp_url("https://user:pass@shop.example.com/miniapp/", bot_id)


async def test_build_miniapp_url_replaces_preconfigured_bot_id() -> None:
    bot_id = uuid.uuid4()
    url = build_miniapp_url(
        "https://shop.example.com/miniapp/?bot_id=attacker-value&source=menu",
        bot_id,
    )
    query_items = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)

    assert query_items.count(("bot_id", str(bot_id))) == 1
    assert ("bot_id", "attacker-value") not in query_items
    assert ("source", "menu") in query_items




async def test_build_admin_url_requires_https_and_injects_bot_id() -> None:
    bot_id = uuid.uuid4()
    url = build_admin_url("https://shop.example.com/admin/?source=telegram", bot_id)
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query))

    assert parsed.scheme == "https"
    assert parsed.path == "/admin/"
    assert query == {"source": "telegram", "bot_id": str(bot_id)}

    with pytest.raises(ValueError, match="HTTPS"):
        build_admin_url("http://shop.example.com/admin/", bot_id)


async def test_miniapp_reuses_same_tenant_user_across_bots(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(name="Multi Bot Store", slug=f"multi-bot-{uuid.uuid4().hex[:8]}")
    db_session.add(tenant)
    await db_session.flush()

    bot_a = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=610001,
        username="store_a_bot",
        display_name="Store A",
        token_secret_ref="BOT_A_TOKEN",
        is_enabled=True,
    )
    bot_b = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=610002,
        username="store_b_bot",
        display_name="Store B",
        token_secret_ref="BOT_B_TOKEN",
        is_enabled=True,
    )
    db_session.add_all([bot_a, bot_b])
    await db_session.flush()

    telegram_user_id = 987654321
    legacy_user = User(
        telegram_id=None,
        username="launch_user",
        first_name="Launch",
        is_active=True,
    )
    db_session.add(legacy_user)
    await db_session.flush()
    db_session.add(
        Membership(
            tenant_id=tenant.id,
            user_id=legacy_user.id,
            role=Role.CUSTOMER,
            permissions=[],
        )
    )
    db_session.add(
        TenantTelegramUser(
            tenant_id=tenant.id,
            bot_id=bot_a.id,
            telegram_user_id=telegram_user_id,
            user_id=legacy_user.id,
            telegram_username="launch_user",
            first_name="Launch",
        )
    )
    await db_session.flush()

    bot_b_token = "610002:phase6-launch-secret"
    tenant_result, user_result, _ = (
        await TelegramMiniAppAuthService.authenticate_and_resolve_tenant(
        session=db_session,
        raw_init_data=make_init_data(bot_b_token, telegram_user_id),
        bot_id=bot_b.id,
            secret_storage=EnvSecretStorage({"BOT_B_TOKEN": bot_b_token}),
        )
    )
    await db_session.flush()

    assert tenant_result.id == tenant.id
    assert user_result.id == legacy_user.id
    assert user_result.telegram_id == telegram_user_id

    bindings = list(
        (
            await db_session.execute(
                select(TenantTelegramUser).where(
                    TenantTelegramUser.tenant_id == tenant.id,
                    TenantTelegramUser.telegram_user_id == telegram_user_id,
                )
            )
        ).scalars().all()
    )
    assert {binding.bot_id for binding in bindings} == {bot_a.id, bot_b.id}
    assert {binding.user_id for binding in bindings} == {legacy_user.id}
