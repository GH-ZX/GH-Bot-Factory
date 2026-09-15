import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.commerce.models import Order, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.fulfillment.models import FulfillmentAttempt, FulfillmentStatus
from packages.fulfillment.reconciliation import ReconciliationService
from packages.payments.models import PaymentProviderConfig
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.registry import ProviderClientRegistry
from packages.providers.models import Provider, ProviderCredential, ProviderProductMapping
from packages.providers.router import ProviderRouter
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase7-2-provider-config-test-jwt-secret-0123456789abcdef"


async def create_identity(
    session: AsyncSession,
    tenant: Tenant,
    role: Role,
) -> tuple[User, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"{role.value.lower()}_{uuid.uuid4().hex[:8]}",
        first_name="Provider Admin",
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


async def create_product(session: AsyncSession, tenant: Tenant, title: str) -> tuple[Product, ProductVariant]:
    product = Product(
        tenant_id=tenant.id,
        title=title,
        description="Provider mapped item",
        metadata_json={},
        is_active=True,
    )
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku=f"SKU-{uuid.uuid4().hex[:8]}",
        title="Standard",
        price=Decimal("20.00"),
        currency="USD",
        stock_quantity=50,
        is_active=True,
        attributes={},
    )
    session.add(variant)
    await session.flush()
    return product, variant


@pytest_asyncio.fixture
async def provider_admin_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
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


