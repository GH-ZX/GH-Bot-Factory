import hashlib
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import get_current_principal
from packages.commerce.checkout import CheckoutLine, CheckoutService
from packages.commerce.economics import PricingService
from packages.commerce.models import Category, Order, Product, ProductVariant
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.core.exceptions import InsufficientFundsError
from packages.factory.business_profiles import business_profile_from_config
from packages.fulfillment.models import FulfillmentAttempt
from packages.payments.economics_models import AssetWallet, FlexibleDepositSession
from packages.payments.exceptions import PaymentError, PaymentIntegrityError, PaymentProviderError
from packages.payments.flexible_deposits import FlexibleDepositService
from packages.payments.models import (
    PaymentIntent,
    PaymentIntentPurpose,
    PaymentMethodConfig,
    PaymentObservation,
    PaymentObservationSource,
    PaymentProviderConfig,
    Wallet,
)
from packages.payments.payment_service import PaymentService
from packages.payments.platform import PaymentPlatformService
from packages.payments.reconciliation import PaymentReconciliationService
from packages.telegram.models import Bot
from packages.tenants.models import Tenant, User

router = APIRouter(prefix="/storefront", tags=["storefront"])

PUBLIC_PRODUCT_METADATA_KEYS = frozenset(
    {
        "badge",
        "delivery_eta",
        "featured",
        "image_url",
        "thumbnail_url",
    }
)
PUBLIC_TENANT_SETTING_KEYS = frozenset(
    {
        "brand_accent",
        "brand_logo_url",
        "store_description",
        "store_tagline",
        "support_url",
    }
)


def _payment_provider_context(
    request: Request, principal: AuthenticatedPrincipal
) -> dict[str, str]:
    """Build transient provider context without persisting request-identifying data.

    The direct ASGI peer address is used deliberately; forwarded-IP headers are not
    trusted here because proxy trust policy belongs to the deployment boundary. The
    device identifier is pseudonymous and contains no raw tenant/user identifier.
    """
    peer = request.client.host.strip() if request.client and request.client.host else ""
    user_agent = (request.headers.get("user-agent") or "GHBF-Storefront").strip()
    if not user_agent:
        user_agent = "GHBF-Storefront"
    browser_version = user_agent[:512]
    digest_source = f"{principal.tenant_id}:{principal.user_id}:{user_agent}".encode()
    device = "ghbf-" + hashlib.sha256(digest_source).hexdigest()[:32]
    return {
        "device": device,
        "browser_version": browser_version,
        "ip": peer,
    }


class UserSummary(BaseModel):
    id: uuid.UUID
    first_name: str | None
    last_name: str | None
    username: str | None


class StoreSummary(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    settings: dict[str, Any]


class WalletResponse(BaseModel):
    currency: str
    balance: Decimal


class AssetWalletResponse(BaseModel):
    asset: str
    network: str
    balance: Decimal


class StorefrontBootstrapResponse(BaseModel):
    store: StoreSummary
    user: UserSummary
    wallets: list[WalletResponse]
    asset_wallets: list[AssetWalletResponse] = Field(default_factory=list)


class TopUpProviderOption(BaseModel):
    provider_name: str
    display_name: str
    min_amount: Decimal
    max_amount: Decimal
    currencies: list[str]
    checkout_mode: str = "external"
    whole_units_only: bool = False
    terms_required: bool = False
    terms_url: str | None = None


class TopUpOptionsResponse(BaseModel):
    providers: list[TopUpProviderOption]


class PaymentMethodOption(BaseModel):
    id: uuid.UUID
    code: str
    display_name: str
    method_type: str
    verification_mode: str
    asset: str | None
    network: str | None
    destination_address: str | None
    destination_memo: str | None
    instructions: str | None
    min_amount: Decimal
    max_amount: Decimal
    currencies: list[str]
    requires_admin_approval: bool
    flexible_deposits_enabled: bool = False
    auto_credit_enabled: bool = False
    auto_credit_target: str = "ASSET_WALLET"


class PaymentMethodOptionsResponse(BaseModel):
    methods: list[PaymentMethodOption]


class FlexibleDepositCreateRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_method_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=100)


