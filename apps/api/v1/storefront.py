import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import get_current_principal
from packages.commerce.checkout import CheckoutLine, CheckoutService
from packages.commerce.models import Category, Order, OrderItem, Product, ProductVariant
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.core.exceptions import InsufficientFundsError
from packages.payments.exceptions import PaymentError, PaymentIntegrityError, PaymentProviderError
from packages.fulfillment.models import FulfillmentAttempt
from packages.payments.models import PaymentIntent, PaymentIntentPurpose, PaymentProviderConfig, Wallet
from packages.payments.payment_service import PaymentService
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


class StorefrontBootstrapResponse(BaseModel):
    store: StoreSummary
    user: UserSummary
    wallets: list[WalletResponse]


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


def get_storefront_payment_service() -> PaymentService:
    return PaymentService()


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

def _variant_response(variant: ProductVariant) -> VariantResponse:
    return VariantResponse(
        id=variant.id,
        sku=variant.sku,
        title=variant.title,
        price=variant.price,
        currency=variant.currency,
        stock_quantity=variant.stock_quantity,
    )


def _product_response(product: Product) -> ProductResponse:
    variants = [
        _variant_response(variant)
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
    visible_products = [_product_response(product) for product in products]
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
        )
        if "items" not in order.__dict__:
            await session.refresh(order, attribute_names=["items"])
        return _order_response(order, fulfillment)
    except InsufficientFundsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
