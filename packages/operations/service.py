from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Order, OrderItem
from packages.commerce.state_machine import OrderStatus
from packages.operations.models import (
    Announcement,
    AnnouncementDelivery,
    Coupon,
    CouponRedemption,
    SupportCase,
    SupportMessage,
)
from packages.telegram.models import Bot, TenantTelegramUser
from packages.tenants.models import AuditLog, Membership


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def audit(session, tenant_id, actor_id, action, resource, details=None):
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=action,
            resource_type=resource.__tablename__,
            resource_id=str(resource.id),
            details=details or {},
        )
    )


async def tenant_user(session, tenant_id, user_id):
    member = await session.scalar(
        select(Membership.id).where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == user_id,
            Membership.is_active.is_(True),
        )
    )
    binding = await session.scalar(
        select(TenantTelegramUser.id).where(
            TenantTelegramUser.tenant_id == tenant_id,
            TenantTelegramUser.user_id == user_id,
            TenantTelegramUser.is_blocked.is_(False),
        )
    )
    if not member and not binding:
        raise ValueError("Customer not found in this store.")


async def open_case(
    session,
    *,
    tenant_id,
    user_id,
    actor_id,
    subject,
    body,
    order_id=None,
    warranty_item_id=None,
    is_staff=False,
):
    await tenant_user(session, tenant_id, user_id)
    order = None
    if order_id:
        order = await session.scalar(
            select(Order).where(
                Order.id == order_id, Order.tenant_id == tenant_id, Order.user_id == user_id
            )
        )
        if not order:
            raise ValueError("Order not found for this customer.")
    terms = None
    if warranty_item_id:
        if not order or order.status != OrderStatus.FULFILLED:
            raise ValueError(
                "Warranty claims require a fulfilled order belonging to this customer."
            )
        # Serialize claims for the order as well as enforcing unique item identity in SQL.
        await session.scalar(
            select(Order.id)
            .where(Order.id == order.id, Order.tenant_id == tenant_id)
            .with_for_update()
        )
        existing = await session.scalar(
            select(SupportCase).where(
                SupportCase.tenant_id == tenant_id, SupportCase.warranty_item_id == warranty_item_id
            )
        )
        if existing:
            return existing
        item = await session.scalar(
            select(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.tenant_id == tenant_id,
                OrderItem.id == warranty_item_id,
                OrderItem.order_id == order.id,
            )
        )
        snapshot = item.sale_terms if item else {}
        days = snapshot.get("warranty_days", 0)
        if (
            not isinstance(days, int)
            or not 1 <= days <= 3650
            or datetime.now(UTC) > utc(order.created_at) + timedelta(days=days)
        ):
            raise ValueError(
                "This item has no eligible purchase-time warranty or its warranty has expired."
            )
        terms = snapshot.get("warranty_terms", "")
    case = SupportCase(
        tenant_id=tenant_id,
        user_id=user_id,
        order_id=order_id,
        warranty_item_id=warranty_item_id,
        kind="WARRANTY" if warranty_item_id else "SUPPORT",
        subject=subject,
        warranty_terms=terms,
    )
    session.add(case)
    await session.flush()
    session.add(
        SupportMessage(
            tenant_id=tenant_id, case_id=case.id, author_id=actor_id, body=body, is_staff=is_staff
        )
    )
    audit(session, tenant_id, actor_id, "care.case_opened", case)
    return case


