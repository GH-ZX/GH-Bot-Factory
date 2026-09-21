from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_admin_or_owner, require_staff_or_above
from packages.commerce.economics_models import (
    MarkupMode,
    OrderItemEconomics,
    PricingRule,
    PricingScope,
    PricingTier,
    UserPricingTier,
)
from packages.commerce.models import Category, Product, ProductVariant
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.core.exceptions import LedgerIntegrityError
from packages.operations.service import audit
from packages.payments.economics import FxPolicyService
from packages.payments.economics_models import FlexibleDepositSession, FxPolicy, FxPolicyMode
from packages.providers.models import Provider, ProviderBalanceSnapshot
from packages.telegram.models import TenantTelegramUser
from packages.tenants.models import Membership, User

router = APIRouter(prefix="/admin/economics", tags=["admin-economics"])
_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class PricingTierCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=2, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    priority: int = Field(default=100, ge=0, le=100000)
    is_default: bool = False


class PricingTierResponse(BaseModel):
    id: uuid.UUID
    code: str
    display_name: str
    priority: int
    is_default: bool
    is_active: bool

    @classmethod
    def from_model(cls, item: PricingTier) -> PricingTierResponse:
        return cls(
            id=item.id,
            code=item.code,
            display_name=item.display_name,
            priority=item.priority,
            is_default=item.is_default,
            is_active=item.is_active,
        )


class TierAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: uuid.UUID
    tier_id: uuid.UUID


class PricingRuleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    scope: PricingScope
    category_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    product_variant_id: uuid.UUID | None = None
    tier_id: uuid.UUID | None = None
    markup_mode: MarkupMode
    markup_percent: Decimal = Field(default=Decimal(0), ge=Decimal(0), le=Decimal(100000))
    markup_fixed: Decimal = Field(default=Decimal(0), ge=Decimal(0))
    minimum_margin: Decimal = Field(default=Decimal(0), ge=Decimal(0))
    rounding_increment: Decimal = Field(default=Decimal("0.01"), gt=Decimal(0))
    priority: int = Field(default=100, ge=0, le=100000)


class PricingRuleResponse(BaseModel):
    id: uuid.UUID
    name: str
    scope: str
    category_id: uuid.UUID | None
    product_id: uuid.UUID | None
    product_variant_id: uuid.UUID | None
    tier_id: uuid.UUID | None
    markup_mode: str
    markup_percent: Decimal
    markup_fixed: Decimal
    minimum_margin: Decimal
    rounding_increment: Decimal
    priority: int
    is_active: bool

    @classmethod
    def from_model(cls, item: PricingRule) -> PricingRuleResponse:
        return cls(
            id=item.id,
            name=item.name,
            scope=item.scope.value,
            category_id=item.category_id,
            product_id=item.product_id,
            product_variant_id=item.product_variant_id,
            tier_id=item.tier_id,
            markup_mode=item.markup_mode.value,
            markup_percent=item.markup_percent,
            markup_fixed=item.markup_fixed,
            minimum_margin=item.minimum_margin,
            rounding_increment=item.rounding_increment,
            priority=item.priority,
            is_active=item.is_active,
        )


class FxPolicyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_asset: str = Field(min_length=1, max_length=24)
    from_network: str | None = Field(default=None, max_length=64)
    to_currency: str = Field(min_length=3, max_length=12)
    mode: FxPolicyMode
    rate: Decimal = Field(gt=Decimal(0))
    max_auto_credit_amount: Decimal | None = Field(default=None, gt=Decimal(0))


class FxPolicyResponse(BaseModel):
    id: uuid.UUID
    from_asset: str
    from_network: str
    to_currency: str
    mode: str
    rate: Decimal
    max_auto_credit_amount: Decimal | None
    is_enabled: bool

    @classmethod
    def from_model(cls, item: FxPolicy) -> FxPolicyResponse:
        return cls(
            id=item.id,
            from_asset=item.from_asset,
            from_network=item.from_network,
            to_currency=item.to_currency,
            mode=item.mode.value,
            rate=item.rate,
            max_auto_credit_amount=item.max_auto_credit_amount,
            is_enabled=item.is_enabled,
        )


