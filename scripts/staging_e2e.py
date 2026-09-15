#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import select

from packages.commerce.models import Category, Product, ProductVariant
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.config import settings
from packages.core.database import async_session_factory
from packages.payments.service import LedgerService
from packages.tenants.models import Membership, Role, Tenant, User

SLUG = "staging-smoke"


async def seed() -> tuple[str, uuid.UUID]:
    if settings.app_env != "staging":
        raise SystemExit("staging_e2e.py refuses to run unless APP_ENV=staging")
    async with async_session_factory() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == SLUG))).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name="Staging Smoke", slug=SLUG, is_active=True, settings={})
            session.add(tenant)
            await session.flush()

        membership = (
            await session.execute(
                select(Membership).where(Membership.tenant_id == tenant.id, Membership.role == Role.CUSTOMER)
            )
        ).scalars().first()
        if membership is None:
            user = User(username="staging_smoke", first_name="Staging", is_active=True)
            session.add(user)
            await session.flush()
            membership = Membership(tenant_id=tenant.id, user_id=user.id, role=Role.CUSTOMER, is_active=True)
            session.add(membership)
            await session.flush()
        else:
            user = await session.get(User, membership.user_id)
            assert user is not None

        category = (
            await session.execute(select(Category).where(Category.tenant_id == tenant.id, Category.slug == "smoke"))
        ).scalar_one_or_none()
        if category is None:
            category = Category(tenant_id=tenant.id, name="Smoke", slug="smoke", is_active=True)
            session.add(category)
            await session.flush()

        product = (
            await session.execute(select(Product).where(Product.tenant_id == tenant.id, Product.title == "Smoke Product"))
        ).scalars().first()
        if product is None:
            product = Product(tenant_id=tenant.id, category_id=category.id, title="Smoke Product", is_active=True, metadata_json={})
            session.add(product)
            await session.flush()

        variant = (
            await session.execute(select(ProductVariant).where(ProductVariant.product_id == product.id, ProductVariant.sku == "SMOKE-1"))
        ).scalar_one_or_none()
        if variant is None:
            variant = ProductVariant(product_id=product.id, sku="SMOKE-1", title="Standard", price=Decimal("1.00"), currency="USD", stock_quantity=999, is_active=True, attributes={})
            session.add(variant)
            await session.flush()

        wallet = await LedgerService.get_or_create_wallet(session, tenant.id, user.id, "USD")
        if wallet.balance < Decimal("20.00"):
            await LedgerService.credit(session, wallet, Decimal("20.00") - wallet.balance, reference_id=f"staging-seed-{uuid.uuid4().hex}", reference_type="STAGING_SMOKE")
        await session.commit()

        token = AuthTokenService().issue_access_token(
            user_id=user.id,
            tenant_id=tenant.id,
            roles=[Role.CUSTOMER],
            source=AuthSource.TEST,
            token_version=user.token_version,
            expires_in_seconds=600,
        )
        return token, variant.id


async def main() -> None:
    token, variant_id = await seed()
    base_url = os.getenv("STAGING_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    key = f"staging-{uuid.uuid4().hex}"
    payload = {
        "items": [{"variant_id": str(variant_id), "quantity": 1}],
        "recipient": "staging-smoke@example.invalid",
        "idempotency_key": key,
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
        ready = await client.get("/health/ready")
        ready.raise_for_status()
        bootstrap = await client.get("/api/v1/storefront/bootstrap", headers=headers)
        bootstrap.raise_for_status()
        catalog = await client.get("/api/v1/storefront/catalog", headers=headers)
        catalog.raise_for_status()
        first = await client.post("/api/v1/storefront/checkout", headers=headers, json=payload)
        first.raise_for_status()
        second = await client.post("/api/v1/storefront/checkout", headers=headers, json=payload)
        second.raise_for_status()
        if first.json()["id"] != second.json()["id"]:
            raise SystemExit("checkout idempotency failed in staging smoke test")
        orders = await client.get("/api/v1/storefront/orders", headers=headers)
        orders.raise_for_status()
        if not any(item["id"] == first.json()["id"] for item in orders.json()):
            raise SystemExit("created order was not visible through storefront orders API")
    print("staging E2E smoke passed")


if __name__ == "__main__":
    asyncio.run(main())