class FlexibleDepositResponse(BaseModel):
    id: uuid.UUID
    payment_method_id: uuid.UUID
    provider: str
    status: str
    checkout_url: str | None
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
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, deposit: FlexibleDepositSession) -> "FlexibleDepositResponse":
        target = deposit.auto_credit_target.value if hasattr(deposit.auto_credit_target, "value") else str(deposit.auto_credit_target)
        return cls(
            id=deposit.id,
            payment_method_id=deposit.payment_method_id,
            provider=deposit.provider,
            status=deposit.status.value,
            checkout_url=deposit.checkout_url,
            asset=deposit.asset,
            network=deposit.network,
            amount_received=deposit.amount_received,
            fee_amount=deposit.fee_amount,
            auto_credit_enabled=deposit.auto_credit_enabled,
            auto_credit_target=target,
            credited_amount=deposit.credited_amount,
            credited_asset=deposit.credited_asset,
            credited_currency=deposit.credited_currency,
            last_error=deposit.last_error,
            created_at=deposit.created_at,
            updated_at=deposit.updated_at,
        )


class LocalWalletTopUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=Decimal("0.00"), max_digits=12, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    payment_method_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=100)


class PaymentObservationSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: PaymentObservationSource
    external_reference: str | None = Field(default=None, max_length=255)
    asset_amount: Decimal | None = Field(default=None, gt=Decimal(0), max_digits=36, decimal_places=18)
    note: str | None = Field(default=None, max_length=500)


class StorefrontPaymentObservationResponse(BaseModel):
    id: uuid.UUID
    payment_intent_id: uuid.UUID
    status: str
    source: str
    external_reference: str | None
    asset: str | None
    network: str | None
    asset_amount: Decimal | None
    is_final: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, observation: PaymentObservation) -> "StorefrontPaymentObservationResponse":
        return cls(
            id=observation.id,
            payment_intent_id=observation.payment_intent_id,
            status=observation.status.value,
            source=observation.source.value,
            external_reference=observation.external_reference,
            asset=observation.asset,
            network=observation.network,
            asset_amount=observation.asset_amount,
            is_final=observation.is_final,
            created_at=observation.created_at,
            updated_at=observation.updated_at,
        )


class WalletTopUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=Decimal("0.00"), max_digits=12, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    provider_name: str = Field(min_length=1, max_length=50)
    idempotency_key: str = Field(min_length=8, max_length=100)
    terms_accepted: bool = False


class WalletTopUpResponse(BaseModel):
    id: uuid.UUID
    status: str
    purpose: str
    provider: str
    amount: Decimal
    currency: str
    checkout_url: str | None
    payment_method_id: uuid.UUID | None = None
    payment_instructions: dict[str, Any] | None = None
    wallet_balance: Decimal
    created_at: datetime
    updated_at: datetime


class CategoryResponse(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    slug: str


class VariantResponse(BaseModel):
    id: uuid.UUID
    sku: str
    title: str
    price: Decimal
    currency: str
    stock_quantity: int


class ProductResponse(BaseModel):
    id: uuid.UUID
    category_id: uuid.UUID | None
    title: str
    description: str | None
    metadata: dict[str, Any]
    variants: list[VariantResponse]


class CatalogResponse(BaseModel):
    categories: list[CategoryResponse]
    products: list[ProductResponse]
    query: str | None
    offset: int
    limit: int
    total: int
    has_more: bool
    next_offset: int | None


class CheckoutLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: uuid.UUID
    quantity: int = Field(ge=1, le=100)


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CheckoutLineRequest] = Field(min_length=1, max_length=50)
    recipient: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=8, max_length=100)


class OrderItemResponse(BaseModel):
    id: uuid.UUID
    product_variant_id: uuid.UUID
    quantity: int
    unit_price: Decimal
    total_price: Decimal


class FulfillmentSummary(BaseModel):
    id: uuid.UUID
    status: str
    external_order_id: str | None


class OrderResponse(BaseModel):
    id: uuid.UUID
    order_number: str
    status: str
    total_amount: Decimal
    currency: str
    created_at: datetime
    items: list[OrderItemResponse]
    fulfillment: FulfillmentSummary | None = None


