from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from apps.api.deps import get_current_principal
from apps.api.main import app
from packages.commerce.checkout import CheckoutService
from packages.commerce.models import OrderItem, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.core.auth import AuthenticatedPrincipal, AuthSource
from packages.core.database import get_db_session
from packages.operations.models import (
    Announcement,
    AnnouncementDelivery,
    Coupon,
    CouponRedemption,
    SupportMessage,
)
from packages.operations.service import open_case, queue_announcement, update_case
from packages.operations.worker import AnnouncementWorker
from packages.payments.models import Wallet
from packages.payments.service import LedgerService
from packages.telegram.models import Bot, TenantTelegramUser
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio


async def seed(session):
    tenant = Tenant(name="Operations", slug="operations")
    other = Tenant(name="Other", slug="other")
    user = User(username="buyer")
    session.add_all([tenant, other, user])
    await session.flush()
    session.add(
        Membership(tenant_id=tenant.id, user_id=user.id, role=Role.CUSTOMER, is_active=True)
    )
    product = Product(
        tenant_id=tenant.id,
        title="Gift",
        metadata_json={"warranty_days": 30, "warranty_terms": "Replacement on verified failure"},
    )
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku="GIFT",
        title="Gift",
        price=Decimal(20),
        currency="USD",
        stock_quantity=100,
    )
    coupon = Coupon(
        tenant_id=tenant.id,
        code="SAVE",
        percent=Decimal(10),
        currency="USD",
        max_uses=1,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=123456789,
        display_name="Store",
        token_secret_ref="test-ref",
        is_enabled=True,
    )
    session.add_all([variant, coupon, bot])
    wallet = await LedgerService.get_or_create_wallet(
        session, tenant_id=tenant.id, user_id=user.id, currency="USD"
    )
    await LedgerService.credit(session, wallet, Decimal(100), description="Test opening balance")
    await session.flush()
    binding = TenantTelegramUser(
        tenant_id=tenant.id,
        bot_id=bot.id,
        user_id=user.id,
        telegram_user_id=111222333,
        is_blocked=False,
    )
    session.add(binding)
    await session.commit()
    return tenant, other, user, variant, coupon, bot


async def purchase(session, tenant, user, variant, key="checkout", code="save"):
    return (
        await CheckoutService().checkout(
            session,
            tenant_id=tenant.id,
            user_id=user.id,
            variant_id=variant.id,
            quantity=2,
            recipient="buyer",
            execute_sync=False,
            enqueue_durable=True,
            idempotency_key=key,
            coupon_code=code,
        )
    )[0]


async def test_coupon_checkout_discount_idempotency_and_purchase_terms(db_session):
    tenant, _, user, variant, coupon, _ = await seed(db_session)
    order = await purchase(db_session, tenant, user, variant)
    await db_session.commit()
    assert order.total_amount == Decimal(36)
    assert (await purchase(db_session, tenant, user, variant)).id == order.id
    assert await db_session.scalar(
        select(Wallet.balance).where(Wallet.tenant_id == tenant.id)
    ) == Decimal(64)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(CouponRedemption)
            .where(CouponRedemption.tenant_id == tenant.id)
        )
        == 1
    )
    item = await db_session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
    assert item.unit_price == Decimal(18)
    assert item.sale_terms["warranty_days"] == 30
    assert Decimal(item.sale_terms["base_unit_price"]) == Decimal(20)
    with pytest.raises(ValueError, match="different"):
        await purchase(db_session, tenant, user, variant, code=None)
    with pytest.raises(ValueError, match="usage limit"):
        await purchase(db_session, tenant, user, variant, key="second")
    await db_session.rollback()
    await db_session.refresh(coupon)
    assert coupon.used_count == 1


async def test_warranty_requires_owned_eligible_order_and_versions(db_session):
    tenant, other, user, variant, _, _ = await seed(db_session)
    order = await purchase(db_session, tenant, user, variant, code=None)
    item = await db_session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
    args = {
        "tenant_id": tenant.id,
        "user_id": user.id,
        "actor_id": user.id,
        "order_id": order.id,
        "warranty_item_id": item.id,
        "subject": "Failed product",
        "body": "Please help",
    }
    with pytest.raises(ValueError, match="fulfilled"):
        await open_case(db_session, **args)
    order.status = OrderStatus.FULFILLED
    await db_session.flush()
    case = await open_case(db_session, **args)
    assert (await open_case(db_session, **args)).id == case.id
    assert case.warranty_terms == "Replacement on verified failure"
    await update_case(
        db_session,
        tenant_id=tenant.id,
        case_id=case.id,
        actor_id=user.id,
        expected_version=1,
        body="Approved after review",
        target="APPROVED",
    )
    with pytest.raises(ValueError, match="changed"):
        await update_case(
            db_session,
            tenant_id=tenant.id,
            case_id=case.id,
            actor_id=user.id,
            expected_version=1,
            body="Stale reply",
        )
    with pytest.raises(ValueError, match="not found"):
        await update_case(
            db_session,
            tenant_id=other.id,
            case_id=case.id,
            actor_id=user.id,
            expected_version=2,
            body="Foreign reply",
        )
    assert await db_session.scalar(
        select(Wallet.balance).where(Wallet.tenant_id == tenant.id)
    ) == Decimal(60)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(SupportMessage)
            .where(SupportMessage.tenant_id == tenant.id)
        )
        == 2
    )


