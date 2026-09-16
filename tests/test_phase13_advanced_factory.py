import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.commerce.economics import PricingService
from packages.commerce.economics_models import PricingTier
from packages.commerce.models import Product, ProductVariant
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.factory.business_profiles import business_profile_from_config
from packages.factory.models import BotProvisioningJob
from packages.payments.models import PaymentMethodConfig, PaymentMethodType, PaymentVerificationMode
from packages.providers.models import (
    Provider,
    ProviderCategory,
    ProviderProductMapping,
    ProviderRoutingStrategy,
)
from packages.providers.router import ProviderRouter
from packages.telegram.models import Bot
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase13-factory-jwt-secret-0123456789abcdef-0123456789abcdef"


async def create_identity(
    session: AsyncSession,
    tenant: Tenant,
    *,
    role: Role,
    bot_id: uuid.UUID | None = None,
) -> tuple[User, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"{role.value.lower()}_{uuid.uuid4().hex[:8]}",
        first_name="Factory",
        last_name="Tester",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role, permissions=[], is_active=True))
    await session.flush()
    token = AuthTokenService(secret_key=TEST_JWT_SECRET).issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[role],
        source=AuthSource.TEST,
        token_version=user.token_version,
        extra_claims={"bot_id": str(bot_id)} if bot_id else None,
    )
    return user, token


@pytest_asyncio.fixture
async def client_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "session": db_session}
    app.dependency_overrides.clear()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_business_options(session: AsyncSession, tenant: Tenant):
    number = Provider(
        tenant_id=tenant.id,
        name="Numbers Mock",
        slug=f"numbers-{uuid.uuid4().hex[:6]}",
        provider_type="mock",
        category=ProviderCategory.NUMBER,
        is_enabled=True,
        priority=1,
        metadata_json={},
    )
    gift = Provider(
        tenant_id=tenant.id,
        name="Gift Mock",
        slug=f"gift-{uuid.uuid4().hex[:6]}",
        provider_type="mock",
        category=ProviderCategory.GIFT,
        is_enabled=True,
        priority=2,
        metadata_json={},
    )
    method_a = PaymentMethodConfig(
        tenant_id=tenant.id,
        code=f"goza-{uuid.uuid4().hex[:6]}",
        display_name="GoZaPay USDT",
        method_type=PaymentMethodType.CRYPTO_GATEWAY,
        verification_mode=PaymentVerificationMode.PROVIDER_RECONCILIATION,
        provider_name="gozapay",
        is_enabled=True,
        auto_credit_enabled=True,
        auto_credit_target="ASSET_WALLET",
        settings_json={"topup_currencies": ["USD"], "flexible_deposits_enabled": True},
    )
    method_b = PaymentMethodConfig(
        tenant_id=tenant.id,
        code=f"manual-{uuid.uuid4().hex[:6]}",
        display_name="Manual Transfer",
        method_type=PaymentMethodType.MANUAL_TRANSFER,
        verification_mode=PaymentVerificationMode.MANUAL,
        is_enabled=True,
        auto_credit_enabled=False,
        settings_json={"topup_currencies": ["USD"]},
    )
    tier = PricingTier(
        tenant_id=tenant.id,
        code=f"reseller-{uuid.uuid4().hex[:6]}",
        display_name="Reseller Default",
        priority=10,
        is_default=False,
        is_active=True,
    )
    session.add_all([number, gift, method_a, method_b, tier])
    await session.flush()
    return number, gift, method_a, method_b, tier