def get_checkout_service() -> CheckoutService:
    return CheckoutService()


def get_pricing_service() -> PricingService:
    return PricingService()


def get_storefront_payment_service() -> PaymentService:
    return PaymentService()


def get_flexible_deposit_service(
    payment_service: PaymentService = Depends(get_storefront_payment_service),
) -> FlexibleDepositService:
    return FlexibleDepositService(payment_service=payment_service)


def get_storefront_payment_platform_service(
    payment_service: PaymentService = Depends(get_storefront_payment_service),
) -> PaymentPlatformService:
    return PaymentPlatformService(payment_service=payment_service)


def get_storefront_reconciliation_service(
    payment_service: PaymentService = Depends(get_storefront_payment_service),
) -> PaymentReconciliationService:
    return PaymentReconciliationService(payment_service=payment_service)


def _public_product_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = metadata or {}
    return {key: metadata[key] for key in PUBLIC_PRODUCT_METADATA_KEYS if key in metadata}


def _public_tenant_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    settings = settings or {}
    return {key: settings[key] for key in PUBLIC_TENANT_SETTING_KEYS if key in settings}




def _bot_public_store_settings(bot: Bot | None) -> dict[str, Any]:
    if bot is None:
        return {}
    config = bot.config or {}
    branding = config.get("branding") if isinstance(config, dict) else None
    if not isinstance(branding, dict):
        return {}
    return {key: branding[key] for key in PUBLIC_TENANT_SETTING_KEYS if key in branding}

def _variant_response(variant: ProductVariant, *, effective_price: Decimal | None = None) -> VariantResponse:
    return VariantResponse(
        id=variant.id,
        sku=variant.sku,
        title=variant.title,
        price=effective_price if effective_price is not None else variant.price,
        currency=variant.currency,
        stock_quantity=variant.stock_quantity,
    )


def _product_response(
    product: Product, *, effective_prices: dict[uuid.UUID, Decimal] | None = None
) -> ProductResponse:
    price_map = effective_prices or {}
    variants = [
        _variant_response(variant, effective_price=price_map.get(variant.id))
        for variant in product.variants
        if variant.is_active and variant.deleted_at is None
    ]
    variants.sort(key=lambda item: (item.price, item.title.lower()))
    return ProductResponse(
        id=product.id,
        category_id=product.category_id,
        title=product.title,
        description=product.description,
        metadata=_public_product_metadata(product.metadata_json),
        variants=variants,
    )


def _order_response(
    order: Order,
    fulfillment: FulfillmentAttempt | None = None,
) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        order_number=order.order_number,
        status=order.status.value,
        total_amount=order.total_amount,
        currency=order.currency,
        created_at=order.created_at,
        items=[
            OrderItemResponse(
                id=item.id,
                product_variant_id=item.product_variant_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                total_price=item.total_price,
            )
            for item in order.items
        ],
        fulfillment=(
            FulfillmentSummary(
                id=fulfillment.id,
                status=fulfillment.status.value,
                external_order_id=fulfillment.external_order_id,
            )
            if fulfillment is not None
            else None
        ),
    )


def _topup_policy(config: PaymentProviderConfig) -> TopUpProviderOption | None:
    policy = config.settings_json or {}
    if policy.get("topup_enabled") is False:
        return None
    try:
        min_amount = Decimal(str(policy.get("topup_min_amount", "1.00")))
        max_amount = Decimal(str(policy.get("topup_max_amount", "1000.00")))
    except Exception:  # noqa: BLE001
        return None
    currencies = sorted(
        {
            str(value).strip().upper()
            for value in policy.get("topup_currencies", ["USD"])
            if len(str(value).strip()) == 3
        }
    )
    if not currencies or min_amount <= Decimal("0.00") or max_amount < min_amount:
        return None
    terms_required = bool(policy.get("terms_required", False))
    terms_url = str(policy.get("terms_url") or "").strip() or None
    if terms_required and (terms_url is None or not terms_url.startswith("https://")):
        return None
    return TopUpProviderOption(
        provider_name=config.provider_name,
        display_name=str(policy.get("display_name") or config.provider_name.title()),
        min_amount=min_amount,
        max_amount=max_amount,
        currencies=currencies,
        checkout_mode=str(policy.get("checkout_mode") or "external"),
        whole_units_only=bool(policy.get("topup_whole_units_only", False)),
        terms_required=terms_required,
        terms_url=terms_url,
    )