class OrderEconomicsResponse(BaseModel):
    order_id: uuid.UUID
    order_item_id: uuid.UUID
    bot_id: uuid.UUID | None
    provider_id: uuid.UUID | None
    sale_amount: Decimal
    sale_currency: str
    estimated_supplier_cost: Decimal | None
    estimated_cost_currency: str | None
    actual_supplier_cost: Decimal | None
    actual_cost_currency: str | None
    gross_profit: Decimal | None




class ProviderBalanceResponse(BaseModel):
    provider_id: uuid.UUID
    provider_name: str
    balance: Decimal | None
    currency: str | None
    low_balance_threshold: Decimal | None
    is_low_balance: bool
    observed_at: datetime | None
    last_error: str | None

class FlexibleDepositAdminResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    provider: str
    status: str
    asset: str | None
    network: str | None
    amount_received: Decimal | None
    fee_amount: Decimal | None
    auto_credit_enabled: bool
    auto_credit_target: str
    credited_amount: Decimal | None
    credited_asset: str | None
    credited_currency: str | None
    last_error: str | None


async def _assert_scope_target(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    req: PricingRuleCreateRequest,
) -> None:
    supplied = [req.category_id is not None, req.product_id is not None, req.product_variant_id is not None]
    expected_count = 0 if req.scope == PricingScope.GLOBAL else 1
    if sum(supplied) != expected_count:
        raise HTTPException(status_code=400, detail="Pricing rule scope must have exactly its matching target.")
    if req.scope == PricingScope.CATEGORY:
        exists = await session.scalar(select(Category.id).where(Category.id == req.category_id, Category.tenant_id == tenant_id))
    elif req.scope == PricingScope.PRODUCT:
        exists = await session.scalar(select(Product.id).where(Product.id == req.product_id, Product.tenant_id == tenant_id))
    elif req.scope == PricingScope.VARIANT:
        exists = await session.scalar(
            select(ProductVariant.id)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(ProductVariant.id == req.product_variant_id, Product.tenant_id == tenant_id)
        )
    else:
        exists = True
    if not exists:
        raise HTTPException(status_code=404, detail="Pricing scope target not found in this tenant.")
    if req.tier_id is not None:
        tier = await session.scalar(select(PricingTier.id).where(PricingTier.id == req.tier_id, PricingTier.tenant_id == tenant_id))
        if tier is None:
            raise HTTPException(status_code=404, detail="Pricing tier not found in this tenant.")


