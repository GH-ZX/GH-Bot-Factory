from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.factory.templates import list_bot_templates
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase14-guidance-jwt-secret-0123456789abcdef-0123456789abcdef"


async def create_identity(
    session: AsyncSession,
    tenant: Tenant,
    *,
    role: Role,
) -> tuple[User, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"{role.value.lower()}_{uuid.uuid4().hex[:8]}",
        first_name="Guidance",
        last_name="Tester",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    session.add(
        Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            permissions=[],
            is_active=True,
        )
    )
    await session.flush()
    token = AuthTokenService(secret_key=TEST_JWT_SECRET).issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[role],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )
    return user, token


@pytest_asyncio.fixture
async def client_env(db_session: AsyncSession) -> dict[str, Any]:
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    app.dependency_overrides[get_db_session] = lambda: db_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "session": db_session}

    app.dependency_overrides.clear()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_all_eleven_templates_have_complete_structured_guidance() -> None:
    templates = list_bot_templates()
    assert len(templates) == 11, f"Expected exactly 11 templates, got {len(templates)}"

    expected_keys = {
        "general-commerce",
        "digital-goods",
        "gift-cards",
        "gaming-store",
        "services",
        "reseller-hub",
        "numbers-sms",
        "accounts-store",
        "gift-reseller",
        "digital-reseller",
        "hybrid-store",
    }
    actual_keys = {t.key for t in templates}
    assert actual_keys == expected_keys

    valid_sources = {"stored", "provider_api", "hybrid"}
    valid_complexities = {"Low", "Medium", "High"}

    for t in templates:
        assert t.guidance is not None, f"Template {t.key} missing guidance"
        g = t.guidance
        assert g.product_source in valid_sources, f"{t.key} invalid source {g.product_source}"
        assert g.operational_complexity in valid_complexities, f"{t.key} invalid complexity {g.operational_complexity}"
        assert len(g.what_you_can_sell.strip()) > 10, f"{t.key} what_you_can_sell too short"
        assert len(g.delivery_experience.strip()) > 10, f"{t.key} delivery_experience too short"
        assert len(g.example_business.strip()) > 10, f"{t.key} example_business too short"
        assert len(g.setup_requirements) >= 2, f"{t.key} setup_requirements must have >= 2 items"
        assert "managed" in g.supported_hosting, f"{t.key} missing managed hosting"

        payload = t.public_payload()
        assert "guidance" in payload
        p_guidance = payload["guidance"]
        assert p_guidance["product_source"] == g.product_source
        assert p_guidance["operational_complexity"] == g.operational_complexity
        assert p_guidance["setup_requirements"] == list(g.setup_requirements)


async def test_admin_api_returns_guidance_for_all_templates(client_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = client_env["client"]
    session: AsyncSession = client_env["session"]
    tenant = Tenant(name="Guidance Tenant", slug=f"guidance-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)

    response = await client.get("/api/v1/admin/bots/templates", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert "templates" in body
    rows = body["templates"]
    assert len(rows) == 11

    by_key = {r["key"]: r for r in rows}

    # Verify stored template guidance
    gc = by_key["general-commerce"]
    assert gc["guidance"]["product_source"] == "stored"
    assert gc["guidance"]["operational_complexity"] == "Low"
    assert "merchandise" in gc["guidance"]["what_you_can_sell"].lower()

    # Verify provider API template guidance
    sms = by_key["numbers-sms"]
    assert sms["guidance"]["product_source"] == "provider_api"
    assert sms["guidance"]["operational_complexity"] == "Medium"
    assert "virtual phone numbers" in sms["guidance"]["what_you_can_sell"].lower()

    # Verify hybrid template guidance
    hybrid = by_key["hybrid-store"]
    assert hybrid["guidance"]["product_source"] == "hybrid"
    assert hybrid["guidance"]["operational_complexity"] == "High"
    assert len(hybrid["guidance"]["setup_requirements"]) >= 3