async def test_advanced_templates_and_wizard_options_are_category_aware(client_env):
    client: httpx.AsyncClient = client_env["client"]
    session: AsyncSession = client_env["session"]
    tenant = Tenant(name="Wizard Tenant", slug=f"wizard-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)
    number, gift, method_a, method_b, tier = await _seed_business_options(session, tenant)

    templates = await client.get("/api/v1/admin/bots/templates", headers=auth(admin_token))
    assert templates.status_code == 200
    keys = {row["key"] for row in templates.json()["templates"]}
    assert {"reseller-hub", "numbers-sms", "accounts-store", "gift-reseller", "digital-reseller", "hybrid-store"} <= keys

    options = await client.get(
        "/api/v1/admin/bots/wizard/options?template_key=numbers-sms", headers=auth(admin_token)
    )
    assert options.status_code == 200, options.text
    data = options.json()
    assert data["business_type"] == "NUMBER_SMS"
    assert data["default_routing_strategy"] == "AVAILABILITY"
    assert {row["id"] for row in data["providers"]} == {str(number.id)}
    assert str(gift.id) not in {row["id"] for row in data["providers"]}
    assert {row["id"] for row in data["payment_methods"]} == {str(method_a.id), str(method_b.id)}
    assert str(tier.id) in {row["id"] for row in data["pricing_tiers"]}


async def test_provisioning_validates_business_profile_tenant_and_category(client_env):
    client: httpx.AsyncClient = client_env["client"]
    session: AsyncSession = client_env["session"]
    tenant = Tenant(name="Business Tenant", slug=f"business-{uuid.uuid4().hex[:6]}", is_active=True)
    other = Tenant(name="Foreign Tenant", slug=f"foreign-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)
    number, gift, method_a, _, tier = await _seed_business_options(session, tenant)
    foreign_provider, *_ = await _seed_business_options(session, other)

    profile = {
        "business_type": "NUMBER_SMS",
        "provider_ids": [str(number.id)],
        "payment_method_ids": [str(method_a.id)],
        "routing_strategy": "MANUAL",
        "preferred_provider_id": str(number.id),
        "default_pricing_tier_id": str(tier.id),
        "allow_flexible_auto_credit": True,
    }
    response = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "phase13-valid-profile"},
        json={
            "token_secret_ref": "PHASE13_BOT_TOKEN",
            "display_name": "Numbers Bot",
            "template_key": "numbers-sms",
            "business_profile": profile,
        },
    )
    assert response.status_code == 202, response.text
    job = await session.get(BotProvisioningJob, uuid.UUID(response.json()["id"]))
    assert job is not None
    saved = business_profile_from_config(job.desired_config)
    assert saved.business_type.value == "NUMBER_SMS"
    assert saved.provider_ids == (number.id,)
    assert saved.preferred_provider_id == number.id
    assert saved.default_pricing_tier_id == tier.id
    assert saved.allow_flexible_auto_credit is True

    incompatible = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "phase13-incompatible"},
        json={
            "token_secret_ref": "PHASE13_BOT_TOKEN_2",
            "template_key": "numbers-sms",
            "business_profile": {**profile, "provider_ids": [str(gift.id)], "preferred_provider_id": str(gift.id)},
        },
    )
    assert incompatible.status_code == 422

    foreign = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "phase13-foreign"},
        json={
            "token_secret_ref": "PHASE13_BOT_TOKEN_3",
            "template_key": "numbers-sms",
            "business_profile": {**profile, "provider_ids": [str(foreign_provider.id)], "preferred_provider_id": str(foreign_provider.id)},
        },
    )
    assert foreign.status_code == 422


async def test_storefront_payment_methods_are_filtered_and_auto_credit_is_bot_gated(client_env):
    client: httpx.AsyncClient = client_env["client"]
    session: AsyncSession = client_env["session"]
    tenant = Tenant(name="Payment Bot Tenant", slug=f"paybot-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    _, _, method_a, method_b, _ = await _seed_business_options(session, tenant)
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=int(uuid.uuid4().int % 8_000_000_000) + 10_000,
        username=f"paybot_{uuid.uuid4().hex[:8]}",
        display_name="Payment Bot",
        token_secret_ref="PAYBOT_REF",
        is_enabled=True,
        config={
            "_business": {
                "business_type": "RESELLER",
                "provider_ids": [],
                "payment_method_ids": [str(method_a.id)],
                "routing_strategy": "PRIORITY",
                "preferred_provider_id": None,
                "default_pricing_tier_id": None,
                "allow_flexible_auto_credit": False,
            }
        },
    )
    session.add(bot)
    await session.flush()
    _, customer_token = await create_identity(session, tenant, role=Role.CUSTOMER, bot_id=bot.id)

    response = await client.get("/api/v1/storefront/wallet/payment-methods", headers=auth(customer_token))
    assert response.status_code == 200, response.text
    methods = response.json()["methods"]
    assert [row["id"] for row in methods] == [str(method_a.id)]
    assert methods[0]["auto_credit_enabled"] is False
    assert str(method_b.id) not in {row["id"] for row in methods}


