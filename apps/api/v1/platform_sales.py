from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.platform_deps import PlatformOperator, require_platform_operator
from packages.core.database import get_db_session
from packages.marketplace.governance import CommercialGovernanceService
from packages.marketplace.handoff_service import DeploymentHandoffService, HandoffError
from packages.marketplace.integrations_service import (
    IntegrationMarketplaceError,
    IntegrationMarketplaceService,
)
from packages.marketplace.models import (
    CommercialQuote,
    CommercialQuoteLine,
    CustomerInquiry,
    DeploymentHandoff,
    HandoffStatus,
    InquiryStatus,
    LicenseType,
    QuoteStatus,
)
from packages.marketplace.onboarding import (
    CustomerOnboardingService,
    OnboardingError,
    OnboardingUnavailableError,
)
from packages.marketplace.quotes import QuoteEngine
from packages.saas.control_plane import append_platform_audit

router = APIRouter(prefix="/platform/sales", tags=["platform-sales"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


class InquiryItemResponse(BaseModel):
    id: uuid.UUID
    contact_method: str
    contact_handle: str
    project_notes: str | None
    status: str
    format: str | None
    template_key: str | None
    product_source: str | None
    delivery_model: str | None
    total_one_time: str | None
    total_monthly: str | None
    created_at: datetime


class InquiryListResponse(BaseModel):
    items: list[InquiryItemResponse]
    total: int


class InquiryDetailResponse(BaseModel):
    id: uuid.UUID
    contact_method: str
    contact_handle: str
    project_notes: str | None
    status: str
    configuration: dict[str, Any]
    estimated_quote: dict[str, Any]
    ip_hash: str | None
    created_at: datetime
    updated_at: datetime
    quotes_count: int


class UpdateInquiryStatusRequest(BaseModel):
    status: str = Field(..., description="NEW, CONTACTED, QUOTED, CONVERTED, ARCHIVED")


class QuoteLineInput(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    category: str = Field(default="general", max_length=60)
    item_type: str = Field(default="one_time", description="'one_time' or 'recurring'")
    amount: str = Field(..., description="Decimal amount string, e.g. '49.00'")
    description: str | None = Field(default=None, max_length=500)


class CreateQuoteRequest(BaseModel):
    customer_name: str = Field(..., min_length=2, max_length=120)
    customer_contact: str = Field(..., min_length=2, max_length=120)
    lines: list[QuoteLineInput] = Field(..., min_length=1, max_length=100)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    terms: str | None = Field(default=None, max_length=3000)
    notes: str | None = Field(default=None, max_length=2000)
    valid_days: int = Field(default=30, ge=1, le=365)


class QuoteLineResponse(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    item_type: str
    amount: str
    description: str | None


class QuoteResponse(BaseModel):
    scope_snapshot: dict[str, Any] = Field(default_factory=dict)
    id: uuid.UUID
    quote_number: str
    version: int
    inquiry_id: uuid.UUID | None
    tenant_id: uuid.UUID | None = None
    customer_name: str
    customer_contact: str
    status: str
    currency: str
    total_one_time: str
    total_monthly: str
    terms: str | None
    notes: str | None
    valid_until: datetime | None
    accepted_at: datetime | None
    created_at: datetime
    lines: list[QuoteLineResponse] = Field(default_factory=list)


class QuoteListResponse(BaseModel):
    items: list[QuoteResponse]
    total: int

class OnboardCustomerRequest(BaseModel):
    tenant_slug: str | None = Field(default=None, max_length=50)
    tenant_name: str | None = Field(default=None, max_length=120)
    owner_username: str | None = Field(default=None, max_length=120)
    owner_telegram_id: int = Field(gt=0)


class OnboardCustomerResponse(BaseModel):
    tenant_id: uuid.UUID
    tenant_slug: str
    tenant_name: str
    owner_id: uuid.UUID
    owner_username: str | None
    bot_id: uuid.UUID | None
    quote_id: uuid.UUID
    quote_number: str
    admin_launch_url: str
    login_code: str
    already_existed: bool


class GrantEntitlementRequest(BaseModel):
    granted_by: str = Field(default="OPERATOR", max_length=40)


class TenantEntitlementResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    integration_key: str
    is_enabled: bool
    granted_by: str
    granted_at: datetime

class CreateHandoffRequest(BaseModel):
    license_type: str = Field(default="DEDICATED_DEPLOYMENT", description="MANAGED, DEDICATED_DEPLOYMENT, SOURCE_LICENSE")
    licensed_to: str = Field(..., min_length=2, max_length=120)
    licensed_domain: str | None = Field(default=None, max_length=120)
    support_plan: str | None = Field(default=None, max_length=60)
    quote_id: uuid.UUID | None = Field(default=None)
    handoff_notes: str | None = Field(default=None, max_length=2000)


class DeploymentHandoffResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    quote_id: uuid.UUID | None
    license_type: str
    license_key: str
    licensed_to: str
    licensed_domain: str | None
    version_tag: str
    status: str
    support_plan: str | None
    runtime_deactivated: bool
    runtime_deactivated_at: datetime | None
    export_checksum: str | None
    export_artifact_path: str | None
    handoff_notes: str | None
    handed_off_at: datetime | None
    created_at: datetime


class GenerateBundleRequest(BaseModel):
    passphrase: SecretStr = Field(min_length=16, max_length=1024)
    confirm_quiesced: bool = False


class GenerateBundleResponse(BaseModel):
    handoff_id: str
    license_key: str
    tenant_slug: str
    checksum_sha256: str
    artifact_dir: str
    bundle_file: str


@router.get("/inquiries", response_model=InquiryListResponse)
async def list_inquiries(
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> InquiryListResponse:
    query = select(CustomerInquiry)
    count_query = select(func.count(CustomerInquiry.id))

    if status_filter:
        norm_status = status_filter.strip().upper()
        if norm_status in InquiryStatus.__members__:
            query = query.where(CustomerInquiry.status == InquiryStatus[norm_status])
            count_query = count_query.where(CustomerInquiry.status == InquiryStatus[norm_status])

    if search:
        s = f"%{search.strip()}%"
        cond = or_(
            CustomerInquiry.contact_handle.ilike(s),
            CustomerInquiry.project_notes.ilike(s),
        )
        query = query.where(cond)
        count_query = count_query.where(cond)

    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(CustomerInquiry.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )

    items = []
    for r in rows:
        conf = r.configuration or {}
        quote = r.estimated_quote or {}
        items.append(
            InquiryItemResponse(
                id=r.id,
                contact_method=r.contact_method.value,
                contact_handle=r.contact_handle,
                project_notes=r.project_notes,
                status=r.status.value,
                format=conf.get("format"),
                template_key=conf.get("template_key"),
                product_source=conf.get("product_source"),
                delivery_model=conf.get("delivery_model"),
                total_one_time=quote.get("total_one_time"),
                total_monthly=quote.get("total_monthly"),
                created_at=r.created_at,
            )
        )

    return InquiryListResponse(items=items, total=total)


@router.get("/inquiries/{inquiry_id}", response_model=InquiryDetailResponse)
async def get_inquiry(
    inquiry_id: uuid.UUID,
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> InquiryDetailResponse:
    stmt = (
        select(CustomerInquiry)
        .where(CustomerInquiry.id == inquiry_id)
        .options(selectinload(CustomerInquiry.quotes))
    )
    inquiry = (await session.execute(stmt)).scalar_one_or_none()
    if not inquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inquiry not found.")

    return InquiryDetailResponse(
        id=inquiry.id,
        contact_method=inquiry.contact_method.value,
        contact_handle=inquiry.contact_handle,
        project_notes=inquiry.project_notes,
        status=inquiry.status.value,
        configuration=inquiry.configuration,
        estimated_quote=inquiry.estimated_quote,
        ip_hash=inquiry.ip_hash,
        created_at=inquiry.created_at,
        updated_at=inquiry.updated_at,
        quotes_count=len(inquiry.quotes),
    )


@router.patch("/inquiries/{inquiry_id}/status", response_model=InquiryDetailResponse)
async def update_inquiry_status(
    inquiry_id: uuid.UUID,
    payload: UpdateInquiryStatusRequest,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> InquiryDetailResponse:
    inquiry = await session.get(CustomerInquiry, inquiry_id)
    if not inquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inquiry not found.")

    norm_status = payload.status.strip().upper()
    if norm_status not in InquiryStatus.__members__:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid status: {payload.status}")

    old_status = inquiry.status.value
    inquiry.status = InquiryStatus[norm_status]

    await append_platform_audit(
        session,
        action="inquiry.status_updated",
        resource_type="customer_inquiry",
        resource_id=str(inquiry.id),
        details={"old_status": old_status, "new_status": norm_status},
        ip_address=_client_ip(request),
        actor=operator.actor,
    )

    await session.commit()
    await session.refresh(inquiry)

    quotes_count = (
        await session.execute(
            select(func.count(CommercialQuote.id)).where(CommercialQuote.inquiry_id == inquiry.id)
        )
    ).scalar_one()

    return InquiryDetailResponse(
        id=inquiry.id,
        contact_method=inquiry.contact_method.value,
        contact_handle=inquiry.contact_handle,
        project_notes=inquiry.project_notes,
        status=inquiry.status.value,
        configuration=inquiry.configuration,
        estimated_quote=inquiry.estimated_quote,
        ip_hash=inquiry.ip_hash,
        created_at=inquiry.created_at,
        updated_at=inquiry.updated_at,
        quotes_count=quotes_count,
    )


@router.post("/inquiries/{inquiry_id}/quotes", response_model=QuoteResponse, status_code=status.HTTP_201_CREATED)
async def create_quote_for_inquiry(
    inquiry_id: uuid.UUID,
    payload: CreateQuoteRequest,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> QuoteResponse:
    inquiry = await session.scalar(select(CustomerInquiry).where(CustomerInquiry.id == inquiry_id).with_for_update())
    if not inquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inquiry not found.")

    if inquiry.status == InquiryStatus.CONVERTED:
        raise HTTPException(status_code=409, detail="This inquiry already has an accepted quote. Create a new inquiry for additional work.")
    customer_owned = inquiry.configuration.get("delivery_model") in QuoteEngine.CUSTOMER_OWNED

    # Calculate version and quote number
    existing_quotes = (
        (
            await session.execute(
                select(CommercialQuote)
                .where(CommercialQuote.inquiry_id == inquiry.id)
                .order_by(CommercialQuote.version.desc())
            )
        )
        .scalars()
        .all()
    )

    if any(q.status == QuoteStatus.ACCEPTED for q in existing_quotes):
        raise HTTPException(status_code=409, detail="This inquiry already has an accepted quote. Create a new inquiry for additional work.")
    now = datetime.now(UTC)
    if existing_quotes:
        quote_number = existing_quotes[0].quote_number
        version = existing_quotes[0].version + 1
    else:
        quote_number = f"Q-{now.year}-{str(uuid.uuid4().int % 100000).zfill(5)}"
        version = 1

    # Compute totals
    total_one_time = Decimal("0.00")
    total_monthly = Decimal("0.00")
    line_models = []

    for l in payload.lines:
        try:
            amt = Decimal(l.amount)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid line amount: {l.amount}"
            ) from exc

        if not amt.is_finite() or amt < 0 or amt > Decimal("999999999.99") or amt != amt.quantize(Decimal("0.01")):
            raise HTTPException(status_code=422, detail="Line amounts must be non-negative finite currency amounts with at most two decimals.")
        item_type = l.item_type.strip().lower()
        if item_type not in {"one_time", "recurring"}:
            raise HTTPException(status_code=422, detail="Invalid quote line type.")
        if customer_owned and item_type == "recurring":
            raise HTTPException(status_code=422, detail="Customer-owned deliveries use one-time project prices. Describe external running costs separately in the terms.")
        if item_type == "one_time":
            total_one_time += amt
        elif item_type == "recurring":
            total_monthly += amt
        else:
            item_type = "one_time"
            total_one_time += amt

        line_models.append(
            CommercialQuoteLine(
                name=l.name.strip(),
                category=l.category.strip().lower(),
                item_type=item_type,
                amount=amt,
                description=l.description.strip() if l.description else None,
            )
        )

    valid_until = now + timedelta(days=payload.valid_days)
    if total_one_time > Decimal("9999999999.99") or total_monthly > Decimal("9999999999.99"):
        raise HTTPException(status_code=422, detail="Quote total exceeds the supported range.")

    quote = CommercialQuote(
        scope_snapshot={"configuration": inquiry.configuration, "project_notes": inquiry.project_notes, "pricing_model": "ONE_TIME_DELIVERY" if customer_owned else "MANAGED", "version": 1},
        quote_number=quote_number,
        version=version,
        inquiry_id=inquiry.id,
        customer_name=payload.customer_name.strip(),
        customer_contact=payload.customer_contact.strip(),
        status=QuoteStatus.DRAFT,
        currency=payload.currency.strip().upper(),
        total_one_time=total_one_time,
        total_monthly=total_monthly,
        terms=payload.terms.strip() if payload.terms else None,
        notes=payload.notes.strip() if payload.notes else None,
        valid_until=valid_until,
        lines=line_models,
    )
    session.add(quote)

    inquiry.status = InquiryStatus.QUOTED

    await append_platform_audit(
        session,
        action="quote.created",
        resource_type="commercial_quote",
        resource_id=quote_number,
        details={
            "quote_id": str(quote.id),
            "version": version,
            "inquiry_id": str(inquiry.id),
            "total_one_time": str(total_one_time),
            "total_monthly": str(total_monthly),
            "currency": quote.currency,
        },
        ip_address=_client_ip(request),
        actor=operator.actor,
    )

    await session.commit()
    stmt = (
        select(CommercialQuote)
        .where(CommercialQuote.id == quote.id)
        .options(selectinload(CommercialQuote.lines))
    )
    quote = (await session.execute(stmt)).scalar_one()

    return QuoteResponse(
        scope_snapshot=quote.scope_snapshot,
        id=quote.id,
        quote_number=quote.quote_number,
        version=quote.version,
        inquiry_id=quote.inquiry_id,
        tenant_id=quote.tenant_id,
        customer_name=quote.customer_name,
        customer_contact=quote.customer_contact,
        status=quote.status.value,
        currency=quote.currency,
        total_one_time=str(quote.total_one_time),
        total_monthly=str(quote.total_monthly),
        terms=quote.terms,
        notes=quote.notes,
        valid_until=quote.valid_until,
        accepted_at=quote.accepted_at,
        created_at=quote.created_at,
        lines=[
            QuoteLineResponse(
                id=item.id,
                name=item.name,
                category=item.category,
                item_type=item.item_type,
                amount=str(item.amount),
                description=item.description,
            )
            for item in quote.lines
        ],
    )


@router.get("/quotes", response_model=QuoteListResponse)
async def list_quotes(
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> QuoteListResponse:
    query = select(CommercialQuote).options(selectinload(CommercialQuote.lines))
    count_query = select(func.count(CommercialQuote.id))

    if status_filter:
        norm_status = status_filter.strip().upper()
        if norm_status in QuoteStatus.__members__:
            query = query.where(CommercialQuote.status == QuoteStatus[norm_status])
            count_query = count_query.where(CommercialQuote.status == QuoteStatus[norm_status])

    if search:
        s = f"%{search.strip()}%"
        cond = or_(
            CommercialQuote.quote_number.ilike(s),
            CommercialQuote.customer_name.ilike(s),
            CommercialQuote.customer_contact.ilike(s),
        )
        query = query.where(cond)
        count_query = count_query.where(cond)

    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(CommercialQuote.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )

    items = [
        QuoteResponse(
            scope_snapshot=q.scope_snapshot,
            id=q.id,
            quote_number=q.quote_number,
            version=q.version,
            inquiry_id=q.inquiry_id,
            tenant_id=q.tenant_id,
            customer_name=q.customer_name,
            customer_contact=q.customer_contact,
            status=q.status.value,
            currency=q.currency,
            total_one_time=str(q.total_one_time),
            total_monthly=str(q.total_monthly),
            terms=q.terms,
            notes=q.notes,
            valid_until=q.valid_until,
            accepted_at=q.accepted_at,
            created_at=q.created_at,
            lines=[
                QuoteLineResponse(
                    id=l.id,
                    name=l.name,
                    category=l.category,
                    item_type=l.item_type,
                    amount=str(l.amount),
                    description=l.description,
                )
                for l in q.lines
            ],
        )
        for q in rows
    ]

    return QuoteListResponse(items=items, total=total)


@router.get("/quotes/{quote_id}", response_model=QuoteResponse)
async def get_quote(
    quote_id: uuid.UUID,
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> QuoteResponse:
    stmt = (
        select(CommercialQuote)
        .where(CommercialQuote.id == quote_id)
        .options(selectinload(CommercialQuote.lines))
    )
    quote = (await session.execute(stmt)).scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found.")

    return QuoteResponse(
        scope_snapshot=quote.scope_snapshot,
        id=quote.id,
        quote_number=quote.quote_number,
        version=quote.version,
        inquiry_id=quote.inquiry_id,
        tenant_id=quote.tenant_id,
        customer_name=quote.customer_name,
        customer_contact=quote.customer_contact,
        status=quote.status.value,
        currency=quote.currency,
        total_one_time=str(quote.total_one_time),
        total_monthly=str(quote.total_monthly),
        terms=quote.terms,
        notes=quote.notes,
        valid_until=quote.valid_until,
        accepted_at=quote.accepted_at,
        created_at=quote.created_at,
        lines=[
            QuoteLineResponse(
                id=l.id,
                name=l.name,
                category=l.category,
                item_type=l.item_type,
                amount=str(l.amount),
                description=l.description,
            )
            for l in quote.lines
        ],
    )


@router.post("/quotes/{quote_id}/accept", response_model=QuoteResponse)
async def accept_quote(
    quote_id: uuid.UUID,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> QuoteResponse:
    parent_id = await session.scalar(select(CommercialQuote.inquiry_id).where(CommercialQuote.id == quote_id))
    if parent_id:
        await session.scalar(select(CustomerInquiry).where(CustomerInquiry.id == parent_id).with_for_update())
    stmt = (
        select(CommercialQuote)
        .where(CommercialQuote.id == quote_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .options(selectinload(CommercialQuote.lines))
    )
    quote = (await session.execute(stmt)).scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found.")

    if quote.status == QuoteStatus.ACCEPTED:
        return QuoteResponse(
            scope_snapshot=quote.scope_snapshot,
        id=quote.id,
            quote_number=quote.quote_number,
            version=quote.version,
            inquiry_id=quote.inquiry_id,
            tenant_id=quote.tenant_id,
            customer_name=quote.customer_name,
            customer_contact=quote.customer_contact,
            status=quote.status.value,
            currency=quote.currency,
            total_one_time=str(quote.total_one_time),
            total_monthly=str(quote.total_monthly),
            terms=quote.terms,
            notes=quote.notes,
            valid_until=quote.valid_until,
            accepted_at=quote.accepted_at,
            created_at=quote.created_at,
            lines=[
                QuoteLineResponse(
                    id=l.id,
                    name=l.name,
                    category=l.category,
                    item_type=l.item_type,
                    amount=str(l.amount),
                    description=l.description,
                )
                for l in quote.lines
            ],
        )

    now = datetime.now(UTC)
    expires = quote.valid_until.replace(tzinfo=UTC) if quote.valid_until and quote.valid_until.tzinfo is None else quote.valid_until
    if quote.status not in {QuoteStatus.DRAFT, QuoteStatus.SENT} or (expires and expires <= now):
        raise HTTPException(status_code=409, detail="Only a current unexpired draft or sent quote can be accepted.")
    if parent_id and await session.scalar(select(CommercialQuote.id).where(CommercialQuote.inquiry_id == parent_id, CommercialQuote.status == QuoteStatus.ACCEPTED, CommercialQuote.id != quote.id)):
        raise HTTPException(status_code=409, detail="Another quote for this inquiry is already accepted.")
    quote.status = QuoteStatus.ACCEPTED
    quote.accepted_at = now

    # Mark prior drafts superseded
    if quote.inquiry_id:
        prior_quotes = (
            (
                await session.execute(
                    select(CommercialQuote).where(
                        CommercialQuote.inquiry_id == quote.inquiry_id,
                        CommercialQuote.id != quote.id,
                        CommercialQuote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT]),
                    )
                )
            )
            .scalars()
            .all()
        )
        for pq in prior_quotes:
            pq.status = QuoteStatus.SUPERSEDED

        # Update inquiry status to CONVERTED
        inquiry = await session.get(CustomerInquiry, quote.inquiry_id)
        if inquiry:
            inquiry.status = InquiryStatus.CONVERTED

    await append_platform_audit(
        session,
        action="quote.accepted",
        resource_type="commercial_quote",
        resource_id=quote.quote_number,
        details={
            "quote_id": str(quote.id),
            "version": quote.version,
            "total_one_time": str(quote.total_one_time),
            "total_monthly": str(quote.total_monthly),
            "customer_name": quote.customer_name,
            "customer_contact": quote.customer_contact,
        },
        ip_address=_client_ip(request),
        actor=operator.actor,
    )

    await session.commit()
    stmt = (
        select(CommercialQuote)
        .where(CommercialQuote.id == quote.id)
        .options(selectinload(CommercialQuote.lines))
    )
    quote = (await session.execute(stmt)).scalar_one()

    return QuoteResponse(
        scope_snapshot=quote.scope_snapshot,
        id=quote.id,
        quote_number=quote.quote_number,
        version=quote.version,
        inquiry_id=quote.inquiry_id,
        tenant_id=quote.tenant_id,
        customer_name=quote.customer_name,
        customer_contact=quote.customer_contact,
        status=quote.status.value,
        currency=quote.currency,
        total_one_time=str(quote.total_one_time),
        total_monthly=str(quote.total_monthly),
        terms=quote.terms,
        notes=quote.notes,
        valid_until=quote.valid_until,
        accepted_at=quote.accepted_at,
        created_at=quote.created_at,
        lines=[
            QuoteLineResponse(
                id=l.id,
                name=l.name,
                category=l.category,
                item_type=l.item_type,
                amount=str(l.amount),
                description=l.description,
            )
            for l in quote.lines
        ],
    )

@router.post("/quotes/{quote_id}/onboard", response_model=OnboardCustomerResponse, status_code=status.HTTP_201_CREATED)
async def onboard_customer_tenant(
    quote_id: uuid.UUID,
    payload: OnboardCustomerRequest,
    request: Request,
    response: Response,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> OnboardCustomerResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await CustomerOnboardingService.onboard_from_quote(
            session,
            quote_id=quote_id,
            tenant_slug=payload.tenant_slug,
            tenant_name=payload.tenant_name,
            owner_username=payload.owner_username,
            owner_telegram_id=payload.owner_telegram_id,
            actor=operator.actor,
            ip_address=_client_ip(request),
        )
    except OnboardingUnavailableError as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Onboarding conflicts with an existing record. Retry with the confirmed owner and a unique slug.") from exc
    except OnboardingError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return OnboardCustomerResponse(
        tenant_id=result.tenant_id,
        tenant_slug=result.tenant_slug,
        tenant_name=result.tenant_name,
        owner_id=result.owner_id,
        owner_username=result.owner_username,
        bot_id=result.bot_id,
        quote_id=result.quote_id,
        quote_number=result.quote_number,
        admin_launch_url=result.admin_launch_url,
        login_code=result.login_code,
        already_existed=result.already_existed,
    )

@router.get("/integrations")
async def list_platform_integrations(
    tenant_id: uuid.UUID | None = Query(default=None),
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    return await IntegrationMarketplaceService.list_offerings_for_tenant(session, tenant_id=tenant_id)


@router.post("/tenants/{tenant_id}/integrations/{integration_key}/grant", response_model=TenantEntitlementResponse)
async def grant_tenant_integration(
    tenant_id: uuid.UUID,
    integration_key: str,
    payload: GrantEntitlementRequest,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> TenantEntitlementResponse:
    try:
        ent = await IntegrationMarketplaceService.grant_entitlement(
            session,
            tenant_id=tenant_id,
            integration_key=integration_key,
            granted_by=payload.granted_by,
            actor=operator.actor,
            ip_address=_client_ip(request),
        )
    except IntegrationMarketplaceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return TenantEntitlementResponse(
        id=ent.id,
        tenant_id=ent.tenant_id,
        integration_key=ent.integration_key,
        is_enabled=ent.is_enabled,
        granted_by=ent.granted_by,
        granted_at=ent.granted_at,
    )


@router.delete("/tenants/{tenant_id}/integrations/{integration_key}")
async def revoke_tenant_integration(
    tenant_id: uuid.UUID,
    integration_key: str,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, bool]:
    await IntegrationMarketplaceService.revoke_entitlement(
        session,
        tenant_id=tenant_id,
        integration_key=integration_key,
        actor=operator.actor,
        ip_address=_client_ip(request),
    )
    return {"ok": True}

@router.get("/handoffs", response_model=list[DeploymentHandoffResponse])
async def list_deployment_handoffs(
    status_filter: str | None = Query(default=None, alias="status"),
    tenant_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> list[DeploymentHandoffResponse]:
    query = select(DeploymentHandoff)
    if status_filter:
        norm = status_filter.strip().upper()
        if norm in HandoffStatus.__members__:
            query = query.where(DeploymentHandoff.status == HandoffStatus[norm])
    if tenant_id:
        query = query.where(DeploymentHandoff.tenant_id == tenant_id)
    if search:
        s = f"%{search.strip()}%"
        query = query.where(
            or_(
                DeploymentHandoff.license_key.ilike(s),
                DeploymentHandoff.licensed_to.ilike(s),
                DeploymentHandoff.licensed_domain.ilike(s),
            )
        )

    rows = (
        (
            await session.execute(
                query.order_by(DeploymentHandoff.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return [
        DeploymentHandoffResponse(
            id=r.id,
            tenant_id=r.tenant_id,
            quote_id=r.quote_id,
            license_type=r.license_type.value,
            license_key=r.license_key,
            licensed_to=r.licensed_to,
            licensed_domain=r.licensed_domain,
            version_tag=r.version_tag,
            status=r.status.value,
            support_plan=r.support_plan,
            runtime_deactivated=r.runtime_deactivated,
            runtime_deactivated_at=r.runtime_deactivated_at,
            export_checksum=r.export_checksum,
            export_artifact_path=r.export_artifact_path,
            handoff_notes=r.handoff_notes,
            handed_off_at=r.handed_off_at,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/tenants/{tenant_id}/handoffs", response_model=DeploymentHandoffResponse, status_code=status.HTTP_201_CREATED)
async def create_deployment_handoff(
    tenant_id: uuid.UUID,
    payload: CreateHandoffRequest,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> DeploymentHandoffResponse:
    norm_type = payload.license_type.strip().upper()
    try:
        lic_type = LicenseType[norm_type]
    except KeyError:
        lic_type = LicenseType.DEDICATED_DEPLOYMENT

    try:
        handoff = await DeploymentHandoffService.create_handoff(
            session,
            tenant_id=tenant_id,
            license_type=lic_type,
            licensed_to=payload.licensed_to,
            licensed_domain=payload.licensed_domain,
            support_plan=payload.support_plan,
            quote_id=payload.quote_id,
            handoff_notes=payload.handoff_notes,
            actor=operator.actor,
            ip_address=_client_ip(request),
        )
    except HandoffError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return DeploymentHandoffResponse(
        id=handoff.id,
        tenant_id=handoff.tenant_id,
        quote_id=handoff.quote_id,
        license_type=handoff.license_type.value,
        license_key=handoff.license_key,
        licensed_to=handoff.licensed_to,
        licensed_domain=handoff.licensed_domain,
        version_tag=handoff.version_tag,
        status=handoff.status.value,
        support_plan=handoff.support_plan,
        runtime_deactivated=handoff.runtime_deactivated,
        runtime_deactivated_at=handoff.runtime_deactivated_at,
        export_checksum=handoff.export_checksum,
        export_artifact_path=handoff.export_artifact_path,
        handoff_notes=handoff.handoff_notes,
        handed_off_at=handoff.handed_off_at,
        created_at=handoff.created_at,
    )


@router.post("/handoffs/{handoff_id}/generate-bundle", response_model=GenerateBundleResponse)
async def generate_handoff_bundle(
    handoff_id: uuid.UUID,
    payload: GenerateBundleRequest,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> GenerateBundleResponse:
    try:
        data = await DeploymentHandoffService.generate_single_tenant_export_bundle(
            session,
            passphrase=payload.passphrase.get_secret_value(),
            confirm_quiesced=payload.confirm_quiesced,
            handoff_id=handoff_id,
            actor=operator.actor,
            ip_address=_client_ip(request),
        )
    except HandoffError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return GenerateBundleResponse(**data)


@router.post("/handoffs/{handoff_id}/deactivate-managed", response_model=DeploymentHandoffResponse)
async def deactivate_managed_runtime(
    handoff_id: uuid.UUID,
    request: Request,
    operator: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> DeploymentHandoffResponse:
    try:
        handoff = await DeploymentHandoffService.deactivate_managed_runtime(
            session,
            handoff_id=handoff_id,
            actor=operator.actor,
            ip_address=_client_ip(request),
        )
    except HandoffError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return DeploymentHandoffResponse(
        id=handoff.id,
        tenant_id=handoff.tenant_id,
        quote_id=handoff.quote_id,
        license_type=handoff.license_type.value,
        license_key=handoff.license_key,
        licensed_to=handoff.licensed_to,
        licensed_domain=handoff.licensed_domain,
        version_tag=handoff.version_tag,
        status=handoff.status.value,
        support_plan=handoff.support_plan,
        runtime_deactivated=handoff.runtime_deactivated,
        runtime_deactivated_at=handoff.runtime_deactivated_at,
        export_checksum=handoff.export_checksum,
        export_artifact_path=handoff.export_artifact_path,
        handoff_notes=handoff.handoff_notes,
        handed_off_at=handoff.handed_off_at,
        created_at=handoff.created_at,
    )

@router.get("/governance/metrics")
async def get_commercial_governance_metrics(
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await CommercialGovernanceService.collect_metrics(session)


@router.get("/handoffs/{handoff_id}/bundle")
async def download_handoff_bundle(
    handoff_id: uuid.UUID,
    _: PlatformOperator = Depends(require_platform_operator),
    session: AsyncSession = Depends(get_db_session),
):
    from packages.core.config import settings

    handoff = await session.get(DeploymentHandoff, handoff_id)
    if not handoff or not handoff.export_artifact_path:
        raise HTTPException(status_code=404, detail="No encrypted bundle is available.")
    path = Path(handoff.export_artifact_path).resolve()
    root = Path(settings.handoff_export_dir).resolve()
    if root not in path.parents or path.name != "tenant.ghbf.enc" or not path.is_file():
        raise HTTPException(status_code=404, detail="Encrypted artifact is unavailable; generate a new bundle.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != handoff.export_checksum:
        raise HTTPException(status_code=409, detail="Artifact integrity check failed; generate a new bundle.")
    return FileResponse(path, filename=f"tenant-{handoff_id}.ghbf.enc", media_type="application/octet-stream", headers={"Cache-Control": "no-store"})
