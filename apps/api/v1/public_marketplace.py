from __future__ import annotations

import hashlib
import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import settings
from packages.core.database import get_db_session
from packages.factory.templates import list_bot_templates
from packages.marketplace.integrations import list_integration_offerings
from packages.marketplace.models import ContactMethod, CustomerInquiry, InquiryStatus
from packages.marketplace.quotes import QuoteEngine

router = APIRouter(prefix="/public", tags=["public-marketplace"])


class PublicTemplateGuidance(BaseModel):
    product_source: str
    what_you_can_sell: str
    delivery_experience: str
    operational_complexity: str
    setup_requirements: list[str]
    example_business: str
    limitations: str
    supported_hosting: list[str]


class PublicTemplateItem(BaseModel):
    key: str
    name: str
    version: int
    description: str
    recommended_for: str
    business_type: str
    provider_categories: list[str]
    default_routing_strategy: str
    guidance: PublicTemplateGuidance | None


class PublicTemplatesResponse(BaseModel):
    templates: list[PublicTemplateItem]


class PublicIntegrationItem(BaseModel):
    key: str
    name: str
    category: str
    description: str
    setup_fee: str
    monthly_fee: str
    supported_templates: list[str]
    features: list[str]
    requirements: list[str]
    currency: str


class PublicIntegrationsResponse(BaseModel):
    integrations: list[PublicIntegrationItem]


class QuoteLineItemResponse(BaseModel):
    name: str
    category: str
    item_type: str
    amount: str
    description: str


class ConfiguratorEstimateRequest(BaseModel):
    format: str = Field(default="combo", description="'bot', 'miniapp', or 'combo'")
    template_key: str = Field(default="general-commerce")
    product_source: str = Field(default="stored", description="'stored', 'provider_api', or 'hybrid'")
    delivery_model: str = Field(default="managed", description="'managed', 'dedicated', or 'source_license'")
    integration_keys: list[str] = Field(default_factory=list)


class ConfiguratorEstimateResponse(BaseModel):
    format: str
    template_key: str
    template_name: str
    product_source: str
    delivery_model: str
    selected_integrations: list[str]
    items: list[QuoteLineItemResponse]
    total_one_time: str
    total_monthly: str
    currency: str
    notes: str
    telegram_contact_url: str | None


class ProjectBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store_name: str = Field(default="", max_length=100)
    accent: str = Field(default="#166b52", pattern=r"^#[0-9a-fA-F]{6}$")
    visual_style: Literal["Clean & minimal", "Bold & colorful", "Dark & refined"] = "Clean & minimal"
    store_language: Literal["Arabic + English", "Arabic", "English"] = "Arabic + English"
    report_language: Literal["Arabic", "English"] = "English"
    requested_features: list[Literal["pricing", "warranty", "coupons", "resellers", "support", "announcements", "branding", "catalog", "users", "history", "review", "alerts", "motion", "emoji"]] = Field(default_factory=list, max_length=14)
    hosting_advice: bool = False


class CreateInquiryRequest(BaseModel):
    brief: ProjectBrief | None = None
    contact_method: str = Field(default="TELEGRAM", description="'TELEGRAM', 'WHATSAPP', or 'EMAIL'")
    contact_handle: str = Field(..., min_length=2, max_length=120)
    project_notes: str | None = Field(default=None, max_length=2000)
    format: str = Field(default="combo")
    template_key: str = Field(default="general-commerce")
    product_source: str = Field(default="stored")
    delivery_model: str = Field(default="managed")
    integration_keys: list[str] = Field(default_factory=list)
    custom_api_request: str | None = Field(
        default=None,
        max_length=1000,
        description="Customer-requested API name, URL, or details to add to the factory",
    )


class CreateInquiryResponse(BaseModel):
    inquiry_id: uuid.UUID
    status: str
    quote_summary: str
    total_one_time: str
    total_monthly: str
    currency: str
    telegram_link: str | None


@router.get("/templates", response_model=PublicTemplatesResponse)
async def get_public_templates() -> PublicTemplatesResponse:
    rows = []
    for t in list_bot_templates():
        payload = t.public_payload()
        rows.append(
            PublicTemplateItem(
                key=payload["key"],
                name=payload["name"],
                version=payload["version"],
                description=payload["description"],
                recommended_for=payload["recommended_for"],
                business_type=payload["business_type"],
                provider_categories=payload["provider_categories"],
                default_routing_strategy=payload["default_routing_strategy"],
                guidance=PublicTemplateGuidance(**payload["guidance"]) if payload.get("guidance") else None,
            )
        )
    return PublicTemplatesResponse(templates=rows)