async def test_bot_default_pricing_tier_and_provider_filter_are_authoritative(db_session: AsyncSession):
    tenant = Tenant(name="Route Tenant", slug=f"route-{uuid.uuid4().hex[:6]}", is_active=True)
    session = db_session
    session.add(tenant)
    await session.flush()
    user, _ = await create_identity(session, tenant, role=Role.CUSTOMER)
    number, gift, _, _, tier = await _seed_business_options(session, tenant)
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=int(uuid.uuid4().int % 8_000_000_000) + 20_000,
        username=f"routebot_{uuid.uuid4().hex[:8]}",
        display_name="Route Bot",
        token_secret_ref="ROUTEBOT_REF",
        is_enabled=True,
        config={
            "_business": {
                "business_type": "NUMBER_SMS",
                "provider_ids": [str(number.id)],
                "payment_method_ids": [],
                "routing_strategy": "HEALTHIEST",
                "preferred_provider_id": None,
                "default_pricing_tier_id": str(tier.id),
                "allow_flexible_auto_credit": False,
            }
        },
    )
    product = Product(tenant_id=tenant.id, title="Telegram number", is_active=True, metadata_json={})
    session.add_all([bot, product])
    await session.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku="TG-US",
        title="US",
        price=Decimal("2.00"),
        currency="USD",
        stock_quantity=10,
        is_active=True,
        attributes={},
    )
    session.add(variant)
    await session.flush()
    session.add_all(
        [
            ProviderProductMapping(
                tenant_id=tenant.id,
                provider_id=number.id,
                product_id=product.id,
                product_variant_id=variant.id,
                external_product_id="number-tg-us",
                is_enabled=True,
                cost_price=Decimal("1.00"),
                cost_currency="USD",
                provider_metadata={},
            ),
            ProviderProductMapping(
                tenant_id=tenant.id,
                provider_id=gift.id,
                product_id=product.id,
                product_variant_id=variant.id,
                external_product_id="gift-wrong-category",
                is_enabled=True,
                cost_price=Decimal("0.50"),
                cost_currency="USD",
                provider_metadata={},
            ),
        ]
    )
    await session.flush()

    resolved = await PricingService().resolve_tier(
        session, tenant_id=tenant.id, user_id=user.id, bot_id=bot.id
    )
    assert resolved is not None and resolved.id == tier.id

    profile = business_profile_from_config(bot.config)
    mappings = await ProviderRouter().get_eligible_mappings(
        session,
        tenant_id=tenant.id,
        product_id=product.id,
        variant_id=variant.id,
        allowed_provider_ids=set(profile.provider_ids),
        allowed_categories={ProviderCategory.NUMBER},
        strategy_override=ProviderRoutingStrategy.HEALTHIEST,
        routing_key="phase13-routing",
    )
    assert [mapping.provider_id for mapping in mappings] == [number.id]

async def test_legacy_bot_without_business_profile_inherits_existing_routing_and_access_defaults():
    profile = business_profile_from_config({"currency": "USD"})
    assert profile.routing_strategy is None
    assert profile.provider_ids == ()
    assert profile.payment_method_ids == ()
    assert profile.allow_flexible_auto_credit is True


async def test_malformed_persisted_business_profile_fails_closed():
    profile = business_profile_from_config({"_business": {"routing_strategy": "NOT_A_STRATEGY"}})
    assert profile.routing_strategy is None
    assert profile.provider_ids
    assert profile.payment_method_ids
    assert profile.allow_flexible_auto_credit is False


@pytest.mark.parametrize("bad_value", ["false", "true", 0, 1, [], {}, None])
async def test_malformed_auto_credit_never_enables_credit(bad_value):
    from packages.factory.business_profiles import parse_business_profile
    with pytest.raises(ValueError, match="must be a boolean"):
        parse_business_profile({"allow_flexible_auto_credit": bad_value})
    profile = business_profile_from_config({"_business": {"allow_flexible_auto_credit": bad_value}})
    assert profile.allow_flexible_auto_credit is False
    assert profile.payment_method_ids


async def test_profile_bot_cannot_use_legacy_funding_or_deleted_bot_context(client_env):
    client, session = client_env["client"], client_env["session"]
    tenant = Tenant(name="Restricted", slug=f"restricted-{uuid.uuid4().hex}", is_active=True)
    session.add(tenant)
    await session.flush()
    bot = Bot(tenant_id=tenant.id, telegram_bot_id=987654321, display_name="Restricted bot",
              token_secret_ref="TEST_REF", is_enabled=True,
              config={"_business": {"payment_method_ids": [str(uuid.uuid4())]}})
    session.add(bot)
    await session.flush()
    _, token = await create_identity(session, tenant, role=Role.CUSTOMER, bot_id=bot.id)
    options = await client.get("/api/v1/storefront/wallet/topups/options", headers=auth(token))
    assert options.status_code == 200
    assert options.json()["providers"] == []
    create = await client.post("/api/v1/storefront/wallet/topups", headers=auth(token), json={
        "amount": "10.00", "currency": "USD", "provider_name": "mock",
        "idempotency_key": "restricted-legacy-test", "terms_accepted": False,
    })
    assert create.status_code == 400
    assert "payment method" in create.json()["detail"]
    bot.is_enabled = False
    await session.flush()
    denied = await client.get("/api/v1/storefront/wallet/payment-methods", headers=auth(token))
    assert denied.status_code == 403
