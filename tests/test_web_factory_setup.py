from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select

from apps.api.main import app
from packages.core.auth import AuthSource, AuthTokenService, hash_password
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.core.system_models import SystemInstallState
from packages.setup.service import SetupError, install_web_factory
from packages.telegram.models import Bot
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
PASSWORD = "test-only-factory-passphrase"
CODE = "test-only-install-code"


@pytest.fixture(autouse=True)
def isolated_auth_key(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-web-factory-jwt-key-0123456789abcdef0123456789")


def setup_payload(**extra):
    return {"setup_code": CODE, "tenant_slug": "test-factory", "tenant_name": "Test Factory",
            "username": "factoryowner", "password": PASSWORD, **extra}


async def test_web_setup_login_and_operator_boundary(db_session, monkeypatch):
    monkeypatch.setattr(settings, "setup_code", CODE)
    monkeypatch.setattr(settings, "local_secret_vault_enabled", False)
    db_session.add(SystemInstallState(id=1, is_initialized=False))
    await db_session.commit()
    app.dependency_overrides[get_db_session] = lambda: db_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            ready = await client.get("/api/v1/setup/status")
            assert ready.json()["ready_for_setup"] is True  # no vault/bot required
            bad = await client.post("/api/v1/setup/initialize-web", json=setup_payload(setup_code="wrong"))
            assert bad.status_code == 403
            short = await client.post("/api/v1/setup/initialize-web", json=setup_payload(password="shortsecret"))
            assert short.status_code == 422
            assert "shortsecret" not in short.text and CODE not in short.text
            assert "input" not in short.json()["detail"][0]

            unknown = await client.post("/api/v1/setup/initialize-web", json=setup_payload(role="OWNER"))
            assert unknown.status_code == 422
            res = await client.post("/api/v1/setup/initialize-web", json=setup_payload())
            assert res.status_code == 200, res.text
            assert PASSWORD not in res.text and CODE not in res.text
            assert await db_session.scalar(select(func.count(Bot.id))) == 0
            again = await client.post("/api/v1/setup/initialize-web", json=setup_payload())
            assert again.status_code == 409
            login = await client.post("/api/v1/auth/login", json={"username": "factoryowner", "password": PASSWORD})
            assert login.status_code == 200, login.text
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            assert (await client.get("/api/v1/admin/bootstrap", headers=headers)).status_code == 200
            assert (await client.get("/api/v1/platform/sales/inquiries", headers=headers)).status_code == 200
            state = await db_session.get(SystemInstallState, 1)
            owner = await db_session.get(User, state.operator_user_id)
            # A normal tenant OWNER, even in the same workspace, is not the operator.
            tenant_owner = User(username="otherowner", hashed_password=hash_password(PASSWORD))
            db_session.add(tenant_owner)
            await db_session.flush()
            db_session.add(Membership(tenant_id=state.tenant_id, user_id=tenant_owner.id, role=Role.OWNER))
            await db_session.commit()
            other_login = await client.post("/api/v1/auth/login", json={"username": "otherowner", "password": PASSWORD})
            other_headers = {"Authorization": "Bearer " + other_login.json()["access_token"]}
            assert (await client.get("/api/v1/platform/sales/inquiries", headers=other_headers)).status_code == 403
            # A token/Telegram session for the operator is not a password session.
            token = AuthTokenService().issue_access_token(user_id=owner.id, tenant_id=state.tenant_id,
                roles=[Role.OWNER], source=AuthSource.SESSION, token_version=owner.token_version)
            assert (await client.get("/api/v1/platform/sales/inquiries", headers={"Authorization": "Bearer " + token})).status_code == 403
            owner.deleted_at = datetime.now(UTC)
            await db_session.commit()
            assert (await client.get("/api/v1/platform/sales/inquiries", headers=headers)).status_code == 403
            owner.deleted_at = None
            owner.token_version += 1
            await db_session.commit()
            assert (await client.get("/api/v1/platform/sales/inquiries", headers=headers)).status_code == 401
    finally:
        app.dependency_overrides.clear()


async def test_web_setup_cannot_take_over_orphan_username(db_session):
    db_session.add(SystemInstallState(id=1, is_initialized=False))
    db_session.add(User(username="FactoryOwner", hashed_password=hash_password("another-test-password")))
    await db_session.commit()
    with pytest.raises(SetupError, match="username already exists"):
        await install_web_factory(session=db_session, tenant_slug="test-factory", tenant_name="Factory",
                                  username="factoryowner", password=PASSWORD)
    assert await db_session.scalar(select(func.count(Tenant.id))) == 0
    state = await db_session.get(SystemInstallState, 1)
    assert not state.is_initialized and state.operator_user_id is None


async def test_web_setup_requires_migration_seed(db_session):
    with pytest.raises(SetupError, match="migrations"):
        await install_web_factory(session=db_session, tenant_slug="test-factory", tenant_name="Factory",
                                  username="factoryowner", password=PASSWORD)


async def test_customer_brief_to_approved_configuration_with_password_operator(db_session, monkeypatch):
    import uuid

    from packages.marketplace.models import CustomerInquiry
    from packages.telegram.admin_login import AdminLoginService

    grants = {}
    async def redis_call(self, operation, key, *args, **kwargs):
        if operation == "set":
            grants[key] = args[0]
            return True
        return grants.pop(key, None)
    monkeypatch.setattr(AdminLoginService, "_redis_call", redis_call)
    monkeypatch.setattr(settings, "setup_code", CODE)
    db_session.add(SystemInstallState(id=1, is_initialized=False))
    await db_session.commit()
    app.dependency_overrides[get_db_session] = lambda: db_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/api/v1/setup/initialize-web", json=setup_payload())).status_code == 200
            login = await client.post("/api/v1/auth/login", json={"username": "factoryowner", "password": PASSWORD})
            auth = {"Authorization": "Bearer " + login.json()["access_token"]}
            checklist = await client.get("/api/v1/admin/onboarding/checklist", headers=auth)
            assert checklist.json()["workspace_kind"] == "factory"
            assert checklist.json()["launch_ready"] is True
            inquiry = await client.post("/api/v1/public/inquiries", json={
                "contact_handle": "@customer_demo", "format": "combo", "template_key": "digital-goods",
                "delivery_model": "supabase_cloud", "product_source": "stored",
                "brief": {"store_name": "Demo Shop", "accent": "#123456", "store_language": "Arabic",
                          "report_language": "Arabic", "requested_features": ["coupons", "warranty"]}})
            assert inquiry.status_code == 201, inquiry.text
            iid = inquiry.json()["inquiry_id"]
            listed = await client.get("/api/v1/platform/sales/inquiries", headers=auth)
            assert any(i["id"] == iid for i in listed.json()["items"])
            quote = await client.post(f"/api/v1/platform/sales/inquiries/{iid}/quotes", headers=auth, json={
                "customer_name": "Demo Shop", "customer_contact": "@customer_demo",
                "lines": [{"name": "Customer-owned delivery", "category": "setup", "item_type": "one_time", "amount": "100"}]})
            assert quote.status_code == 201, quote.text
            qid = quote.json()["id"]
            assert float(quote.json()["total_monthly"]) == 0
            assert (await client.post(f"/api/v1/platform/sales/quotes/{qid}/accept", headers=auth)).status_code == 200
            # Later changes to the inquiry must not rewrite accepted delivery scope.
            source = await db_session.get(CustomerInquiry, uuid.UUID(iid))
            source.configuration = {"template_key": "general-commerce"}
            await db_session.commit()
            tenant = await client.post(f"/api/v1/platform/sales/quotes/{qid}/onboard", headers=auth, json={
                "tenant_slug": "demo-shop", "tenant_name": "Demo Shop", "owner_username": "customer_demo",
                "owner_telegram_id": 123456789})
            assert tenant.status_code == 201, tenant.text
            tid = uuid.UUID(tenant.json()["tenant_id"])
            configured = await db_session.get(Tenant, tid)
            assert configured.settings["onboarding_template"]["_factory"]["template_key"] == "digital-goods"
            assert configured.settings["onboarding_template"]["branding"]["brand_accent"] == "#123456"
            assert configured.settings["onboarding_template"]["locale"] == "ar"
            assert configured.settings["delivery_scope"]["configuration"]["brief"]["requested_features"] == ["coupons", "warranty"]
            customer_login = await client.post("/api/v1/auth/admin-code", json={"code": tenant.json()["login_code"]})
            assert customer_login.status_code == 200
            customer_auth = {"Authorization": "Bearer " + customer_login.json()["access_token"]}
            assert (await client.get("/api/v1/admin/onboarding/checklist", headers=customer_auth)).json()["launch_ready"] is False
            assert (await client.get("/api/v1/platform/sales/inquiries", headers=customer_auth)).status_code == 403
            assert await db_session.scalar(select(func.count(Bot.id))) == 0
    finally:
        app.dependency_overrides.clear()
