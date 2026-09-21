from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_principal, require_admin_or_owner, require_staff_or_above
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.operations.models import (
    Announcement,
    AnnouncementDelivery,
    Coupon,
    SupportCase,
    SupportMessage,
)
from packages.operations.service import audit, open_case, queue_announcement, update_case, utc
from packages.telegram.models import Bot

router = APIRouter(prefix="/admin/operations", tags=["tenant-operations"])
customer_router = APIRouter(prefix="/storefront/support", tags=["customer-support"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CaseRequest(StrictRequest):
    subject: str = Field(min_length=3, max_length=160)
    body: str = Field(min_length=1, max_length=3000)
    order_id: uuid.UUID | None = None
    warranty_item_id: uuid.UUID | None = None


class AdminCaseRequest(CaseRequest):
    user_id: uuid.UUID


class ReplyRequest(StrictRequest):
    body: str = Field(min_length=1, max_length=3000)
    expected_version: int = Field(ge=1)


class CaseUpdate(ReplyRequest):
    status: Literal["IN_PROGRESS", "APPROVED", "DECLINED", "RESOLVED"] | None = None


class CouponRequest(StrictRequest):
    code: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    percent: Decimal = Field(gt=0, le=90, max_digits=5, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    minimum_amount: Decimal = Field(default=Decimal(0), ge=0, max_digits=12, decimal_places=2)
    max_uses: int = Field(ge=1, le=1000000)
    expires_at: datetime


class ActiveRequest(StrictRequest):
    is_active: bool


class AnnouncementRequest(StrictRequest):
    bot_id: uuid.UUID
    title: str = Field(min_length=2, max_length=120)
    body: str = Field(min_length=1, max_length=3500)
    audience: Literal["ALL", "BUYERS"] = "ALL"


def serialize(row):
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name != "tenant_id"
    }


async def commit(session):
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            409, "This record already exists or changed concurrently. Refresh and retry."
        ) from exc


async def detail(session, tenant_id, case_id, customer_id=None):
    case = await session.scalar(
        select(SupportCase).where(SupportCase.tenant_id == tenant_id, SupportCase.id == case_id)
    )
    if not case or (customer_id and case.user_id != customer_id):
        raise HTTPException(404, "Case not found.")
    messages = (
        await session.scalars(
            select(SupportMessage)
            .where(SupportMessage.tenant_id == tenant_id, SupportMessage.case_id == case.id)
            .order_by(SupportMessage.created_at, SupportMessage.id)
        )
    ).all()
    return {**serialize(case), "messages": [serialize(m) for m in messages]}


@router.get("/cases")
async def cases(
    kind: Literal["SUPPORT", "WARRANTY"] | None = None,
    offset: int = Query(0, ge=0),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
):
    query = select(SupportCase).where(SupportCase.tenant_id == principal.tenant_id)
    if kind:
        query = query.where(SupportCase.kind == kind)
    return [
        serialize(row)
        for row in (
            await session.scalars(
                query.order_by(SupportCase.created_at.desc()).offset(offset).limit(100)
            )
        ).all()
    ]


@router.get("/cases/{case_id}")
async def case_detail(
    case_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
):
    return await detail(session, principal.tenant_id, case_id)


@router.post("/cases", status_code=201)
async def create_case(
    req: AdminCaseRequest,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        item = await open_case(
            session,
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            is_staff=True,
            **req.model_dump(),
        )
        await commit(session)
        return await detail(session, principal.tenant_id, item.id)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            409, "A claim already exists for this item. Refresh the case list."
        ) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/cases/{case_id}/reply")
async def reply_case(
    case_id: uuid.UUID,
    req: CaseUpdate,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
):
    if req.status in {"APPROVED", "DECLINED"} and not principal.is_admin_or_owner():
        raise HTTPException(403, "Only owners and admins can decide warranty claims.")
    try:
        await update_case(
            session,
            tenant_id=principal.tenant_id,
            case_id=case_id,
            actor_id=principal.user_id,
            expected_version=req.expected_version,
            body=req.body,
            target=req.status,
        )
        await commit(session)
        return await detail(session, principal.tenant_id, case_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@customer_router.get("")
async def customer_cases(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
):
    return [
        serialize(row)
        for row in (
            await session.scalars(
                select(SupportCase)
                .where(
                    SupportCase.tenant_id == principal.tenant_id,
                    SupportCase.user_id == principal.user_id,
                )
                .order_by(SupportCase.created_at.desc())
                .limit(100)
            )
        ).all()
    ]


@customer_router.post("", status_code=201)
async def customer_create_case(
    req: CaseRequest,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        item = await open_case(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            actor_id=principal.user_id,
            **req.model_dump(),
        )
        await commit(session)
        return await detail(session, principal.tenant_id, item.id, principal.user_id)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            409, "A claim already exists for this item. Refresh the case list."
        ) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@customer_router.get("/{case_id}")
async def customer_case_detail(
    case_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
):
    return await detail(session, principal.tenant_id, case_id, principal.user_id)


@customer_router.post("/{case_id}/reply")
async def customer_reply(
    case_id: uuid.UUID,
    req: ReplyRequest,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        await update_case(
            session,
            tenant_id=principal.tenant_id,
            case_id=case_id,
            actor_id=principal.user_id,
            customer_id=principal.user_id,
            **req.model_dump(),
        )
        await commit(session)
        return await detail(session, principal.tenant_id, case_id, principal.user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/coupons")
async def coupons(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
):
    return [
        serialize(row)
        for row in (
            await session.scalars(
                select(Coupon)
                .where(Coupon.tenant_id == principal.tenant_id)
                .order_by(Coupon.created_at.desc())
                .limit(200)
            )
        ).all()
    ]


@router.post("/coupons", status_code=201)
async def create_coupon(
    req: CouponRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
):
    if utc(req.expires_at) <= datetime.now(UTC):
        raise HTTPException(400, "Expiry must be in the future.")
    item = Coupon(tenant_id=principal.tenant_id, **{**req.model_dump(), "code": req.code.upper()})
    session.add(item)
    try:
        await session.flush()
        audit(session, principal.tenant_id, principal.user_id, "coupon.created", item)
        await commit(session)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Coupon code already exists.") from exc
    return serialize(item)


@router.patch("/coupons/{coupon_id}")
async def toggle_coupon(
    coupon_id: uuid.UUID,
    req: ActiveRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
):
    item = await session.scalar(
        select(Coupon)
        .where(Coupon.id == coupon_id, Coupon.tenant_id == principal.tenant_id)
        .with_for_update()
    )
    if not item:
        raise HTTPException(404, "Coupon not found.")
    item.is_active = req.is_active
    audit(
        session,
        principal.tenant_id,
        principal.user_id,
        "coupon.status_changed",
        item,
        req.model_dump(),
    )
    await commit(session)
    return serialize(item)


@router.get("/announcements")
async def announcements(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
):
    rows = (
        await session.scalars(
            select(Announcement)
            .where(Announcement.tenant_id == principal.tenant_id)
            .order_by(Announcement.created_at.desc())
            .limit(100)
        )
    ).all()
    results = []
    for row in rows:
        counts = (
            await session.execute(
                select(AnnouncementDelivery.status, func.count())
                .where(
                    AnnouncementDelivery.tenant_id == principal.tenant_id,
                    AnnouncementDelivery.announcement_id == row.id,
                )
                .group_by(AnnouncementDelivery.status)
            )
        ).all()
        results.append({**serialize(row), "deliveries": dict(counts)})
    return results


@router.post("/announcements", status_code=201)
async def create_announcement(
    req: AnnouncementRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
):
    bot = await session.scalar(
        select(Bot.id).where(
            Bot.id == req.bot_id, Bot.tenant_id == principal.tenant_id, Bot.deleted_at.is_(None)
        )
    )
    if not bot:
        raise HTTPException(404, "Bot not found.")
    item = Announcement(tenant_id=principal.tenant_id, **req.model_dump())
    session.add(item)
    await session.flush()
    audit(session, principal.tenant_id, principal.user_id, "announcement.created", item)
    await commit(session)
    return serialize(item)


@router.post("/announcements/{announcement_id}/queue")
async def send_announcement(
    announcement_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        item = await queue_announcement(
            session,
            tenant_id=principal.tenant_id,
            announcement_id=announcement_id,
            actor_id=principal.user_id,
        )
        await commit(session)
        return serialize(item)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/announcements/{announcement_id}/cancel")
async def cancel_announcement(
    announcement_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
):
    item = await session.scalar(
        select(Announcement)
        .where(Announcement.tenant_id == principal.tenant_id, Announcement.id == announcement_id)
        .with_for_update()
    )
    if not item or item.status not in {"DRAFT", "QUEUED"}:
        raise HTTPException(409, "Only drafts and queued campaigns can be cancelled.")
    item.status = "CANCELLED"
    await session.execute(
        update(AnnouncementDelivery)
        .where(
            AnnouncementDelivery.tenant_id == principal.tenant_id,
            AnnouncementDelivery.announcement_id == item.id,
            AnnouncementDelivery.status == "QUEUED",
        )
        .values(status="CANCELLED")
    )
    audit(session, principal.tenant_id, principal.user_id, "announcement.cancelled", item)
    await commit(session)
    return serialize(item)