@router.get("/pricing-tiers", response_model=list[PricingTierResponse])
async def list_pricing_tiers(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[PricingTierResponse]:
    items = list((await session.execute(select(PricingTier).where(PricingTier.tenant_id == principal.tenant_id).order_by(PricingTier.priority, PricingTier.code))).scalars().all())
    return [PricingTierResponse.from_model(item) for item in items]


@router.post("/pricing-tiers", response_model=PricingTierResponse, status_code=status.HTTP_201_CREATED)
async def create_pricing_tier(
    req: PricingTierCreateRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> PricingTierResponse:
    code = req.code.strip().lower()
    if not _CODE_RE.fullmatch(code):
        raise HTTPException(status_code=400, detail="Pricing tier code is invalid.")
    if req.is_default:
        existing_default = await session.scalar(select(PricingTier.id).where(PricingTier.tenant_id == principal.tenant_id, PricingTier.is_default.is_(True), PricingTier.is_active.is_(True)))
        if existing_default is not None:
            raise HTTPException(status_code=409, detail="An active default pricing tier already exists.")
    item = PricingTier(tenant_id=principal.tenant_id, code=code, display_name=req.display_name.strip(), priority=req.priority, is_default=req.is_default, is_active=True)
    try:
        async with session.begin_nested():
            session.add(item)
            await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Pricing tier code already exists.") from exc
    audit(session, principal.tenant_id, principal.user_id, "pricing.tier_created", item)
    return PricingTierResponse.from_model(item)


@router.post("/pricing-tier-assignment", status_code=status.HTTP_204_NO_CONTENT)
async def assign_pricing_tier(
    req: TierAssignmentRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    user_is_tenant_scoped = await session.scalar(
        select(User.id).where(
            User.id == req.user_id,
            (
                select(Membership.id)
                .where(Membership.tenant_id == principal.tenant_id, Membership.user_id == req.user_id)
                .exists()
            )
            | (
                select(TenantTelegramUser.id)
                .where(TenantTelegramUser.tenant_id == principal.tenant_id, TenantTelegramUser.user_id == req.user_id)
                .exists()
            ),
        )
    )
    tier = await session.scalar(select(PricingTier.id).where(PricingTier.id == req.tier_id, PricingTier.tenant_id == principal.tenant_id, PricingTier.is_active.is_(True)))
    if user_is_tenant_scoped is None or tier is None:
        raise HTTPException(status_code=404, detail="User or pricing tier not found in this tenant.")
    existing = await session.scalar(select(UserPricingTier).where(UserPricingTier.tenant_id == principal.tenant_id, UserPricingTier.user_id == req.user_id))
    if existing is None:
        session.add(UserPricingTier(tenant_id=principal.tenant_id, user_id=req.user_id, tier_id=req.tier_id))
    else:
        existing.tier_id = req.tier_id
    await session.flush()
    assignment = await session.scalar(select(UserPricingTier).where(UserPricingTier.tenant_id == principal.tenant_id, UserPricingTier.user_id == req.user_id))
    audit(session, principal.tenant_id, principal.user_id, "pricing.tier_assigned", assignment, {"tier_id": str(req.tier_id), "user_id": str(req.user_id)})


@router.get("/pricing-rules", response_model=list[PricingRuleResponse])
async def list_pricing_rules(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[PricingRuleResponse]:
    items = list((await session.execute(select(PricingRule).where(PricingRule.tenant_id == principal.tenant_id).order_by(PricingRule.priority, PricingRule.id))).scalars().all())
    return [PricingRuleResponse.from_model(item) for item in items]


@router.post("/pricing-rules", response_model=PricingRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_pricing_rule(
    req: PricingRuleCreateRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> PricingRuleResponse:
    await _assert_scope_target(session, tenant_id=principal.tenant_id, req=req)
    if req.markup_mode == MarkupMode.PERCENT and req.markup_fixed != 0:
        raise HTTPException(status_code=400, detail="PERCENT rules cannot also carry a fixed markup.")
    if req.markup_mode == MarkupMode.FIXED and req.markup_percent != 0:
        raise HTTPException(status_code=400, detail="FIXED rules cannot also carry a percentage markup.")
    item = PricingRule(
        tenant_id=principal.tenant_id,
        name=req.name.strip(),
        scope=req.scope,
        category_id=req.category_id,
        product_id=req.product_id,
        product_variant_id=req.product_variant_id,
        tier_id=req.tier_id,
        markup_mode=req.markup_mode,
        markup_percent=req.markup_percent,
        markup_fixed=req.markup_fixed,
        minimum_margin=req.minimum_margin,
        rounding_increment=req.rounding_increment,
        priority=req.priority,
        is_active=True,
    )
    session.add(item)
    await session.flush()
    audit(session, principal.tenant_id, principal.user_id, "pricing.rule_created", item)
    return PricingRuleResponse.from_model(item)


@router.get("/fx-policies", response_model=list[FxPolicyResponse])
async def list_fx_policies(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[FxPolicyResponse]:
    items = list((await session.execute(select(FxPolicy).where(FxPolicy.tenant_id == principal.tenant_id).order_by(FxPolicy.from_asset, FxPolicy.from_network, FxPolicy.to_currency))).scalars().all())
    return [FxPolicyResponse.from_model(item) for item in items]


@router.post("/fx-policies", response_model=FxPolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_fx_policy(
    req: FxPolicyCreateRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> FxPolicyResponse:
    try:
        item = await FxPolicyService.create_policy(
            session,
            tenant_id=principal.tenant_id,
            from_asset=req.from_asset,
            from_network=req.from_network,
            to_currency=req.to_currency,
            mode=req.mode,
            rate=req.rate,
            max_auto_credit_amount=req.max_auto_credit_amount,
        )
        return FxPolicyResponse.from_model(item)
    except (ValueError, LedgerIntegrityError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/order-economics", response_model=list[OrderEconomicsResponse])
async def list_order_economics(
    limit: int = Query(100, ge=1, le=500),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[OrderEconomicsResponse]:
    items = list((await session.execute(select(OrderItemEconomics).where(OrderItemEconomics.tenant_id == principal.tenant_id).order_by(OrderItemEconomics.created_at.desc()).limit(limit))).scalars().all())
    return [OrderEconomicsResponse(**{name: getattr(item, name) for name in OrderEconomicsResponse.model_fields}) for item in items]


@router.get("/provider-balances", response_model=list[ProviderBalanceResponse])
async def list_provider_balances(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[ProviderBalanceResponse]:
    rows = (
        await session.execute(
            select(ProviderBalanceSnapshot, Provider.name)
            .join(Provider, Provider.id == ProviderBalanceSnapshot.provider_id)
            .where(ProviderBalanceSnapshot.tenant_id == principal.tenant_id)
            .order_by(ProviderBalanceSnapshot.is_low_balance.desc(), Provider.name.asc())
        )
    ).all()
    return [
        ProviderBalanceResponse(
            provider_id=snapshot.provider_id,
            provider_name=name,
            balance=snapshot.balance,
            currency=snapshot.currency,
            low_balance_threshold=snapshot.low_balance_threshold,
            is_low_balance=snapshot.is_low_balance,
            observed_at=snapshot.observed_at,
            last_error=snapshot.last_error,
        )
        for snapshot, name in rows
    ]


@router.get("/flexible-deposits", response_model=list[FlexibleDepositAdminResponse])
async def list_flexible_deposits(
    limit: int = Query(100, ge=1, le=500),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[FlexibleDepositAdminResponse]:
    items = list((await session.execute(select(FlexibleDepositSession).where(FlexibleDepositSession.tenant_id == principal.tenant_id).order_by(FlexibleDepositSession.created_at.desc()).limit(limit))).scalars().all())
    return [
        FlexibleDepositAdminResponse(
            id=item.id,
            user_id=item.user_id,
            provider=item.provider,
            status=item.status.value,
            asset=item.asset,
            network=item.network,
            amount_received=item.amount_received,
            fee_amount=item.fee_amount,
            auto_credit_enabled=item.auto_credit_enabled,
            auto_credit_target=item.auto_credit_target.value,
            credited_amount=item.credited_amount,
            credited_asset=item.credited_asset,
            credited_currency=item.credited_currency,
            last_error=item.last_error,
        )
        for item in items
    ]


class PricingActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool


@router.patch("/pricing-rules/{rule_id}", response_model=PricingRuleResponse)
async def toggle_pricing_rule(rule_id: uuid.UUID, req: PricingActiveRequest, principal: AuthenticatedPrincipal = Depends(require_admin_or_owner), session: AsyncSession = Depends(get_db_session)):
    item = await session.scalar(select(PricingRule).where(PricingRule.tenant_id == principal.tenant_id, PricingRule.id == rule_id).with_for_update())
    if not item:
        raise HTTPException(404, "Pricing rule not found.")
    item.is_active = req.is_active
    audit(session, principal.tenant_id, principal.user_id, "pricing.rule_status_changed", item, req.model_dump())
    await session.flush()
    return PricingRuleResponse.from_model(item)


@router.patch("/pricing-tiers/{tier_id}", response_model=PricingTierResponse)
async def toggle_pricing_tier(tier_id: uuid.UUID, req: PricingActiveRequest, principal: AuthenticatedPrincipal = Depends(require_admin_or_owner), session: AsyncSession = Depends(get_db_session)):
    item = await session.scalar(select(PricingTier).where(PricingTier.tenant_id == principal.tenant_id, PricingTier.id == tier_id).with_for_update())
    if not item:
        raise HTTPException(404, "Pricing tier not found.")
    # The unique active-default index remains authoritative if activation races.
    item.is_active = req.is_active
    audit(session, principal.tenant_id, principal.user_id, "pricing.tier_status_changed", item, req.model_dump())
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Another default tier is already active.") from exc
    return PricingTierResponse.from_model(item)
