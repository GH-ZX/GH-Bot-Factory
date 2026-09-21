from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_staff_or_above
from packages.commerce.models import Product
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.core.system_models import SystemInstallState
from packages.factory.templates import TemplateValidationError, get_bot_template
from packages.payments.models import PaymentMethodConfig
from packages.providers.models import Provider
from packages.telegram.models import Bot
from packages.tenants.models import Tenant

router = APIRouter(prefix="/admin/onboarding", tags=["admin-onboarding"])


class ChecklistItem(BaseModel):
    key: str
    title: str
    description: str
    completed: bool
    action_view: str  # navigation target in admin, e.g. "products", "providers", "bots"


class OnboardingChecklistResponse(BaseModel):
    tenant_id: uuid.UUID
    workspace_kind: str = "store"
    progress_percent: int = Field(ge=0, le=100)
    launch_ready: bool
    next_step: str
    items: list[ChecklistItem]
    recommended_template_key: str


@router.get("/checklist", response_model=OnboardingChecklistResponse)
async def get_onboarding_checklist(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> OnboardingChecklistResponse:
    tenant_id = principal.tenant_id

    install = await session.get(SystemInstallState, 1)
    if (install and install.is_initialized and install.tenant_id == tenant_id
            and install.operator_user_id == principal.user_id):
        return OnboardingChecklistResponse(
            tenant_id=tenant_id, workspace_kind="factory", progress_percent=100,
            launch_ready=True, next_step="Open Sales & leads to review customer requests. No owner bot is required.",
            recommended_template_key="general-commerce",
            items=[ChecklistItem(key="factory_account", title="Factory account ready",
                description="Your workspace is ready. Customer bots are configured separately.",
                completed=True, action_view="sales")],
        )

    # 1. Inspect Bot & Branding
    bot = (
        await session.execute(
            select(Bot).where(Bot.tenant_id == tenant_id, Bot.deleted_at.is_(None)).order_by(Bot.created_at.asc())
        )
    ).scalars().first()

    tenant = await session.get(Tenant, tenant_id)
    initial_config = (tenant.settings or {}).get("onboarding_template", {}) if tenant else {}
    template_key = initial_config.get("_factory", {}).get("template_key", "general-commerce")
    if bot and bot.config and isinstance(bot.config, dict):
        template_key = bot.config.get("_factory", {}).get("template_key", "general-commerce")

    try:
        template = get_bot_template(template_key)
        needs_providers = (
            template.guidance.product_source in {"provider_api", "hybrid"}
            if template.guidance
            else False
        )
    except (TemplateValidationError, ValueError):
        needs_providers = False

    brand_ready = False
    bot_ready = False
    if bot:
        brand_ready = bool(bot.display_name and bot.display_name.strip() != "My Store")
        bot_ready = bool(bot.is_enabled and bot.token_secret_ref and bot.credential_status == "VERIFIED")

    # 2. Inspect Products
    product_count = (
        await session.execute(
            select(func.count(Product.id)).where(Product.tenant_id == tenant_id, Product.is_active.is_(True))
        )
    ).scalar_one()
    catalog_ready = product_count > 0

    # 3. Inspect Payments
    payment_count = (
        await session.execute(
            select(func.count(PaymentMethodConfig.id)).where(
                PaymentMethodConfig.tenant_id == tenant_id, PaymentMethodConfig.is_enabled.is_(True)
            )
        )
    ).scalar_one()
    payments_ready = payment_count > 0

    # 4. Inspect Providers
    if needs_providers:
        provider_count = (
            await session.execute(
                select(func.count(Provider.id)).where(Provider.tenant_id == tenant_id, Provider.is_enabled.is_(True))
            )
        )
        providers_ready = provider_count.scalar_one() > 0
    else:
        providers_ready = True

    items = [
        ChecklistItem(
            key="branding",
            title="Store Branding & Identity",
            description="Customize your bot name, logo, accent color, and store tagline.",
            completed=brand_ready,
            action_view="bots",
        ),
        ChecklistItem(
            key="catalog",
            title="Product Catalog & Inventory",
            description="Add your first product, pricing variants, and stock.",
            completed=catalog_ready,
            action_view="products",
        ),
        ChecklistItem(
            key="payments",
            title="Payment Methods",
            description="Enable customer payment options (crypto wallet, cards, stars).",
            completed=payments_ready,
            action_view="providers",
        ),
        ChecklistItem(
            key="providers",
            title="Supplier API Connections",
            description="Connect wholesale provider adapters if your store uses live API inventory.",
            completed=providers_ready,
            action_view="providers",
        ),
        ChecklistItem(
            key="bot_token",
            title="Telegram Bot Token",
            description="Connect your BotFather bot token to verify Telegram identity.",
            completed=bot_ready,
            action_view="bots",
        ),
    ]

    completed_count = sum(1 for it in items if it.completed)
    progress_percent = int((completed_count / len(items)) * 100)
    launch_ready = completed_count == len(items)

    if not brand_ready:
        next_step = "Brand your bot in the Bots tab"
    elif not catalog_ready:
        next_step = "Add your first product in the Products tab"
    elif not payments_ready:
        next_step = "Configure a payment method in Providers tab"
    elif not providers_ready:
        next_step = "Configure a supplier provider in Providers tab"
    elif not bot_ready:
        next_step = "Connect BotFather token in Bots tab"
    else:
        next_step = "Store is launch-ready! Test placing an order."

    return OnboardingChecklistResponse(
        tenant_id=tenant_id,
        progress_percent=progress_percent,
        launch_ready=launch_ready,
        next_step=next_step,
        items=items,
        recommended_template_key=template_key,
    )