async def test_operations_api_rbac_tenant_isolation_and_metadata(db_session):
    tenant, other, user, variant, _, _ = await seed(db_session)
    principal = AuthenticatedPrincipal(
        user_id=user.id,
        tenant_id=tenant.id,
        source=AuthSource.TEST,
        roles=frozenset({Role.CUSTOMER}),
    )
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_db_session] = lambda: db_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            assert (await client.get("/api/v1/admin/operations/coupons")).status_code == 403
            case = await client.post(
                "/api/v1/storefront/support", json={"subject": "Help please", "body": "Question"}
            )
            assert case.status_code == 201, case.text
            principal = AuthenticatedPrincipal(
                user_id=user.id,
                tenant_id=other.id,
                source=AuthSource.TEST,
                roles=frozenset({Role.OWNER}),
            )
            assert (
                await client.get(f"/api/v1/admin/operations/cases/{case.json()['id']}")
            ).status_code == 404
            principal = AuthenticatedPrincipal(
                user_id=user.id,
                tenant_id=tenant.id,
                source=AuthSource.TEST,
                roles=frozenset({Role.STAFF}),
            )
            denied = await client.post(
                f"/api/v1/admin/operations/cases/{case.json()['id']}/reply",
                json={"body": "Approved", "status": "APPROVED", "expected_version": 1},
            )
            assert denied.status_code == 403
            principal = AuthenticatedPrincipal(
                user_id=user.id,
                tenant_id=tenant.id,
                source=AuthSource.TEST,
                roles=frozenset({Role.OWNER}),
            )
            for metadata in [
                {"image_url": "javascript:alert(1)"},
                {"warranty_days": -1},
                {"warranty_days": 30},
            ]:
                response = await client.patch(
                    f"/api/v1/admin/catalog/products/{variant.product_id}",
                    json={"metadata": metadata},
                )
                assert response.status_code == 422, response.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("outcome", ["sent", "ambiguous", "blocked"])
async def test_announcements_are_durable_and_never_repeat_unknown(db_session_factory, outcome):
    async with db_session_factory() as session:
        tenant, _, user, _, _, bot = await seed(session)
        campaign = Announcement(tenant_id=tenant.id, bot_id=bot.id, title="News", body="Hello")
        session.add(campaign)
        await session.flush()
        await queue_announcement(
            session, tenant_id=tenant.id, announcement_id=campaign.id, actor_id=user.id
        )
        await session.commit()
        if outcome == "blocked":
            binding = await session.scalar(
                select(TenantTelegramUser).where(TenantTelegramUser.tenant_id == tenant.id)
            )
            binding.is_blocked = True
            await session.commit()
    sends = []

    async def sender(bot, binding, announcement):
        sends.append(binding.id)
        if outcome == "ambiguous":
            raise TimeoutError("upstream may have accepted")
        return 77

    worker = AnnouncementWorker(db_session_factory, sender=sender)
    assert await worker.run_once()
    assert not await worker.run_once()
    assert len(sends) == (0 if outcome == "blocked" else 1)
    async with db_session_factory() as session:
        delivery = await session.scalar(
            select(AnnouncementDelivery).where(AnnouncementDelivery.tenant_id == tenant.id)
        )
        assert (
            delivery.status
            == {"sent": "SENT", "ambiguous": "UNKNOWN", "blocked": "CANCELLED"}[outcome]
        )
        parent = await session.scalar(
            select(Announcement).where(Announcement.tenant_id == tenant.id)
        )
        assert parent.status == ("NEEDS_REVIEW" if outcome == "ambiguous" else "COMPLETED")


async def test_interrupted_send_is_reviewed_without_retry(db_session_factory):
    async with db_session_factory() as session:
        tenant, _, user, _, _, bot = await seed(session)
        campaign = Announcement(tenant_id=tenant.id, bot_id=bot.id, title="News", body="Hello")
        session.add(campaign)
        await session.flush()
        await queue_announcement(
            session, tenant_id=tenant.id, announcement_id=campaign.id, actor_id=user.id
        )
        await session.flush()
        delivery = await session.scalar(
            select(AnnouncementDelivery).where(AnnouncementDelivery.tenant_id == tenant.id)
        )
        delivery.status = "RUNNING"
        delivery.updated_at = datetime.now(UTC) - timedelta(minutes=10)
        await session.commit()

    async def sender(*args):
        raise AssertionError("Interrupted sends must never be repeated")

    assert not await AnnouncementWorker(db_session_factory, sender).run_once()
    async with db_session_factory() as session:
        assert (
            await session.scalar(
                select(Announcement.status).where(Announcement.tenant_id == tenant.id)
            )
            == "NEEDS_REVIEW"
        )