def _public_payment_instructions(intent: PaymentIntent) -> dict[str, Any] | None:
    metadata = intent.metadata_json or {}
    local = metadata.get("payment_instruction")
    if isinstance(local, dict):
        allowed_local = {
            "method_code",
            "display_name",
            "method_type",
            "verification_mode",
            "asset",
            "network",
            "destination_address",
            "destination_memo",
            "instructions",
        }
        result = {key: local[key] for key in allowed_local if key in local}
        if result:
            result["mode"] = "local"
            return result

    provider_create = metadata.get("provider_create")
    if isinstance(provider_create, dict):
        allowed_provider = {
            "pay_address",
            "payin_extra_id",
            "pay_amount",
            "pay_currency",
            "expiration_estimate_date",
            "qr_content",
        }
        result = {
            key: provider_create[key]
            for key in allowed_provider
            if key in provider_create and provider_create[key] is not None
        }
        if result:
            result["mode"] = "provider_direct"
            return result
    return None


async def _wallet_topup_response(
    session: AsyncSession,
    intent: PaymentIntent,
) -> WalletTopUpResponse:
    wallet_stmt = select(Wallet).where(
        Wallet.tenant_id == intent.tenant_id,
        Wallet.user_id == intent.user_id,
        Wallet.currency == intent.currency,
        Wallet.is_active.is_(True),
    )
    wallet = (await session.execute(wallet_stmt)).scalar_one_or_none()
    return WalletTopUpResponse(
        id=intent.id,
        status=intent.status.value,
        purpose=intent.purpose.value,
        provider=intent.provider,
        amount=intent.amount,
        currency=intent.currency,
        checkout_url=intent.checkout_url,
        payment_method_id=intent.payment_method_id,
        payment_instructions=_public_payment_instructions(intent),
        wallet_balance=wallet.balance if wallet is not None else Decimal("0.00"),
        created_at=intent.created_at,
        updated_at=intent.updated_at,
    )


def _assert_customer_topup_access(
    intent: PaymentIntent,
    principal: AuthenticatedPrincipal,
) -> None:
    if intent.purpose != PaymentIntentPurpose.WALLET_TOPUP:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet top-up not found.")
    if intent.tenant_id != principal.tenant_id or intent.user_id != principal.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet top-up not found.")


async def _bot_business_context(
    session: AsyncSession, principal: AuthenticatedPrincipal
):
    if principal.bot_id is None:
        return None, business_profile_from_config(None)
    bot = await session.scalar(select(Bot).where(
        Bot.id == principal.bot_id, Bot.tenant_id == principal.tenant_id,
        Bot.deleted_at.is_(None), Bot.is_enabled.is_(True),
    ))
    if bot is None:
        raise HTTPException(status_code=403, detail="This bot is no longer available. Sign in again.")
    return bot, business_profile_from_config(bot.config)


async def _legacy_funding_allowed(session: AsyncSession, principal: AuthenticatedPrincipal) -> bool:
    bot, _profile = await _bot_business_context(session, principal)
    # Business-profile bots must fund through method-based routes, which apply method
    # identity, asset/network, and approval policy. Provider-name routing loses that policy.
    return bot is None or "_business" not in (bot.config or {})


async def _assert_bot_payment_method_allowed(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    payment_method_id: uuid.UUID,
):
    _bot, profile = await _bot_business_context(session, principal)
    if profile.payment_method_ids and payment_method_id not in profile.payment_method_ids:
        raise PaymentError("This payment method is not enabled for the current bot.")
    return profile


