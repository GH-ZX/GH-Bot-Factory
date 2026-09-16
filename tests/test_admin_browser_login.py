import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from apps.api.v1.auth import get_admin_login_service
from packages.core.auth import AuthTokenService
from packages.core.database import get_db_session
from packages.telegram.admin_login import AdminLoginError, AdminLoginService
from packages.telegram.models import Bot
from packages.telegram.routers.admin import handle_admin_command
from packages.tenants.models import Membership, Role, Tenant, User


class MemoryGrants:
    def __init__(self):
        self.values = {}
        self.ttl = None

    async def set(self, key, value, *, ex):
        self.values[key] = value
        self.ttl = ex

    async def getdel(self, key):
        return self.values.pop(key, None)


async def identity(session):
    tenant = Tenant(name="Admin login", slug=f"login-{uuid.uuid4().hex}", is_active=True)
    user = User(username="operator", is_active=True)
    session.add_all([tenant, user])
    await session.flush()
    member = Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER, is_active=True)
    bot = Bot(tenant_id=tenant.id, telegram_bot_id=123456, display_name="Login bot", token_secret_ref="TEST_BOT", is_enabled=True, config={})
    session.add_all([member, bot])
    await session.flush()
    return tenant, user, member, bot


@pytest.mark.asyncio
async def test_code_is_single_use_and_not_stored_in_plaintext(db_session):
    tenant, user, _, bot = await identity(db_session)
    redis = MemoryGrants()
    service = AdminLoginService(redis)
    code = await service.issue(db_session, tenant_id=tenant.id, user_id=user.id, bot_id=bot.id)
    assert redis.ttl == 300
    assert all(code not in key + value for key, value in redis.values.items())
    results = await asyncio.gather(service.consume(db_session, code), service.consume(db_session, code), return_exceptions=True)
    assert sum(isinstance(value, AdminLoginError) for value in results) == 1
    assert sum(isinstance(value, tuple) or hasattr(value, '_mapping') for value in results) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["customer", "inactive", "revoked", "deleted_bot", "disabled_bot", "inactive_tenant"])
async def test_code_rechecks_authority(db_session, change):
    tenant, user, member, bot = await identity(db_session)
    service = AdminLoginService(MemoryGrants())
    code = await service.issue(db_session, tenant_id=tenant.id, user_id=user.id, bot_id=bot.id)
    if change == "customer":
        member.role = Role.CUSTOMER
    elif change == "inactive":
        member.is_active = False
    elif change == "revoked":
        user.token_version += 1
    elif change == "disabled_bot":
        bot.is_enabled = False
    elif change == "inactive_tenant":
        tenant.is_active = False
    else:
        from datetime import UTC, datetime
        bot.deleted_at = datetime.now(UTC)
    await db_session.flush()
    with pytest.raises(AdminLoginError):
        await service.consume(db_session, code)


@pytest.mark.asyncio
async def test_browser_login_endpoint_replay_and_tenant_override(db_session):
    tenant, user, _, bot = await identity(db_session)
    service = AdminLoginService(MemoryGrants())
    token_service = AuthTokenService(secret_key="test-browser-login-secret-at-least-32-bytes")
    async def database():
        yield db_session
    app.dependency_overrides[get_db_session] = database
    app.dependency_overrides[get_admin_login_service] = lambda: service
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    try:
        code = await service.issue(db_session, tenant_id=tenant.id, user_id=user.id, bot_id=bot.id)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            bad = await client.post("/api/v1/auth/admin-code", json={"code": code, "tenant_id": str(uuid.uuid4())})
            assert bad.status_code == 422
            good = await client.post("/api/v1/auth/admin-code", json={"code": code})
            assert good.status_code == 200, good.text
            assert good.headers["cache-control"] == "no-store"
            claims = token_service.verify_access_token(good.json()["access_token"])
            assert claims["tenant_id"] == str(tenant.id)
            assert claims["sub"] == str(user.id)
            replay = await client.post("/api/v1/auth/admin-code", json={"code": code})
            assert replay.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_command_never_issues_code_in_group():
    message = SimpleNamespace(chat=SimpleNamespace(type="group"), answer=AsyncMock())
    await handle_admin_command(message, None, None)
    assert "private chat" in message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_expired_or_malformed_code_fails_closed(db_session):
    service = AdminLoginService(MemoryGrants())
    for code in ("x" * 32, "not-a-code"):
        with pytest.raises(AdminLoginError):
            await service.consume(db_session, code)
