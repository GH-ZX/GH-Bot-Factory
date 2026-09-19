from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.commerce.models import Product
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.providers.clients.registry import provider_registry
from packages.providers.interface import ProviderProductDTO
from packages.providers.models import (
    Provider,
    ProviderCategory,
    ProviderCredential,
    ProviderProductMapping,
)
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio

TEST_JWT_SECRET = "test-jwt-secret-importer-0123456789abcdef-0123456789abcdef"


class DummySupplierClient:
    def __init__(self, provider_name: str = "DummySupplier", config: dict | None = None) -> None:
        self.provider_name = provider_name
        self.config = config or {}

    async def list_products(self, lang: str = "en") -> list[ProviderProductDTO]:
        desc1 = "الوصف بالعربية للجيميني" if lang == "ar" else "Gemini AI 18 months activation"
        desc2 = "وصف شات جي بي تي" if lang == "ar" else "ChatGPT Plus 1 Month Account"
        return [
            ProviderProductDTO(
                external_id="PROD-101",
                name="Gemini Pro 18M",
                cost=Decimal("1.00"),
                currency="USD",
                stock=50,
                description=desc1,
            ),
            ProviderProductDTO(
                external_id="PROD-102",
                name="ChatGPT Plus 1M",
                cost=Decimal("5.00"),
                currency="USD",
                stock=10,
                description=desc2,
            ),
        ]


async def test_list_importable_products(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    from packages.telegram.secrets import EnvSecretStorage
    storage = EnvSecretStorage({"DUMMY_KEY_REF": "test-dummy-key"})
    monkeypatch.setattr("apps.api.v1.admin_providers.get_default_secret_storage", lambda: storage)
    tenant = Tenant(name="Import Tenant", slug=f"import-t-{uuid.uuid4().hex[:6]}")
    user = User(username=f"owner_{uuid.uuid4().hex[:6]}")
    db_session.add_all([tenant, user])
    await db_session.flush()

    db_session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER))
    provider = Provider(
        tenant_id=tenant.id,
        name="Dummy Provider",
        slug="dummy-prov",
        provider_type="MOCK",
        category=ProviderCategory.DIGITAL_PRODUCT,
        is_enabled=True,
    )
    db_session.add(provider)
    await db_session.flush()

    db_session.add(
        ProviderCredential(
            tenant_id=tenant.id,
            provider_id=provider.id,
            credential_type="API_KEY",
            secret_ref="DUMMY_KEY_REF",
        )
    )
    await db_session.commit()

    dummy_client = DummySupplierClient()
    provider_registry.register_singleton(str(provider.id), dummy_client)

    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    token = token_service.issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.OWNER],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )

    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    app.dependency_overrides[get_db_session] = lambda: db_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get(
            f"/api/v1/admin/providers/{provider.id}/importable-products?lang=en",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        items = res.json()
        assert len(items) == 2
        assert items[0]["external_id"] == "PROD-101"
        assert items[0]["cost"] == "1.00"
        assert items[0]["already_imported"] is False
        assert "Gemini AI" in items[0]["description"]

    app.dependency_overrides.clear()


async def test_import_catalog_creates_products_variants_and_mappings(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    from packages.telegram.secrets import EnvSecretStorage
    storage = EnvSecretStorage({"WHOLESALE_KEY_REF": "test-wholesale-key"})
    monkeypatch.setattr("apps.api.v1.admin_providers.get_default_secret_storage", lambda: storage)
    tenant = Tenant(name="Store Import Tenant", slug=f"store-imp-{uuid.uuid4().hex[:6]}")
    user = User(username=f"owner_{uuid.uuid4().hex[:6]}")
    db_session.add_all([tenant, user])
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER))
    provider = Provider(
        tenant_id=tenant.id,
        name="Wholesale Cloud",
        slug="wholesale-cloud",
        provider_type="MOCK",
        category=ProviderCategory.ACCOUNT,
        is_enabled=True,
    )
    db_session.add(provider)
    await db_session.flush()

    db_session.add(
        ProviderCredential(
            tenant_id=tenant.id,
            provider_id=provider.id,
            credential_type="API_KEY",
            secret_ref="WHOLESALE_KEY_REF",
        )
    )
    await db_session.commit()

    dummy_client = DummySupplierClient()
    provider_registry.register_singleton(str(provider.id), dummy_client)

    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)
    token = token_service.issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[Role.OWNER],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )

    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    app.dependency_overrides[get_db_session] = lambda: db_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Import both products with +20% markup and +$0.50 fixed fee
        payload = {
            "category_name": "AI Subscriptions",
            "markup_percent": 20.0,
            "markup_fixed": 0.50,
            "lang": "en",
            "activate_products": True,
        }
        res = await client.post(
            f"/api/v1/admin/providers/{provider.id}/import-catalog",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["imported_count"] == 2
        assert data["updated_count"] == 0
        assert data["category_name"] == "AI Subscriptions"

        # Verify items in database
        stmt_prod = (
            select(Product)
            .where(Product.tenant_id == tenant.id)
            .options(selectinload(Product.variants), selectinload(Product.category))
        )
        prods = list((await db_session.execute(stmt_prod)).scalars().all())
        assert len(prods) == 2

        # Check retail price math:
        # PROD-101: cost 1.00 * 1.20 + 0.50 = $1.70
        prod1 = next(p for p in prods if "Gemini" in p.title)
        assert prod1.variants[0].price == Decimal("1.70")
        assert prod1.category.name == "AI Subscriptions"

        # PROD-102: cost 5.00 * 1.20 + 0.50 = $6.50
        prod2 = next(p for p in prods if "ChatGPT" in p.title)
        assert prod2.variants[0].price == Decimal("6.50")

        # Verify ProviderProductMapping records
        stmt_map = select(ProviderProductMapping).where(ProviderProductMapping.tenant_id == tenant.id)
        mappings = list((await db_session.execute(stmt_map)).scalars().all())
        assert len(mappings) == 2
        assert {m.external_product_id for m in mappings} == {"PROD-101", "PROD-102"}

        # 2. Test Idempotent Re-Import (updates prices and counts instead of duplicating)
        reimport_payload = {
            "category_name": "AI Subscriptions",
            "markup_percent": 50.0,
            "markup_fixed": 1.00,
            "lang": "ar",
        }
        res_reimport = await client.post(
            f"/api/v1/admin/providers/{provider.id}/import-catalog",
            headers={"Authorization": f"Bearer {token}"},
            json=reimport_payload,
        )
        assert res_reimport.status_code == 200
        data_reimport = res_reimport.json()
        assert data_reimport["imported_count"] == 0
        assert data_reimport["updated_count"] == 2

        # Check updated retail price:
        # PROD-101: cost 1.00 * 1.50 + 1.00 = $2.50
        await db_session.refresh(prod1.variants[0])
        assert prod1.variants[0].price == Decimal("2.50")

        # Total products count in tenant remains 2 (no duplicates)
        count = len(list((await db_session.execute(select(Product).where(Product.tenant_id == tenant.id))).scalars().all()))
        assert count == 2

    app.dependency_overrides.clear()