def _payment_method_option(method: PaymentMethodConfig, *, allow_auto_credit: bool = True) -> PaymentMethodOption | None:
    settings = method.settings_json or {}
    try:
        min_amount = Decimal(str(settings.get("topup_min_amount", "1.00")))
        max_amount = Decimal(str(settings.get("topup_max_amount", "1000.00")))
    except Exception:  # noqa: BLE001
        return None
    currencies = sorted(
        {
            str(value).strip().upper()
            for value in settings.get("topup_currencies", ["USD"])
            if len(str(value).strip()) == 3
        }
    )
    if min_amount <= 0 or max_amount < min_amount or not currencies:
        return None
    return PaymentMethodOption(
        id=method.id,
        code=method.code,
        display_name=method.display_name,
        method_type=method.method_type.value,
        verification_mode=method.verification_mode.value,
        asset=method.asset,
        network=method.network,
        destination_address=method.destination_address,
        destination_memo=method.destination_memo,
        instructions=method.instructions,
        min_amount=min_amount,
        max_amount=max_amount,
        currencies=currencies,
        requires_admin_approval=method.requires_admin_approval,
        flexible_deposits_enabled=bool((method.settings_json or {}).get("flexible_deposits_enabled", False)),
        auto_credit_enabled=bool(method.auto_credit_enabled and allow_auto_credit),
        auto_credit_target=method.auto_credit_target,
    )


@router.get("/wallet/payment-methods", response_model=PaymentMethodOptionsResponse)
async def wallet_payment_methods(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    service: PaymentPlatformService = Depends(get_storefront_payment_platform_service),
) -> PaymentMethodOptionsResponse:
    methods = await service.list_methods(
        session, tenant_id=principal.tenant_id, enabled_only=True
    )
    _bot, profile = await _bot_business_context(session, principal)
    if profile.payment_method_ids:
        methods = [method for method in methods if method.id in profile.payment_method_ids]
    return PaymentMethodOptionsResponse(
        methods=[
            option
            for method in methods
            if (
                option := _payment_method_option(
                    method, allow_auto_credit=profile.allow_flexible_auto_credit
                )
            ) is not None
        ]
    )