async def test_provider_capabilities_and_supplier_listing_are_tenant_scoped(
    provider_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = provider_admin_env["client"]
    session: AsyncSession = provider_admin_env["session"]
    tenant = Tenant(name="Provider A", slug=f"provider-a-{uuid.uuid4().hex[:6]}", is_active=True)
    other = Tenant(name="Provider B", slug=f"provider-b-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, token = await create_identity(session, tenant, Role.STAFF)
    session.add_all(
        [
            Provider(tenant_id=tenant.id, name="Visible", slug="visible", provider_type="MOCK", priority=1),
            Provider(tenant_id=other.id, name="Hidden", slug="hidden", provider_type="MOCK", priority=1),
        ]
    )
    await session.commit()

    caps = await client.get("/api/v1/admin/provider-capabilities", headers=auth(token))
    assert caps.status_code == 200
    assert "MOCK" in caps.json()["supplier_provider_types"]
    assert "telegram_stars" in caps.json()["payment_provider_names"]

    response = await client.get("/api/v1/admin/providers", headers=auth(token))
    assert response.status_code == 200
    assert [row["name"] for row in response.json()] == ["Visible"]


async def test_supplier_configuration_requires_admin_and_rejects_secret_material(
    provider_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = provider_admin_env["client"]
    session: AsyncSession = provider_admin_env["session"]
    tenant = Tenant(name="Supplier Config", slug=f"supplier-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    _, staff_token = await create_identity(session, tenant, Role.STAFF)
    admin, admin_token = await create_identity(session, tenant, Role.ADMIN)
    await session.commit()

    forbidden = await client.post(
        "/api/v1/admin/providers",
        headers=auth(staff_token),
        json={"name": "Nope", "slug": "nope", "provider_type": "MOCK"},
    )
    assert forbidden.status_code == 403

    leaked = await client.post(
        "/api/v1/admin/providers",
        headers=auth(admin_token),
        json={
            "name": "Unsafe",
            "slug": "unsafe",
            "provider_type": "MOCK",
            "metadata": {"api_key": "plaintext-value"},
        },
    )
    assert leaked.status_code == 422

    created = await client.post(
        "/api/v1/admin/providers",
        headers=auth(admin_token),
        json={
            "name": "Safe Mock",
            "slug": "safe-mock",
            "provider_type": "MOCK",
            "priority": 2,
            "metadata": {"region": "eu"},
        },
    )
    assert created.status_code == 201, created.text
    provider_id = created.json()["id"]

    invalid_ref = await client.put(
        f"/api/v1/admin/providers/{provider_id}/credentials",
        headers=auth(admin_token),
        json={"credential_type": "API_KEY", "secret_ref": "123456:PLAINTEXT"},
    )
    assert invalid_ref.status_code == 422

    credential = await client.put(
        f"/api/v1/admin/providers/{provider_id}/credentials",
        headers=auth(admin_token),
        json={"credential_type": "API_KEY", "secret_ref": "ENV_SAFE_PROVIDER_API_KEY"},
    )
    assert credential.status_code == 200
    assert "secret_ref" not in credential.json()

    listing = await client.get("/api/v1/admin/providers", headers=auth(admin_token))
    body = listing.json()[0]
    assert body["credentials"] == [{"credential_type": "API_KEY", "configured": True}]
    assert "ENV_SAFE_PROVIDER_API_KEY" not in listing.text

    audit_actions = set(
        (
            await session.execute(
                select(AuditLog.action).where(
                    AuditLog.tenant_id == tenant.id,
                    AuditLog.user_id == admin.id,
                )
            )
        ).scalars().all()
    )
    assert "SUPPLIER_PROVIDER_CREATED" in audit_actions
    assert "SUPPLIER_CREDENTIAL_REFERENCE_UPDATED" in audit_actions


async def test_supplier_mapping_enforces_product_and_variant_tenant_ownership(
    provider_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = provider_admin_env["client"]
    session: AsyncSession = provider_admin_env["session"]
    tenant = Tenant(name="Mapping A", slug=f"mapping-a-{uuid.uuid4().hex[:6]}", is_active=True)
    other = Tenant(name="Mapping B", slug=f"mapping-b-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, token = await create_identity(session, tenant, Role.ADMIN)
    product, variant = await create_product(session, tenant, "Visible product")
    foreign_product, foreign_variant = await create_product(session, other, "Foreign product")
    provider = Provider(tenant_id=tenant.id, name="Supplier", slug="supplier-map", provider_type="MOCK", priority=1)
    session.add(provider)
    await session.commit()

    cross_tenant = await client.post(
        f"/api/v1/admin/providers/{provider.id}/mappings",
        headers=auth(token),
        json={
            "product_id": str(foreign_product.id),
            "product_variant_id": str(foreign_variant.id),
            "external_product_id": "foreign",
        },
    )
    assert cross_tenant.status_code == 404

    wrong_variant = await client.post(
        f"/api/v1/admin/providers/{provider.id}/mappings",
        headers=auth(token),
        json={
            "product_id": str(product.id),
            "product_variant_id": str(foreign_variant.id),
            "external_product_id": "wrong",
        },
    )
    assert wrong_variant.status_code == 404

    created = await client.post(
        f"/api/v1/admin/providers/{provider.id}/mappings",
        headers=auth(token),
        json={
            "product_id": str(product.id),
            "product_variant_id": str(variant.id),
            "external_product_id": "external-123",
            "cost_price": "12.25",
            "cost_currency": "usd",
            "priority_override": 1,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["product_title"] == "Visible product"
    assert created.json()["variant_title"] == "Standard"
    assert created.json()["cost_currency"] == "USD"


async def test_provider_router_resolves_secret_references_only_at_runtime(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Runtime Secrets", slug=f"runtime-{uuid.uuid4().hex[:6]}", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    product, variant = await create_product(db_session, tenant, "Runtime mapped")
    provider = Provider(tenant_id=tenant.id, name="Runtime Mock", slug="runtime-mock", provider_type="MOCK", priority=1)
    db_session.add(provider)
    await db_session.flush()
    db_session.add_all(
        [
            ProviderCredential(
                tenant_id=tenant.id,
                provider_id=provider.id,
                credential_type="API_KEY",
                secret_ref="ENV_RUNTIME_KEY",
            ),
            ProviderProductMapping(
                tenant_id=tenant.id,
                provider_id=provider.id,
                product_id=product.id,
                product_variant_id=variant.id,
                external_product_id="mock-product",
                is_enabled=True,
                cost_price=Decimal("1.00"),
                cost_currency="USD",
                provider_metadata={},
            ),
        ]
    )
    await db_session.commit()

    router = ProviderRouter(secret_storage=EnvSecretStorage({"ENV_RUNTIME_KEY": "super-secret-runtime-value"}))
    mappings = await router.get_eligible_mappings(db_session, tenant.id, product.id, variant.id)
    config = await router.build_provider_config(mappings[0].provider)
    assert config["credentials"] == {"API_KEY": "super-secret-runtime-value"}

    stored = (
        await db_session.execute(select(ProviderCredential).where(ProviderCredential.provider_id == provider.id))
    ).scalar_one()
    assert stored.secret_ref == "ENV_RUNTIME_KEY"
    assert "super-secret-runtime-value" not in stored.secret_ref


async def test_payment_provider_upsert_hides_secret_refs_and_is_tenant_scoped(
    provider_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = provider_admin_env["client"]
    session: AsyncSession = provider_admin_env["session"]
    tenant = Tenant(name="Payments Config", slug=f"paycfg-{uuid.uuid4().hex[:6]}", is_active=True)
    other = Tenant(name="Other Payments", slug=f"other-paycfg-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, admin_token = await create_identity(session, tenant, Role.ADMIN)
    _, staff_token = await create_identity(session, tenant, Role.STAFF)
    session.add(
        PaymentProviderConfig(
            tenant_id=other.id,
            provider_name="mock",
            is_enabled=True,
            credentials_ref="OTHER_SECRET_REF",
            webhook_secret_ref=None,
            settings_json={"display_name": "Hidden"},
        )
    )
    await session.commit()

    secret_in_settings = await client.put(
        "/api/v1/admin/payment-providers/mock",
        headers=auth(admin_token),
        json={
            "credentials_ref": "ENV_PAYMENT_KEY",
            "settings": {"api_token": "plaintext"},
        },
    )
    assert secret_in_settings.status_code == 422

    created = await client.put(
        "/api/v1/admin/payment-providers/mock",
        headers=auth(admin_token),
        json={
            "credentials_ref": "ENV_PAYMENT_KEY",
            "webhook_secret_ref": "ENV_PAYMENT_WEBHOOK",
            "is_enabled": True,
            "settings": {
                "display_name": "Card Gateway",
                "topup_enabled": True,
                "topup_min_amount": "5.00",
                "topup_max_amount": "500.00",
                "topup_currencies": ["USD"],
            },
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["credentials_configured"] is True
    assert created.json()["webhook_secret_configured"] is True
    assert "ENV_PAYMENT_KEY" not in created.text
    assert "ENV_PAYMENT_WEBHOOK" not in created.text

    listing = await client.get("/api/v1/admin/payment-providers", headers=auth(staff_token))
    assert listing.status_code == 200
    assert [row["provider_name"] for row in listing.json()] == ["mock"]
    assert "OTHER_SECRET_REF" not in listing.text
    assert "ENV_PAYMENT_KEY" not in listing.text

    stored = (
        await session.execute(
            select(PaymentProviderConfig).where(
                PaymentProviderConfig.tenant_id == tenant.id,
                PaymentProviderConfig.provider_name == "mock",
            )
        )
    ).scalar_one()
    assert stored.credentials_ref == "ENV_PAYMENT_KEY"
    assert stored.webhook_secret_ref == "ENV_PAYMENT_WEBHOOK"


async def test_reconciliation_uses_secret_resolved_provider_configuration(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Reconcile Secrets", slug=f"reconcile-{uuid.uuid4().hex[:6]}", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    customer, _ = await create_identity(db_session, tenant, Role.CUSTOMER)
    provider = Provider(
        tenant_id=tenant.id,
        name="Secure Reconcile",
        slug="secure-reconcile",
        provider_type="SECURE_TEST",
        priority=1,
    )
    db_session.add(provider)
    await db_session.flush()
    db_session.add(
        ProviderCredential(
            tenant_id=tenant.id,
            provider_id=provider.id,
            credential_type="API_KEY",
            secret_ref="ENV_RECONCILE_KEY",
        )
    )
    order = Order(
        tenant_id=tenant.id,
        user_id=customer.id,
        order_number=f"REC-{uuid.uuid4().hex[:8]}",
        status=OrderStatus.PROCESSING,
        total_amount=Decimal("20.00"),
        currency="USD",
    )
    db_session.add(order)
    await db_session.flush()
    attempt = FulfillmentAttempt(
        tenant_id=tenant.id,
        order_id=order.id,
        provider_id=provider.id,
        attempt_number=1,
        idempotency_key=f"reconcile:{order.id}:1",
        status=FulfillmentStatus.PROCESSING,
        external_order_id="ext-reconcile-done",
        request_payload={},
        response_payload={},
        cost_currency="USD",
    )
    db_session.add(attempt)
    await db_session.commit()

    captured_config: dict[str, Any] = {}

    def secure_factory(name: str, config: dict[str, Any]) -> MockProvider:
        captured_config.update(config)
        client = MockProvider(provider_name=name, config=config)
        client.orders["ext-reconcile-done"] = {
            "external_order_id": "ext-reconcile-done",
            "status": "COMPLETED",
            "cost": "1.00",
        }
        return client

    registry = ProviderClientRegistry()
    registry.register_type("SECURE_TEST", secure_factory)
    router = ProviderRouter(
        registry=registry,
        secret_storage=EnvSecretStorage({"ENV_RECONCILE_KEY": "runtime-reconciliation-secret"}),
    )
    service = ReconciliationService(registry=registry, provider_router=router)
    discrepancies = await service.reconcile_order(db_session, tenant.id, order.id)

    assert discrepancies[0].issue_type == "OUT_OF_SYNC_RESOLVED"
    assert captured_config["credentials"] == {"API_KEY": "runtime-reconciliation-secret"}
    await db_session.refresh(order)
    await db_session.refresh(attempt)
    assert order.status == OrderStatus.FULFILLED
    assert attempt.status == FulfillmentStatus.SUCCEEDED


async def test_telegram_stars_admin_config_enforces_native_payment_invariants(
    provider_admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = provider_admin_env["client"]
    session: AsyncSession = provider_admin_env["session"]
    tenant = Tenant(name="Stars Config", slug=f"stars-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    _, admin_token = await create_identity(session, tenant, Role.ADMIN)
    await session.commit()

    endpoint_override = await client.put(
        "/api/v1/admin/payment-providers/telegram_stars",
        headers=auth(admin_token),
        json={
            "credentials_ref": "ENV_STARS_BOT_TOKEN",
            "settings": {
                "api_base_url": "https://attacker.example",
                "terms_url": "https://store.example/terms",
            },
        },
    )
    assert endpoint_override.status_code == 422

    wrong_currency = await client.put(
        "/api/v1/admin/payment-providers/telegram_stars",
        headers=auth(admin_token),
        json={
            "credentials_ref": "ENV_STARS_BOT_TOKEN",
            "settings": {
                "topup_currencies": ["USD"],
                "terms_url": "https://store.example/terms",
            },
        },
    )
    assert wrong_currency.status_code == 422

    insecure_terms = await client.put(
        "/api/v1/admin/payment-providers/telegram_stars",
        headers=auth(admin_token),
        json={
            "credentials_ref": "ENV_STARS_BOT_TOKEN",
            "settings": {"terms_url": "http://store.example/terms"},
        },
    )
    assert insecure_terms.status_code == 422

    created = await client.put(
        "/api/v1/admin/payment-providers/telegram_stars",
        headers=auth(admin_token),
        json={
            "credentials_ref": "ENV_STARS_BOT_TOKEN",
            "is_enabled": True,
            "settings": {
                "display_name": "Telegram Stars",
                "topup_min_amount": "10",
                "topup_max_amount": "10000",
                "topup_currencies": ["xtr"],
                "terms_url": "https://store.example/terms",
                "terms_version": "2026-09",
                "transaction_scan_pages": 12,
            },
        },
    )
    assert created.status_code == 200, created.text
    settings = created.json()["settings"]
    assert settings["topup_currencies"] == ["XTR"]
    assert settings["topup_whole_units_only"] is True
    assert settings["checkout_mode"] == "telegram_invoice"
    assert settings["terms_required"] is True
    assert settings["transaction_scan_pages"] == 12
    assert "api_base_url" not in settings
    assert "ENV_STARS_BOT_TOKEN" not in created.text