async def update_case(
    session, *, tenant_id, case_id, actor_id, expected_version, body, target=None, customer_id=None
):
    case = await session.scalar(
        select(SupportCase)
        .where(SupportCase.tenant_id == tenant_id, SupportCase.id == case_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not case or (customer_id and case.user_id != customer_id):
        raise ValueError("Case not found.")
    if case.version != expected_version:
        raise ValueError("This case changed. Refresh it before replying.")
    transitions = {
        "OPEN": {"IN_PROGRESS", "APPROVED", "DECLINED", "RESOLVED"},
        "IN_PROGRESS": {"APPROVED", "DECLINED", "RESOLVED"},
        "APPROVED": {"RESOLVED"},
        "DECLINED": set(),
        "RESOLVED": set(),
    }
    if case.status in {"RESOLVED", "DECLINED"}:
        raise ValueError("This case is closed. Open a support case for further assistance.")
    if target:
        if customer_id or target not in transitions.get(case.status, set()):
            raise ValueError("Invalid case transition.")
        if target in {"APPROVED", "DECLINED"} and case.kind != "WARRANTY":
            raise ValueError("Approval and decline apply to warranty claims only.")
    result = await session.execute(
        update(SupportCase)
        .where(
            SupportCase.id == case.id,
            SupportCase.tenant_id == tenant_id,
            SupportCase.version == expected_version,
        )
        .values(
            version=expected_version + 1,
            status=target or case.status,
            resolution=body if target in {"RESOLVED", "DECLINED"} else case.resolution,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ValueError("This case changed. Refresh it before replying.")
    session.add(
        SupportMessage(
            tenant_id=tenant_id,
            case_id=case.id,
            author_id=actor_id,
            body=body,
            is_staff=customer_id is None,
        )
    )
    audit(
        session, tenant_id, actor_id, "care.case_updated", case, {"status": target or case.status}
    )
    await session.refresh(case)
    return case


async def apply_coupon(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    order: Order,
    code: str,
    prices: dict,
    quantities: dict,
):
    coupon = await session.scalar(
        select(Coupon)
        .where(Coupon.tenant_id == tenant_id, Coupon.code == code)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = datetime.now(UTC)
    if (
        not coupon
        or not coupon.is_active
        or utc(coupon.expires_at) <= now
        or coupon.currency != order.currency
        or order.total_amount < coupon.minimum_amount
    ):
        raise ValueError("Coupon is unavailable for this purchase.")
    updated = await session.execute(
        update(Coupon)
        .where(
            Coupon.tenant_id == tenant_id,
            Coupon.id == coupon.id,
            Coupon.used_count < Coupon.max_uses,
            Coupon.is_active.is_(True),
        )
        .values(used_count=Coupon.used_count + 1)
    )
    if updated.rowcount != 1:
        raise ValueError("Coupon usage limit reached.")
    # Discount each unit downward to whole currency cents. A payable unit remains >= 0.01.
    effective = {
        key: max(
            Decimal("0.01"),
            price - (price * coupon.percent / 100).quantize(Decimal("0.01"), rounding=ROUND_DOWN),
        )
        for key, price in prices.items()
    }
    discounted_total = sum((effective[key] * quantities[key] for key in effective), Decimal(0))
    discount = order.total_amount - discounted_total
    if discount <= 0:
        raise ValueError("Coupon does not produce a discount for this purchase.")
    session.add(
        CouponRedemption(
            tenant_id=tenant_id,
            coupon_id=coupon.id,
            order_id=order.id,
            user_id=user_id,
            code=coupon.code,
            discount_amount=discount,
            currency=order.currency,
        )
    )
    order.total_amount = discounted_total
    return effective


async def queue_announcement(session, *, tenant_id, announcement_id, actor_id):
    item = await session.scalar(
        select(Announcement)
        .where(Announcement.id == announcement_id, Announcement.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not item or item.status != "DRAFT":
        raise ValueError("Only a draft announcement can be queued.")
    bot = await session.scalar(
        select(Bot).where(
            Bot.id == item.bot_id,
            Bot.tenant_id == tenant_id,
            Bot.is_enabled.is_(True),
            Bot.deleted_at.is_(None),
        )
    )
    if not bot:
        raise ValueError("Choose an enabled bot belonging to this store.")
    query = select(TenantTelegramUser.id).where(
        TenantTelegramUser.tenant_id == tenant_id,
        TenantTelegramUser.bot_id == item.bot_id,
        TenantTelegramUser.is_blocked.is_(False),
        TenantTelegramUser.telegram_user_id > 0,
    )
    if item.audience == "BUYERS":
        query = query.where(
            select(Order.id)
            .where(
                Order.tenant_id == tenant_id,
                Order.user_id == TenantTelegramUser.user_id,
                Order.status == OrderStatus.FULFILLED,
            )
            .exists()
        )
    # Bound one campaign; do not silently send to a truncated audience.
    recipients = list((await session.scalars(query.limit(10001))).all())
    if not recipients or len(recipients) > 10000:
        raise ValueError("The selected audience must contain between 1 and 10,000 reachable users.")
    now = datetime.now(UTC)
    session.add_all(
        [
            AnnouncementDelivery(
                tenant_id=tenant_id,
                announcement_id=item.id,
                binding_id=binding,
                next_attempt_at=now,
            )
            for binding in recipients
        ]
    )
    item.status = "QUEUED"
    item.queued_at = now
    audit(
        session,
        tenant_id,
        actor_id,
        "announcement.queued",
        item,
        {"recipients": len(recipients), "audience": item.audience},
    )
    return item