@router.post(
    "/wallet/topups/method",
    response_model=WalletTopUpResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_method_wallet_topup(
    req: LocalWalletTopUpRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    service: PaymentPlatformService = Depends(get_storefront_payment_platform_service),
) -> WalletTopUpResponse:
    """Create a top-up using the selected Phase 11 payment method.

    Provider-backed methods are initialized at the gateway; manual/self-custody methods
    remain local evidence workflows. Neither path can directly credit the wallet.
    """
    if not principal.is_customer():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Wallet top-up requires a customer session.",
        )
    try:
        await _assert_bot_payment_method_allowed(
            session, principal=principal, payment_method_id=req.payment_method_id
        )
        intent = await service.create_topup_intent(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            method_id=req.payment_method_id,
            amount=req.amount,
            currency=req.currency,
            idempotency_key=req.idempotency_key,
            provider_context=_payment_provider_context(request, principal),
        )
        return await _wallet_topup_response(session, intent)
    except PaymentIntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (PaymentError, PaymentProviderError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/wallet/flexible-deposits",
    response_model=FlexibleDepositResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_flexible_deposit(
    req: FlexibleDepositCreateRequestModel,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    service: FlexibleDepositService = Depends(get_flexible_deposit_service),
) -> FlexibleDepositResponse:
    if not principal.is_customer():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer session required.")
    try:
        profile = await _assert_bot_payment_method_allowed(
            session, principal=principal, payment_method_id=req.payment_method_id
        )
        deposit = await service.create(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            payment_method_id=req.payment_method_id,
            idempotency_key=req.idempotency_key,
            allow_auto_credit=profile.allow_flexible_auto_credit,
        )
        await session.commit()
        return FlexibleDepositResponse.from_model(deposit)
    except (PaymentError, PaymentIntegrityError, PaymentProviderError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/wallet/flexible-deposits/{deposit_id}", response_model=FlexibleDepositResponse)
async def get_flexible_deposit(
    deposit_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    service: FlexibleDepositService = Depends(get_flexible_deposit_service),
) -> FlexibleDepositResponse:
    try:
        deposit = await service.get(session, tenant_id=principal.tenant_id, deposit_id=deposit_id)
        if principal.is_customer() and deposit.user_id != principal.user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flexible deposit not found.")
        return FlexibleDepositResponse.from_model(deposit)
    except PaymentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flexible deposit not found.") from exc


@router.post("/wallet/flexible-deposits/{deposit_id}/reconcile", response_model=FlexibleDepositResponse)
async def reconcile_flexible_deposit(
    deposit_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    service: FlexibleDepositService = Depends(get_flexible_deposit_service),
) -> FlexibleDepositResponse:
    try:
        deposit = await service.get(session, tenant_id=principal.tenant_id, deposit_id=deposit_id)
        if principal.is_customer() and deposit.user_id != principal.user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flexible deposit not found.")
        deposit = await service.reconcile(session, tenant_id=principal.tenant_id, deposit_id=deposit_id)
        await session.commit()
        return FlexibleDepositResponse.from_model(deposit)
    except HTTPException:
        raise
    except (PaymentError, PaymentIntegrityError, PaymentProviderError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/wallet/topups/local",
    response_model=WalletTopUpResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_local_wallet_topup(
    req: LocalWalletTopUpRequest,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    service: PaymentPlatformService = Depends(get_storefront_payment_platform_service),
) -> WalletTopUpResponse:
    if not principal.is_customer():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Wallet top-up requires a customer session.",
        )
    try:
        await _assert_bot_payment_method_allowed(
            session, principal=principal, payment_method_id=req.payment_method_id
        )
        intent = await service.create_local_topup_intent(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            method_id=req.payment_method_id,
            amount=req.amount,
            currency=req.currency,
            idempotency_key=req.idempotency_key,
        )
        return await _wallet_topup_response(session, intent)
    except (PaymentError, PaymentIntegrityError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/wallet/topups/{intent_id}/observations",
    response_model=StorefrontPaymentObservationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_wallet_topup_observation(
    intent_id: uuid.UUID,
    req: PaymentObservationSubmitRequest,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    service: PaymentPlatformService = Depends(get_storefront_payment_platform_service),
) -> StorefrontPaymentObservationResponse:
    if not principal.is_customer():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer session required.")
    try:
        observation = await service.submit_observation(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            intent_id=intent_id,
            source=req.source,
            external_reference=req.external_reference,
            asset_amount=req.asset_amount,
            details={"note": req.note} if req.note else {},
        )
        return StorefrontPaymentObservationResponse.from_model(observation)
    except (PaymentError, PaymentIntegrityError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/bootstrap", response_model=StorefrontBootstrapResponse)
async def storefront_bootstrap(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> StorefrontBootstrapResponse:
    """Return authenticated Mini App store, user, and wallet context."""
    tenant = await session.get(Tenant, principal.tenant_id)
    user = await session.get(User, principal.user_id)
    bot = None
    if principal.bot_id is not None:
        candidate = await session.get(Bot, principal.bot_id)
        if candidate is not None and candidate.tenant_id == principal.tenant_id and candidate.deleted_at is None:
            bot = candidate
    if tenant is None or user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Authenticated storefront context was not found.",
        )

    wallet_stmt = (
        select(Wallet)
        .where(
            Wallet.tenant_id == principal.tenant_id,
            Wallet.user_id == principal.user_id,
            Wallet.is_active.is_(True),
        )
        .order_by(Wallet.currency.asc())
    )
    wallets = list((await session.execute(wallet_stmt)).scalars().all())
    asset_wallet_stmt = (
        select(AssetWallet)
        .where(
            AssetWallet.tenant_id == principal.tenant_id,
            AssetWallet.user_id == principal.user_id,
            AssetWallet.is_active.is_(True),
        )
        .order_by(AssetWallet.asset.asc(), AssetWallet.network.asc())
    )
    asset_wallets = list((await session.execute(asset_wallet_stmt)).scalars().all())

    return StorefrontBootstrapResponse(
        store=StoreSummary(
            id=tenant.id,
            name=bot.display_name if bot is not None else tenant.name,
            slug=tenant.slug,
            settings={
                **_public_tenant_settings(tenant.settings),
                **_bot_public_store_settings(bot),
            },
        ),
        user=UserSummary(
            id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
        ),
        wallets=[
            WalletResponse(currency=wallet.currency, balance=wallet.balance)
            for wallet in wallets
        ],
        asset_wallets=[
            AssetWalletResponse(asset=wallet.asset, network=wallet.network, balance=wallet.balance)
            for wallet in asset_wallets
        ],
    )


@router.get("/catalog", response_model=CatalogResponse)
async def list_catalog(
    category_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, max_length=100),
    available: bool | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    pricing_service: PricingService = Depends(get_pricing_service),
) -> CatalogResponse:
    """Return a searchable, paginated, tenant-scoped active storefront catalog."""
    category_stmt = (
        select(Category)
        .where(
            Category.tenant_id == principal.tenant_id,
            Category.is_active.is_(True),
            Category.deleted_at.is_(None),
        )
        .order_by(Category.name.asc())
    )
    categories = list((await session.execute(category_stmt)).scalars().all())

    active_variant = and_(
        ProductVariant.is_active.is_(True),
        ProductVariant.deleted_at.is_(None),
    )
    available_variant = and_(active_variant, ProductVariant.stock_quantity > 0)
    filters = [
        Product.tenant_id == principal.tenant_id,
        Product.is_active.is_(True),
        Product.deleted_at.is_(None),
        Product.variants.any(active_variant),
    ]
    if category_id is not None:
        filters.append(Product.category_id == category_id)

    normalized_query = (q or "").strip() or None
    if normalized_query is not None:
        pattern = f"%{normalized_query}%"
        filters.append(
            or_(
                Product.title.ilike(pattern),
                Product.description.ilike(pattern),
                Product.variants.any(
                    and_(
                        active_variant,
                        or_(
                            ProductVariant.title.ilike(pattern),
                            ProductVariant.sku.ilike(pattern),
                        ),
                    )
                ),
            )
        )

    if available is True:
        filters.append(Product.variants.any(available_variant))
    elif available is False:
        filters.append(~Product.variants.any(available_variant))

    total = int(
        (
            await session.execute(
                select(func.count(Product.id)).where(*filters)
            )
        ).scalar_one()
    )
    product_stmt = (
        select(Product)
        .where(*filters)
        .options(selectinload(Product.variants))
        .order_by(Product.title.asc(), Product.id.asc())
        .offset(offset)
        .limit(limit)
    )
    products = list((await session.execute(product_stmt)).scalars().unique().all())
    effective_prices: dict[uuid.UUID, Decimal] = {}
    for product in products:
        for variant in product.variants:
            if not variant.is_active or variant.deleted_at is not None:
                continue
            decision = await pricing_service.decide(
                session,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                variant=variant,
                bot_id=principal.bot_id,
            )
            effective_prices[variant.id] = decision.sell_price
    visible_products = [
        _product_response(product, effective_prices=effective_prices) for product in products
    ]
    next_offset = offset + len(visible_products)
    has_more = next_offset < total

    return CatalogResponse(
        categories=[
            CategoryResponse(
                id=category.id,
                parent_id=category.parent_id,
                name=category.name,
                slug=category.slug,
            )
            for category in categories
        ],
        products=visible_products,
        query=normalized_query,
        offset=offset,
        limit=limit,
        total=total,
        has_more=has_more,
        next_offset=next_offset if has_more else None,
    )


@router.get("/orders", response_model=list[OrderResponse])
async def list_orders(
    limit: int = Query(20, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[OrderResponse]:
    """Return orders visible to the authenticated principal inside its tenant."""
    stmt = (
        select(Order)
        .where(Order.tenant_id == principal.tenant_id)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    if principal.is_customer():
        stmt = stmt.where(Order.user_id == principal.user_id)

    orders = list((await session.execute(stmt)).scalars().unique().all())
    return [_order_response(order) for order in orders]


@router.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> OrderResponse:
    """Return one tenant-scoped order, enforcing customer ownership."""
    stmt = (
        select(Order)
        .where(Order.id == order_id, Order.tenant_id == principal.tenant_id)
        .options(selectinload(Order.items))
    )
    order = (await session.execute(stmt)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found.")
    if principal.is_customer() and order.user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer cannot access another user's order.",
        )
    return _order_response(order)


@router.get("/wallet/topups/options", response_model=TopUpOptionsResponse)
async def wallet_topup_options(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    payment_service: PaymentService = Depends(get_storefront_payment_service),
) -> TopUpOptionsResponse:
    """Return public, tenant-scoped wallet funding policies without exposing credentials."""
    if not await _legacy_funding_allowed(session, principal):
        return TopUpOptionsResponse(providers=[])
    stmt = (
        select(PaymentProviderConfig)
        .where(
            PaymentProviderConfig.tenant_id == principal.tenant_id,
            PaymentProviderConfig.is_enabled.is_(True),
        )
        .order_by(PaymentProviderConfig.provider_name.asc())
    )
    configs = list((await session.execute(stmt)).scalars().all())
    providers = [
        option
        for config in configs
        if payment_service.registry.has_provider(principal.tenant_id, config.provider_name)
        if (option := _topup_policy(config)) is not None
    ]
    return TopUpOptionsResponse(providers=providers)


@router.post(
    "/wallet/topups",
    response_model=WalletTopUpResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_wallet_topup(
    req: WalletTopUpRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    payment_service: PaymentService = Depends(get_storefront_payment_service),
) -> WalletTopUpResponse:
    """Create an idempotent external payment intent that funds the customer's wallet."""
    if not principal.is_customer():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Wallet top-up requires a customer session.",
        )
    try:
        if not await _legacy_funding_allowed(session, principal):
            raise PaymentError("Choose a payment method for this bot instead of a legacy provider top-up.")
        intent = await payment_service.create_wallet_topup_intent(
            session=session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            amount=req.amount,
            currency=req.currency,
            provider_name=req.provider_name,
            idempotency_key=req.idempotency_key,
            metadata={
                "source": "telegram_miniapp",
                "terms_accepted": req.terms_accepted,
            },
        )
        return await _wallet_topup_response(session, intent)
    except PaymentIntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (PaymentError, PaymentProviderError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/wallet/topups/{intent_id}", response_model=WalletTopUpResponse)
async def get_wallet_topup(
    intent_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    payment_service: PaymentService = Depends(get_storefront_payment_service),
) -> WalletTopUpResponse:
    """Return one customer-owned wallet top-up without mutating provider state."""
    try:
        intent = await payment_service.get_payment_intent(session, principal.tenant_id, intent_id)
    except Exception as exc:  # narrowed to 404 to avoid tenant existence disclosure
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet top-up not found.") from exc
    _assert_customer_topup_access(intent, principal)
    return await _wallet_topup_response(session, intent)


@router.post("/wallet/topups/{intent_id}/reconcile", response_model=WalletTopUpResponse)
async def reconcile_wallet_topup(
    intent_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    payment_service: PaymentService = Depends(get_storefront_payment_service),
    reconciliation_service: PaymentReconciliationService = Depends(
        get_storefront_reconciliation_service
    ),
) -> WalletTopUpResponse:
    """Synchronize a customer-owned top-up with its provider and return current balance."""
    try:
        intent = await payment_service.get_payment_intent(session, principal.tenant_id, intent_id)
        _assert_customer_topup_access(intent, principal)
        if intent.status.value not in {"SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"}:
            intent = await reconciliation_service.reconcile_intent(
                session, principal.tenant_id, intent.id
            )
        return await _wallet_topup_response(session, intent)
    except HTTPException:
        raise
    except PaymentIntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (PaymentError, PaymentProviderError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/checkout", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def wallet_checkout(
    req: CheckoutRequest,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
    checkout_service: CheckoutService = Depends(get_checkout_service),
) -> OrderResponse:
    """Debit the authenticated customer's wallet and fulfill an authoritative cart."""
    if not principal.is_customer():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Storefront checkout requires a customer session.",
        )

    try:
        order, fulfillment = await checkout_service.checkout_cart(
            session=session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            lines=[
                CheckoutLine(variant_id=item.variant_id, quantity=item.quantity)
                for item in req.items
            ],
            recipient=req.recipient,
            idempotency_key=req.idempotency_key,
            execute_sync=False,
            enqueue_durable=True,
            bot_id=principal.bot_id,
        )
        if "items" not in order.__dict__:
            await session.refresh(order, attribute_names=["items"])
        return _order_response(order, fulfillment)
    except InsufficientFundsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
