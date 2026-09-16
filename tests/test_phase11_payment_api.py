from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_principal
from apps.api.main import app
from packages.core.auth import AuthenticatedPrincipal, AuthSource
from packages.core.database import get_db_session
from packages.payments.models import Wallet
from packages.tenants.models import Role, Tenant, User

pytestmark = pytest.mark.asyncio


async def test_manual_payment_api_flow_never_credits_before_admin_approval(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(name="Phase 11 API", slug=f"phase11-api-{uuid.uuid4().hex[:8]}", is_active=True)
    customer = User(username=f"p11_customer_{uuid.uuid4().hex[:8]}", is_active=True)
    admin = User(username=f"p11_admin_{uuid.uuid4().hex[:8]}", is_active=True)
    db_session.add_all([tenant, customer, admin])
    await db_session.flush()

    holder = {
        "principal": AuthenticatedPrincipal(
            user_id=admin.id,
            tenant_id=tenant.id,
            source=AuthSource.TEST,
            roles=frozenset({Role.ADMIN}),
        )
    }

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_principal() -> AuthenticatedPrincipal:
        return holder["principal"]

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_principal] = override_principal
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/admin/payments/methods",
                json={
                    "code": "manual-usdt",
                    "display_name": "Manual USDT",
                    "method_type": "MANUAL_TRANSFER",
                    "verification_mode": "MANUAL",
                    "asset": "USDT",
                    "network": "TRON",
                    "destination_address": "TADMINADDRESS111111111111111111111",
                    "instructions": "Send USDT then submit your transaction reference.",
                    "requires_admin_approval": True,
                    "settings": {
                        "topup_min_amount": "5.00",
                        "topup_max_amount": "500.00",
                        "topup_currencies": ["USD"],
                    },
                },
            )
            assert created.status_code == 201, created.text
            method_id = created.json()["id"]

            holder["principal"] = AuthenticatedPrincipal(
                user_id=customer.id,
                tenant_id=tenant.id,
                source=AuthSource.TEST,
                roles=frozenset({Role.CUSTOMER}),
            )
            options = await client.get("/api/v1/storefront/wallet/payment-methods")
            assert options.status_code == 200, options.text
            assert options.json()["methods"][0]["code"] == "manual-usdt"

            topup = await client.post(
                "/api/v1/storefront/wallet/topups/local",
                json={
                    "amount": "25.00",
                    "currency": "USD",
                    "payment_method_id": method_id,
                    "idempotency_key": "phase11-api-topup-0001",
                },
            )
            assert topup.status_code == 201, topup.text
            intent_id = topup.json()["id"]
            assert topup.json()["status"] == "PENDING"

            submitted = await client.post(
                f"/api/v1/storefront/wallet/topups/{intent_id}/observations",
                json={
                    "source": "MANUAL",
                    "external_reference": "tx-user-claimed-001",
                    "asset_amount": "25.0",
                    "note": "paid",
                },
            )
            assert submitted.status_code == 201, submitted.text
            observation_id = submitted.json()["id"]
            assert submitted.json()["status"] == "MANUAL_REVIEW"

            wallet_before = (
                await db_session.execute(
                    select(Wallet).where(Wallet.tenant_id == tenant.id, Wallet.user_id == customer.id)
                )
            ).scalar_one_or_none()
            assert wallet_before is None

            holder["principal"] = AuthenticatedPrincipal(
                user_id=admin.id,
                tenant_id=tenant.id,
                source=AuthSource.TEST,
                roles=frozenset({Role.ADMIN}),
            )
            approved = await client.post(
                f"/api/v1/admin/payments/observations/{observation_id}/approve",
                json={},
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == "VERIFIED"

            wallet_after = (
                await db_session.execute(
                    select(Wallet).where(Wallet.tenant_id == tenant.id, Wallet.user_id == customer.id)
                )
            ).scalar_one()
            assert wallet_after.balance == Decimal("25.00")
    finally:
        app.dependency_overrides.clear()


async def test_payment_method_api_rejects_secret_material(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Phase 11 Secret API", slug=f"phase11-secret-{uuid.uuid4().hex[:8]}", is_active=True)
    admin = User(username=f"p11_secret_admin_{uuid.uuid4().hex[:8]}", is_active=True)
    db_session.add_all([tenant, admin])
    await db_session.flush()

    principal = AuthenticatedPrincipal(
        user_id=admin.id,
        tenant_id=tenant.id,
        source=AuthSource.TEST,
        roles=frozenset({Role.ADMIN}),
    )

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_principal() -> AuthenticatedPrincipal:
        return principal

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_principal] = override_principal
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/admin/payments/methods",
                json={
                    "code": "unsafe-provider",
                    "display_name": "Unsafe Provider",
                    "method_type": "CRYPTO_GATEWAY",
                    "verification_mode": "PROVIDER_RECONCILIATION",
                    "provider_name": "example",
                    "settings": {"api_token": "do-not-store-this"},
                },
            )
            assert response.status_code == 409, response.text
            assert "credential-like" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
