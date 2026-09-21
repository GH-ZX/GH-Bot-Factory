import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import require_admin_or_owner, require_manager_or_above, require_staff_or_above
from packages.commerce.models import Category, Order, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.fulfillment.models import (
    FulfillmentAttempt,
    FulfillmentJobRecord,
    FulfillmentJobStatus,
    FulfillmentStatus,
)
from packages.fulfillment.operations import (
    FulfillmentOperationError,
    RequeueDecision,
    evaluate_manual_requeue,
    requeue_dead_letter_job,
)
from packages.fulfillment.reconciliation import ReconciliationService
from packages.payments.models import (
    FinancialResolutionCase,
    FinancialResolutionCaseStatus,
    PaymentReconciliationEvent,
)
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminActor(BaseModel):
    id: uuid.UUID
    username: str | None
    first_name: str | None
    last_name: str | None
    role: Role


class AdminStore(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class AdminCounts(BaseModel):
    products: int
    active_products: int
    orders: int
    attention_orders: int
    dead_letter_jobs: int
    reconciliation_reviews: int
    financial_open_cases: int


class AdminBootstrapResponse(BaseModel):
    actor: AdminActor
    store: AdminStore
    counts: AdminCounts


class CategoryResponse(BaseModel):
    sort_order: int = Field(default=0, ge=0, le=100000)
    id: uuid.UUID
    name: str
    slug: str
    parent_id: uuid.UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CategoryCreateRequest(BaseModel):
    sort_order: int = Field(default=0, ge=0, le=100000)
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    parent_id: uuid.UUID | None = None
    is_active: bool = True


class CategoryUpdateRequest(BaseModel):
    sort_order: int = Field(default=0, ge=0, le=100000)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    parent_id: uuid.UUID | None = None
    is_active: bool | None = None


class VariantCreateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=100)
    price: Decimal = Field(gt=Decimal(0), max_digits=12, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    stock_quantity: int = Field(default=0, ge=0)
    is_active: bool = True
    attributes: dict[str, Any] = Field(default_factory=dict)


class VariantUpdateRequest(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=100)
    price: Decimal | None = Field(default=None, gt=Decimal(0), max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    stock_quantity: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
    attributes: dict[str, Any] | None = None


class VariantResponse(BaseModel):
    id: uuid.UUID
    sku: str
    title: str
    price: Decimal
    currency: str
    stock_quantity: int
    is_active: bool
    attributes: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


def validate_product_metadata(value):
    if value is None:
        return value
    from urllib.parse import urlsplit
    image = value.get("image_url")
    if image:
        if not isinstance(image, str) or len(image) > 2000:
            raise ValueError("Image URL must be a valid HTTPS URL.")
        parsed = urlsplit(image)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Image URL must use HTTPS without embedded credentials.")
    days = value.get("warranty_days", 0)
    if type(days) is not int or not 0 <= days <= 3650:
        raise ValueError("Warranty duration must be between 0 and 3650 whole days.")
    terms = value.get("warranty_terms", "")
    if not isinstance(terms, str) or len(terms) > 2000:
        raise ValueError("Warranty terms must be text of at most 2000 characters.")
    if days and not terms.strip():
        raise ValueError("Describe the warranty terms before enabling a warranty.")
    return value


class ProductCreateRequest(BaseModel):
    sort_order: int = Field(default=0, ge=0, le=100000)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    category_id: uuid.UUID | None = None
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    _validate_metadata = field_validator("metadata")(validate_product_metadata)
    variants: list[VariantCreateRequest] = Field(default_factory=list, max_length=50)


class ProductUpdateRequest(BaseModel):
    sort_order: int = Field(default=0, ge=0, le=100000)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    category_id: uuid.UUID | None = None
    is_active: bool | None = None
    metadata: dict[str, Any] | None = None
    _validate_metadata = field_validator("metadata")(validate_product_metadata)


class ProductResponse(BaseModel):
    sort_order: int = Field(default=0, ge=0, le=100000)
    id: uuid.UUID
    title: str
    description: str | None
    category_id: uuid.UUID | None
    category_name: str | None
    is_active: bool
    metadata: dict[str, Any]
    variants: list[VariantResponse]
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    products: list[ProductResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


class AdminOrderItemResponse(BaseModel):
    id: uuid.UUID
    variant_id: uuid.UUID
    quantity: int
    unit_price: Decimal
    total_price: Decimal


class AdminOrderResponse(BaseModel):
    id: uuid.UUID
    order_number: str
    user_id: uuid.UUID
    customer_name: str
    customer_username: str | None
    status: OrderStatus
    total_amount: Decimal
    currency: str
    created_at: datetime
    items: list[AdminOrderItemResponse]
    fulfillment_status: FulfillmentJobStatus | None


class AdminOrderListResponse(BaseModel):
    orders: list[AdminOrderResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


class AdminReconciliationEventResponse(BaseModel):
    id: uuid.UUID
    provider: str
    event_type: str
    provider_event_id: str
    payment_intent_id: uuid.UUID | None
    status: str
    amount: Decimal
    currency: str
    classification: str
    requires_review: bool
    created_at: datetime


class AdminFulfillmentAttemptResponse(BaseModel):
    id: uuid.UUID
    attempt_number: int
    status: FulfillmentStatus
    provider_id: uuid.UUID | None
    external_order_id: str | None
    error_classification: str | None
    started_at: datetime
    completed_at: datetime | None


class AdminFulfillmentJobResponse(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_number: str
    order_status: OrderStatus
    recipient: str
    attempt_number: int
    status: FulfillmentJobStatus
    failure_classification: str | None
    last_error: str | None
    manual_requeue_count: int
    last_requeued_at: datetime | None
    created_at: datetime
    updated_at: datetime
    can_requeue: bool
    requeue_reason_code: str
    requeue_reason: str


class AdminFulfillmentJobListResponse(BaseModel):
    jobs: list[AdminFulfillmentJobResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


class AdminFulfillmentJobDetailResponse(AdminFulfillmentJobResponse):
    attempts: list[AdminFulfillmentAttemptResponse]


class AdminFulfillmentRequeueResponse(BaseModel):
    job_id: uuid.UUID
    status: FulfillmentJobStatus
    attempt_number: int
    manual_requeue_count: int


class AdminFulfillmentReconciliationResult(BaseModel):
    order_id: uuid.UUID
    order_number: str
    issue_type: str
    details: str
    action_taken: str
    metadata: dict[str, Any]


class AdminFulfillmentReconciliationResponse(BaseModel):
    results: list[AdminFulfillmentReconciliationResult]


async def _audit(
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | str,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            details=details or {},
        )
    )


async def _tenant_category(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    category_id: uuid.UUID | None,
) -> Category | None:
    if category_id is None:
        return None
    category = await session.get(Category, category_id)
    if category is None or category.tenant_id != tenant_id or category.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found.")
    return category


def _product_response(product: Product) -> ProductResponse:
    return ProductResponse(
        sort_order=product.sort_order,
        id=product.id,
        title=product.title,
        description=product.description,
        category_id=product.category_id,
        category_name=product.category.name if product.category else None,
        is_active=product.is_active,
        metadata=product.metadata_json or {},
        variants=[VariantResponse.model_validate(v) for v in product.variants if v.deleted_at is None],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


@router.get("/bootstrap", response_model=AdminBootstrapResponse)
async def admin_bootstrap(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> AdminBootstrapResponse:
    tenant = await session.get(Tenant, principal.tenant_id)
    user = await session.get(User, principal.user_id)
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.tenant_id == principal.tenant_id,
                Membership.user_id == principal.user_id,
                Membership.is_active.is_(True),
            )
        )
    ).scalar_one()
    if tenant is None or user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin context not found.")

    attention_statuses = [OrderStatus.FAILED, OrderStatus.PARTIALLY_FULFILLED]
    product_count = await session.scalar(
        select(func.count()).select_from(Product).where(
            Product.tenant_id == principal.tenant_id,
            Product.deleted_at.is_(None),
        )
    )
    active_product_count = await session.scalar(
        select(func.count()).select_from(Product).where(
            Product.tenant_id == principal.tenant_id,
            Product.deleted_at.is_(None),
            Product.is_active.is_(True),
        )
    )
    order_count = await session.scalar(
        select(func.count()).select_from(Order).where(Order.tenant_id == principal.tenant_id)
    )
    attention_order_count = await session.scalar(
        select(func.count()).select_from(Order).where(
            Order.tenant_id == principal.tenant_id,
            Order.status.in_(attention_statuses),
        )
    )
    dead_letter_count = await session.scalar(
        select(func.count()).select_from(FulfillmentJobRecord).where(
            FulfillmentJobRecord.tenant_id == principal.tenant_id,
            FulfillmentJobRecord.status == FulfillmentJobStatus.DEAD_LETTER,
        )
    )
    review_count = await session.scalar(
        select(func.count()).select_from(PaymentReconciliationEvent).where(
            PaymentReconciliationEvent.tenant_id == principal.tenant_id,
            PaymentReconciliationEvent.requires_review.is_(True),
        )
    )
    financial_case_count = await session.scalar(
        select(func.count()).select_from(FinancialResolutionCase).where(
            FinancialResolutionCase.tenant_id == principal.tenant_id,
            FinancialResolutionCase.status != FinancialResolutionCaseStatus.RESOLVED,
        )
    )

    return AdminBootstrapResponse(
        actor=AdminActor(
            id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            role=membership.role,
        ),
        store=AdminStore(id=tenant.id, name=tenant.name, slug=tenant.slug),
        counts=AdminCounts(
            products=product_count or 0,
            active_products=active_product_count or 0,
            orders=order_count or 0,
            attention_orders=attention_order_count or 0,
            dead_letter_jobs=dead_letter_count or 0,
            reconciliation_reviews=review_count or 0,
            financial_open_cases=financial_case_count or 0,
        ),
    )


@router.get("/catalog/categories", response_model=list[CategoryResponse])
async def list_categories(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[CategoryResponse]:
    rows = list(
        (
            await session.execute(
                select(Category)
                .where(
                    Category.tenant_id == principal.tenant_id,
                    Category.deleted_at.is_(None),
                )
                .order_by(Category.sort_order.asc(), Category.name.asc(), Category.id.asc())
            )
        ).scalars().all()
    )
    return [CategoryResponse.model_validate(row) for row in rows]


@router.post("/catalog/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    req: CategoryCreateRequest,
    principal: AuthenticatedPrincipal = Depends(require_manager_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> CategoryResponse:
    await _tenant_category(session, principal.tenant_id, req.parent_id)
    category = Category(
        tenant_id=principal.tenant_id,
        name=req.name.strip(),
        slug=req.slug.strip().lower(),
        parent_id=req.parent_id,
        sort_order=req.sort_order,
        is_active=req.is_active,
    )
    session.add(category)
    await session.flush()
    await _audit(
        session,
        principal,
        action="CATEGORY_CREATED",
        resource_type="category",
        resource_id=category.id,
        details={"name": category.name, "slug": category.slug},
    )
    await session.commit()
    await session.refresh(category)
    return CategoryResponse.model_validate(category)


@router.patch("/catalog/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: uuid.UUID,
    req: CategoryUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(require_manager_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> CategoryResponse:
    category = await _tenant_category(session, principal.tenant_id, category_id)
    assert category is not None
    changes = req.model_dump(exclude_unset=True)
    if "parent_id" in changes:
        if changes["parent_id"] == category.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Category cannot be its own parent.")
        await _tenant_category(session, principal.tenant_id, changes["parent_id"])
    for field, value in changes.items():
        if field in {"name", "slug"} and isinstance(value, str):
            value = value.strip().lower() if field == "slug" else value.strip()
        setattr(category, field, value)
    await _audit(
        session,
        principal,
        action="CATEGORY_UPDATED",
        resource_type="category",
        resource_id=category.id,
        details={"fields": sorted(changes)},
    )
    await session.commit()
    await session.refresh(category)
    return CategoryResponse.model_validate(category)


@router.get("/catalog/products", response_model=ProductListResponse)
async def list_products(
    q: str | None = Query(None, max_length=120),
    active: bool | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> ProductListResponse:
    filters = [Product.tenant_id == principal.tenant_id, Product.deleted_at.is_(None)]
    if active is not None:
        filters.append(Product.is_active.is_(active))
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        matching_variants = select(ProductVariant.product_id).where(
            ProductVariant.deleted_at.is_(None),
            or_(
                func.lower(ProductVariant.title).like(needle),
                func.lower(ProductVariant.sku).like(needle),
            ),
        )
        filters.append(
            or_(
                func.lower(Product.title).like(needle),
                func.lower(func.coalesce(Product.description, "")).like(needle),
                Product.id.in_(matching_variants),
            )
        )
    total = await session.scalar(select(func.count()).select_from(Product).where(*filters)) or 0
    stmt = (
        select(Product)
        .where(*filters)
        .options(selectinload(Product.variants), selectinload(Product.category))
        .order_by(Product.sort_order.asc(), Product.created_at.desc(), Product.id.desc())
        .offset(offset)
        .limit(limit)
    )
    products = list((await session.execute(stmt)).scalars().unique().all())
    next_offset = offset + len(products) if offset + len(products) < total else None
    return ProductListResponse(
        products=[_product_response(product) for product in products],
        total=total,
        offset=offset,
        limit=limit,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.post("/catalog/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    req: ProductCreateRequest,
    principal: AuthenticatedPrincipal = Depends(require_manager_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> ProductResponse:
    await _tenant_category(session, principal.tenant_id, req.category_id)
    product = Product(
        tenant_id=principal.tenant_id,
        category_id=req.category_id,
        sort_order=req.sort_order,
        title=req.title.strip(),
        description=req.description,
        is_active=req.is_active,
        metadata_json=req.metadata,
    )
    session.add(product)
    await session.flush()
    created_variants: list[ProductVariant] = []
    for variant_req in req.variants:
        variant = ProductVariant(
            product_id=product.id,
            sku=variant_req.sku.strip(),
            title=variant_req.title.strip(),
            price=variant_req.price,
            currency=variant_req.currency.upper(),
            stock_quantity=variant_req.stock_quantity,
            is_active=variant_req.is_active,
            attributes=variant_req.attributes,
        )
        session.add(variant)
        created_variants.append(variant)
    await session.flush()
    await _audit(
        session,
        principal,
        action="PRODUCT_CREATED",
        resource_type="product",
        resource_id=product.id,
        details={"title": product.title, "variant_count": len(created_variants)},
    )
    await session.commit()
    stmt = (
        select(Product)
        .where(Product.id == product.id, Product.tenant_id == principal.tenant_id)
        .options(selectinload(Product.variants), selectinload(Product.category))
    )
    loaded = (await session.execute(stmt)).scalar_one()
    return _product_response(loaded)


@router.patch("/catalog/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: uuid.UUID,
    req: ProductUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(require_manager_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> ProductResponse:
    stmt = (
        select(Product)
        .where(
            Product.id == product_id,
            Product.tenant_id == principal.tenant_id,
            Product.deleted_at.is_(None),
        )
        .options(selectinload(Product.variants), selectinload(Product.category))
    )
    product = (await session.execute(stmt)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")
    changes = req.model_dump(exclude_unset=True)
    if "category_id" in changes:
        await _tenant_category(session, principal.tenant_id, changes["category_id"])
    for field, value in changes.items():
        if field == "metadata":
            product.metadata_json = value or {}
        else:
            if field == "title" and isinstance(value, str):
                value = value.strip()
            setattr(product, field, value)
    await _audit(
        session,
        principal,
        action="PRODUCT_UPDATED",
        resource_type="product",
        resource_id=product.id,
        details={"fields": sorted(changes)},
    )
    await session.commit()
    product = (await session.execute(stmt)).scalar_one()
    return _product_response(product)


@router.post(
    "/catalog/products/{product_id}/variants",
    response_model=VariantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_variant(
    product_id: uuid.UUID,
    req: VariantCreateRequest,
    principal: AuthenticatedPrincipal = Depends(require_manager_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> VariantResponse:
    product = (
        await session.execute(
            select(Product).where(
                Product.id == product_id,
                Product.tenant_id == principal.tenant_id,
                Product.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")
    variant = ProductVariant(
        product_id=product.id,
        sku=req.sku.strip(),
        title=req.title.strip(),
        price=req.price,
        currency=req.currency.upper(),
        stock_quantity=req.stock_quantity,
        is_active=req.is_active,
        attributes=req.attributes,
    )
    session.add(variant)
    await session.flush()
    await _audit(
        session,
        principal,
        action="PRODUCT_VARIANT_CREATED",
        resource_type="product_variant",
        resource_id=variant.id,
        details={"product_id": str(product.id), "sku": variant.sku},
    )
    await session.commit()
    await session.refresh(variant)
    return VariantResponse.model_validate(variant)


@router.patch("/catalog/variants/{variant_id}", response_model=VariantResponse)
async def update_variant(
    variant_id: uuid.UUID,
    req: VariantUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(require_manager_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> VariantResponse:
    stmt = (
        select(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(
            ProductVariant.id == variant_id,
            ProductVariant.deleted_at.is_(None),
            Product.tenant_id == principal.tenant_id,
            Product.deleted_at.is_(None),
        )
    )
    variant = (await session.execute(stmt)).scalar_one_or_none()
    if variant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found.")
    changes = req.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field == "currency" and value is not None:
            value = value.upper()
        if field in {"sku", "title"} and isinstance(value, str):
            value = value.strip()
        setattr(variant, field, value)
    await _audit(
        session,
        principal,
        action="PRODUCT_VARIANT_UPDATED",
        resource_type="product_variant",
        resource_id=variant.id,
        details={"fields": sorted(changes)},
    )
    await session.commit()
    await session.refresh(variant)
    return VariantResponse.model_validate(variant)


@router.get("/orders", response_model=AdminOrderListResponse)
async def list_orders(
    q: str | None = Query(None, max_length=120),
    order_status: OrderStatus | None = Query(None, alias="status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> AdminOrderListResponse:
    filters = [Order.tenant_id == principal.tenant_id]
    if order_status is not None:
        filters.append(Order.status == order_status)
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        filters.append(func.lower(Order.order_number).like(needle))
    total = await session.scalar(select(func.count()).select_from(Order).where(*filters)) or 0
    stmt = (
        select(Order)
        .where(*filters)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc(), Order.id.desc())
        .offset(offset)
        .limit(limit)
    )
    orders = list((await session.execute(stmt)).scalars().unique().all())
    user_ids = {order.user_id for order in orders}
    users = {}
    if user_ids:
        users = {
            user.id: user
            for user in (
                await session.execute(select(User).where(User.id.in_(user_ids)))
            ).scalars().all()
        }
    order_ids = [order.id for order in orders]
    jobs_by_order: dict[uuid.UUID, FulfillmentJobRecord] = {}
    if order_ids:
        jobs = list(
            (
                await session.execute(
                    select(FulfillmentJobRecord)
                    .where(
                        FulfillmentJobRecord.tenant_id == principal.tenant_id,
                        FulfillmentJobRecord.order_id.in_(order_ids),
                    )
                    .order_by(FulfillmentJobRecord.created_at.desc())
                )
            ).scalars().all()
        )
        for job in jobs:
            jobs_by_order.setdefault(job.order_id, job)

    payload: list[AdminOrderResponse] = []
    for order in orders:
        user = users.get(order.user_id)
        name = "Unknown customer"
        username = None
        if user is not None:
            name = " ".join(filter(None, [user.first_name, user.last_name])).strip() or user.username or str(user.id)
            username = user.username
        job = jobs_by_order.get(order.id)
        payload.append(
            AdminOrderResponse(
                id=order.id,
                order_number=order.order_number,
                user_id=order.user_id,
                customer_name=name,
                customer_username=username,
                status=order.status,
                total_amount=order.total_amount,
                currency=order.currency,
                created_at=order.created_at,
                items=[
                    AdminOrderItemResponse(
                        id=item.id,
                        variant_id=item.product_variant_id,
                        quantity=item.quantity,
                        unit_price=item.unit_price,
                        total_price=item.total_price,
                    )
                    for item in order.items
                ],
                fulfillment_status=job.status if job else None,
            )
        )
    next_offset = offset + len(payload) if offset + len(payload) < total else None
    return AdminOrderListResponse(
        orders=payload,
        total=total,
        offset=offset,
        limit=limit,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.get("/reconciliation-events", response_model=list[AdminReconciliationEventResponse])
async def admin_reconciliation_events(
    requires_review: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[AdminReconciliationEventResponse]:
    stmt = select(PaymentReconciliationEvent).where(
        PaymentReconciliationEvent.tenant_id == principal.tenant_id
    )
    if requires_review is not None:
        stmt = stmt.where(PaymentReconciliationEvent.requires_review.is_(requires_review))
    events = list(
        (
            await session.execute(
                stmt.order_by(PaymentReconciliationEvent.created_at.desc()).limit(limit)
            )
        ).scalars().all()
    )
    return [
        AdminReconciliationEventResponse(
            id=event.id,
            provider=event.provider,
            event_type=event.event_type,
            provider_event_id=event.provider_event_id,
            payment_intent_id=event.payment_intent_id,
            status=event.status.value,
            amount=event.amount,
            currency=event.currency,
            classification=event.classification,
            requires_review=event.requires_review,
            created_at=event.created_at,
        )
        for event in events
    ]


async def _admin_fulfillment_job_response(
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    job: FulfillmentJobRecord,
    order: Order,
) -> AdminFulfillmentJobResponse:
    if job.status == FulfillmentJobStatus.DEAD_LETTER:
        decision = await evaluate_manual_requeue(
            session,
            tenant_id=principal.tenant_id,
            job=job,
        )
    else:
        decision = RequeueDecision(
            False,
            "JOB_NOT_DEAD_LETTER",
            "Only dead-letter jobs can be manually requeued.",
        )
    return AdminFulfillmentJobResponse(
        id=job.id,
        order_id=job.order_id,
        order_number=order.order_number,
        order_status=order.status,
        recipient=job.recipient,
        attempt_number=job.attempt_number,
        status=job.status,
        failure_classification=job.failure_classification,
        last_error=job.last_error,
        manual_requeue_count=job.manual_requeue_count,
        last_requeued_at=job.last_requeued_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        can_requeue=decision.allowed,
        requeue_reason_code=decision.reason_code,
        requeue_reason=decision.reason,
    )


@router.get("/fulfillment/jobs", response_model=AdminFulfillmentJobListResponse)
async def list_fulfillment_jobs(
    job_status: FulfillmentJobStatus | None = Query(None, alias="status"),
    q: str | None = Query(None, max_length=120),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> AdminFulfillmentJobListResponse:
    filters = [FulfillmentJobRecord.tenant_id == principal.tenant_id]
    if job_status is not None:
        filters.append(FulfillmentJobRecord.status == job_status)
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        filters.append(func.lower(Order.order_number).like(needle))

    total = (
        await session.scalar(
            select(func.count())
            .select_from(FulfillmentJobRecord)
            .join(Order, Order.id == FulfillmentJobRecord.order_id)
            .where(*filters)
        )
        or 0
    )
    rows = list(
        (
            await session.execute(
                select(FulfillmentJobRecord, Order)
                .join(Order, Order.id == FulfillmentJobRecord.order_id)
                .where(*filters)
                .order_by(FulfillmentJobRecord.created_at.desc(), FulfillmentJobRecord.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    jobs = [await _admin_fulfillment_job_response(session, principal, job, order) for job, order in rows]
    next_offset = offset + len(jobs) if offset + len(jobs) < total else None
    return AdminFulfillmentJobListResponse(
        jobs=jobs,
        total=total,
        offset=offset,
        limit=limit,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.get("/fulfillment/jobs/{job_id}", response_model=AdminFulfillmentJobDetailResponse)
async def fulfillment_job_detail(
    job_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> AdminFulfillmentJobDetailResponse:
    row = (
        await session.execute(
            select(FulfillmentJobRecord, Order)
            .join(Order, Order.id == FulfillmentJobRecord.order_id)
            .where(
                FulfillmentJobRecord.id == job_id,
                FulfillmentJobRecord.tenant_id == principal.tenant_id,
                Order.tenant_id == principal.tenant_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fulfillment job not found.")
    job, order = row
    base = await _admin_fulfillment_job_response(session, principal, job, order)
    attempts = list(
        (
            await session.execute(
                select(FulfillmentAttempt)
                .where(
                    FulfillmentAttempt.tenant_id == principal.tenant_id,
                    FulfillmentAttempt.order_id == order.id,
                )
                .order_by(FulfillmentAttempt.attempt_number.desc(), FulfillmentAttempt.created_at.desc())
            )
        ).scalars().all()
    )
    return AdminFulfillmentJobDetailResponse(
        **base.model_dump(),
        attempts=[
            AdminFulfillmentAttemptResponse(
                id=attempt.id,
                attempt_number=attempt.attempt_number,
                status=attempt.status,
                provider_id=attempt.provider_id,
                external_order_id=attempt.external_order_id,
                error_classification=attempt.error_classification,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
            )
            for attempt in attempts
        ],
    )


@router.post(
    "/fulfillment/jobs/{job_id}/requeue",
    response_model=AdminFulfillmentRequeueResponse,
)
async def requeue_fulfillment_job(
    job_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> AdminFulfillmentRequeueResponse:
    try:
        job, decision = await requeue_dead_letter_job(
            session,
            tenant_id=principal.tenant_id,
            job_id=job_id,
            actor_user_id=principal.user_id,
        )
    except FulfillmentOperationError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.code == "JOB_NOT_FOUND" else status.HTTP_409_CONFLICT
        raise HTTPException(
            status_code=code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    await _audit(
        session,
        principal,
        action="FULFILLMENT_JOB_REQUEUED",
        resource_type="fulfillment_job",
        resource_id=job.id,
        details={
            "order_id": str(job.order_id),
            "attempt_number": job.attempt_number,
            "safety_decision": decision.reason_code,
        },
    )
    await session.commit()
    await session.refresh(job)
    return AdminFulfillmentRequeueResponse(
        job_id=job.id,
        status=job.status,
        attempt_number=job.attempt_number,
        manual_requeue_count=job.manual_requeue_count,
    )


@router.post(
    "/fulfillment/orders/{order_id}/reconcile",
    response_model=AdminFulfillmentReconciliationResponse,
)
async def reconcile_fulfillment_order(
    order_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> AdminFulfillmentReconciliationResponse:
    order = (
        await session.execute(
            select(Order).where(
                Order.id == order_id,
                Order.tenant_id == principal.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found.")

    reconciliation = ReconciliationService()
    results = await reconciliation.reconcile_order(
        session,
        tenant_id=principal.tenant_id,
        order_id=order.id,
    )

    issue_types = {result.issue_type for result in results}
    latest_job = (
        await session.execute(
            select(FulfillmentJobRecord)
            .where(
                FulfillmentJobRecord.tenant_id == principal.tenant_id,
                FulfillmentJobRecord.order_id == order.id,
            )
            .order_by(FulfillmentJobRecord.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_job is not None:
        if "OUT_OF_SYNC_RESOLVED" in issue_types:
            latest_job.status = FulfillmentJobStatus.COMPLETED
            latest_job.failure_classification = "RECONCILED_SUCCESS"
            latest_job.last_error = None
            latest_job.completed_at = datetime.now(UTC)
        elif "UPSTREAM_FAILED_RESOLVED" in issue_types:
            latest_job.status = FulfillmentJobStatus.FAILED
            latest_job.failure_classification = "RECONCILED_UPSTREAM_FAILURE"
            latest_job.completed_at = datetime.now(UTC)

    await _audit(
        session,
        principal,
        action="FULFILLMENT_RECONCILIATION_RUN",
        resource_type="order",
        resource_id=order.id,
        details={"issue_types": sorted(issue_types), "result_count": len(results)},
    )
    await session.commit()
    return AdminFulfillmentReconciliationResponse(
        results=[
            AdminFulfillmentReconciliationResult(
                order_id=result.order_id,
                order_number=result.order_number,
                issue_type=result.issue_type,
                details=result.details,
                action_taken=result.action_taken,
                metadata=result.metadata,
            )
            for result in results
        ]
    )
