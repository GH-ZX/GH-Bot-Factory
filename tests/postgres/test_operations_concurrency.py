import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from packages.commerce.checkout import CheckoutService
from packages.commerce.models import Order, Product, ProductVariant
from packages.operations.models import Announcement, AnnouncementDelivery, Coupon, CouponRedemption
from packages.operations.service import queue_announcement
from packages.operations.worker import AnnouncementWorker
from packages.payments.models import Wallet
from packages.payments.service import LedgerService
from packages.telegram.models import Bot, TenantTelegramUser
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def seed(factory):
    async with factory() as session:
        tenant, user = Tenant(name="Race", slug="ops-race"), User(username="race-buyer")
        session.add_all([tenant, user])
        await session.flush()
        session.add(
            Membership(tenant_id=tenant.id, user_id=user.id, role=Role.CUSTOMER, is_active=True)
        )
        product = Product(tenant_id=tenant.id, title="Gift")
        session.add(product)
        await session.flush()
        variant = ProductVariant(
            product_id=product.id,
            sku="RACE",
            title="Gift",
            price=Decimal(20),
            currency="USD",
            stock_quantity=100,
        )
        session.add(variant)
        session.add(
            Coupon(
                tenant_id=tenant.id,
                code="LAST",
                percent=Decimal(10),
                currency="USD",
                max_uses=1,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        wallet = await LedgerService.get_or_create_wallet(
            session, tenant_id=tenant.id, user_id=user.id, currency="USD"
        )
        await LedgerService.credit(
            session, wallet, Decimal(100), description="Test opening balance"
        )
        await session.commit()
        return tenant.id, user.id, variant.id


@pytest.mark.parametrize("same_key", [True, False])
async def test_last_coupon_use_and_checkout_idempotency(postgres_session_factory, same_key):
    factory = postgres_session_factory
    tenant_id, user_id, variant_id = await seed(factory)

    async def purchase(key):
        async with factory() as session:
            try:
                order, _ = await CheckoutService().checkout(
                    session,
                    tenant_id,
                    user_id,
                    variant_id,
                    1,
                    "buyer",
                    execute_sync=False,
                    enqueue_durable=True,
                    idempotency_key=key,
                    coupon_code="LAST",
                )
                await session.commit()
                return order.id
            except ValueError as exc:
                await session.rollback()
                assert "usage limit" in str(exc)
                return None

    results = await asyncio.wait_for(
        asyncio.gather(purchase("first"), purchase("first" if same_key else "second")), timeout=20
    )
    assert (results[0] == results[1]) if same_key else (results.count(None) == 1)
    async with factory() as session:
        assert (
            await session.scalar(select(Coupon.used_count).where(Coupon.tenant_id == tenant_id))
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CouponRedemption)
                .where(CouponRedemption.tenant_id == tenant_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(Order).where(Order.tenant_id == tenant_id)
            )
            == 1
        )
        assert await session.scalar(
            select(Wallet.balance).where(Wallet.tenant_id == tenant_id)
        ) == Decimal(82)


async def test_two_announcement_workers_claim_once(postgres_session_factory):
    factory = postgres_session_factory
    tenant_id, user_id, _ = await seed(factory)
    async with factory() as session:
        bot = Bot(
            tenant_id=tenant_id,
            telegram_bot_id=777888999,
            display_name="Test",
            token_secret_ref="test",
            is_enabled=True,
        )
        session.add(bot)
        await session.flush()
        session.add(
            TenantTelegramUser(
                tenant_id=tenant_id,
                bot_id=bot.id,
                user_id=user_id,
                telegram_user_id=123123123,
                is_blocked=False,
            )
        )
        campaign = Announcement(tenant_id=tenant_id, bot_id=bot.id, title="Test", body="Hello")
        session.add(campaign)
        await session.flush()
        await queue_announcement(
            session, tenant_id=tenant_id, announcement_id=campaign.id, actor_id=user_id
        )
        await session.commit()
    sends = []

    async def sender(*args):
        sends.append(1)
        await asyncio.sleep(0.05)
        return 42

    await asyncio.wait_for(
        asyncio.gather(
            AnnouncementWorker(factory, sender).run_once(),
            AnnouncementWorker(factory, sender).run_once(),
        ),
        timeout=20,
    )
    assert len(sends) == 1
    async with factory() as session:
        assert (
            await session.scalar(
                select(AnnouncementDelivery.status).where(
                    AnnouncementDelivery.tenant_id == tenant_id
                )
            )
            == "SENT"
        )


async def test_quote_acceptance_serializes_sibling_versions(postgres_session_factory):
    import httpx

    from apps.api.main import app
    from packages.core.config import settings
    from packages.core.database import get_db_session
    from packages.marketplace.models import (
        CommercialQuote,
        ContactMethod,
        CustomerInquiry,
        QuoteStatus,
    )

    factory = postgres_session_factory
    async with factory() as session:
        inquiry = CustomerInquiry(contact_method=ContactMethod.TELEGRAM, contact_handle="@race", configuration={"delivery_model":"dedicated"}, estimated_quote={})
        session.add(inquiry)
        await session.flush()
        quotes = [CommercialQuote(inquiry_id=inquiry.id, quote_number="Q-RACE", version=v, customer_name="Buyer", customer_contact="@race", currency="USD", total_one_time=Decimal(100), total_monthly=Decimal(0), status=QuoteStatus.DRAFT) for v in [1, 2]]
        session.add_all(quotes)
        await session.commit()
        ids, inquiry_id = [q.id for q in quotes], inquiry.id
    async def db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    token = "test-operations-platform-token-01234567890123456789"
    old_token = settings.platform_admin_token
    settings.platform_admin_token = token
    app.dependency_overrides[get_db_session] = db
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def accept(quote_id):
                return await client.post(f"/api/v1/platform/sales/quotes/{quote_id}/accept", headers={"X-GHBF-Platform-Token":token})
            responses = await asyncio.wait_for(asyncio.gather(*(accept(q) for q in ids)), timeout=20)
            assert sorted(r.status_code for r in responses) == [200, 409]
        async with factory() as session:
            statuses = list((await session.scalars(select(CommercialQuote.status).where(CommercialQuote.inquiry_id == inquiry_id))).all())
            assert set(statuses) == {QuoteStatus.ACCEPTED, QuoteStatus.SUPERSEDED}
    finally:
        app.dependency_overrides.clear()
        settings.platform_admin_token = old_token