@router.get("/integrations", response_model=PublicIntegrationsResponse)
async def get_public_integrations() -> PublicIntegrationsResponse:
    rows = [PublicIntegrationItem(**item.public_payload()) for item in list_integration_offerings()]
    return PublicIntegrationsResponse(integrations=rows)


@router.post("/estimate", response_model=ConfiguratorEstimateResponse)
async def calculate_estimate(request_data: ConfiguratorEstimateRequest) -> ConfiguratorEstimateResponse:
    estimate = QuoteEngine.calculate_estimate(
        format=request_data.format,
        template_key=request_data.template_key,
        product_source=request_data.product_source,
        delivery_model=request_data.delivery_model,
        integration_keys=request_data.integration_keys,
    )
    return ConfiguratorEstimateResponse(
        format=estimate.format,
        template_key=estimate.template_key,
        template_name=estimate.template_name,
        product_source=estimate.product_source,
        delivery_model=estimate.delivery_model,
        selected_integrations=list(estimate.selected_integrations),
        items=[QuoteLineItemResponse(**item.to_dict()) for item in estimate.items],
        total_one_time=str(estimate.total_one_time),
        total_monthly=str(estimate.total_monthly),
        currency=estimate.currency,
        notes=estimate.notes,
        telegram_contact_url=(f"https://t.me/{settings.owner_telegram_handle}" if settings.owner_telegram_handle else None),
    )


@router.post("/inquiries", response_model=CreateInquiryResponse, status_code=status.HTTP_201_CREATED)
async def submit_inquiry(
    request: Request,
    payload: CreateInquiryRequest,
    session: AsyncSession = Depends(get_db_session),
) -> CreateInquiryResponse:
    client_ip = request.client.host if request.client else "unknown"
    ip_hash = hashlib.sha256(client_ip.encode("utf-8")).hexdigest()

    estimate = QuoteEngine.calculate_estimate(
        format=payload.format,
        template_key=payload.template_key,
        product_source=payload.product_source,
        delivery_model=payload.delivery_model,
        integration_keys=payload.integration_keys,
    )

    norm_method = payload.contact_method.strip().upper()
    try:
        contact_method = ContactMethod[norm_method]
    except KeyError:
        contact_method = ContactMethod.TELEGRAM

    custom_api = payload.custom_api_request.strip() if payload.custom_api_request else None
    notes = payload.project_notes.strip() if payload.project_notes else ""
    if custom_api:
        notes = f"{notes}\n\n[Requested Custom API: {custom_api}]".strip()

    inquiry = CustomerInquiry(
        contact_method=contact_method,
        contact_handle=payload.contact_handle.strip(),
        project_notes=notes or None,
        configuration={
            "format": payload.format,
            "template_key": payload.template_key,
            "product_source": payload.product_source,
            "delivery_model": payload.delivery_model,
            "integration_keys": payload.integration_keys,
            "custom_api_request": custom_api,
            "brief": payload.brief.model_dump() if payload.brief else None,
        },
        estimated_quote=estimate.to_dict(),
        status=InquiryStatus.NEW,
        ip_hash=ip_hash,
    )
    session.add(inquiry)
    await session.commit()
    await session.refresh(inquiry)

    owner_handle = settings.owner_telegram_handle
    msg_summary = (
        f"Hello! I want to build a bot with GH Bot Factory.\n\n"
        f"• Inquiry #{str(inquiry.id)[:8]}\n"
        f"• Format: {estimate.format.title()}\n"
        f"• Template: {estimate.template_name}\n"
        f"• Source: {estimate.product_source.title()}\n"
        f"• Hosting: {estimate.delivery_model.title()}\n"
        f"• Est. One-Time: ${estimate.total_one_time}\n"
        f"• Est. Monthly: ${estimate.total_monthly}\n"
        f"• Contact: {inquiry.contact_handle} ({inquiry.contact_method.value})\n"
        + (f"• Requested Custom API: {custom_api}\n" if custom_api else "")
    )
    if inquiry.project_notes:
        msg_summary += f"• Notes: {inquiry.project_notes}\n"

    telegram_link = f"https://t.me/{owner_handle}?text={quote(msg_summary, safe='')}" if owner_handle else None

    return CreateInquiryResponse(
        inquiry_id=inquiry.id,
        status=inquiry.status.value,
        quote_summary=f"{estimate.template_name} ({estimate.format}) — ${estimate.total_one_time} setup, ${estimate.total_monthly}/mo",
        total_one_time=str(estimate.total_one_time),
        total_monthly=str(estimate.total_monthly),
        currency=estimate.currency,
        telegram_link=telegram_link,
    )
