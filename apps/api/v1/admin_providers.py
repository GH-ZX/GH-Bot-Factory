import uuid
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import require_admin_or_owner, require_staff_or_above
from packages.commerce.models import Product, ProductVariant
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.payments.models import PaymentProviderConfig
from packages.payments.providers.registry import default_payment_provider_registry
from packages.providers.clients.registry import provider_registry
from packages.providers.models import (
    Provider,
    ProviderCredential,
    ProviderHealthStatus,
    ProviderProductMapping,
)
from packages.providers.router import ProviderRouter
from packages.tenants.models import AuditLog

router = APIRouter(prefix="/admin", tags=["admin-providers"])

_SECRET_REF_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{1,254}$"
_SECRETISH_FRAGMENTS = ("secret", "password", "token", "api_key", "apikey", "authorization", "credential")


def _reject_secret_material(value: Any, *, path: str = "settings") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in _SECRETISH_FRAGMENTS):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"{path}.{key} looks like secret material. Store secrets by reference instead.",
                )
            _reject_secret_material(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_secret_material(nested, path=f"{path}[{index}]")


_TELEGRAM_STARS_SETTING_KEYS = {
    "display_name",
    "topup_enabled",
    "topup_min_amount",
    "topup_max_amount",
    "topup_currencies",
    "topup_whole_units_only",
    "checkout_mode",
    "terms_required",
    "terms_url",
    "terms_version",
    "invoice_title",
    "invoice_description",
    "price_label",
    "transaction_scan_pages",
    "chargeback_reconciliation_enabled",
}


def _payment_config_error(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _normalize_payment_provider_settings(provider_name: str, settings: dict[str, Any]) -> dict[str, Any]:
    """Validate operator-controlled settings without allowing them to become a secret exfiltration path."""
    _reject_secret_material(settings, path="settings")
    normalized = dict(settings)

    if provider_name == "telegram_stars":
        unknown = sorted(set(normalized) - _TELEGRAM_STARS_SETTING_KEYS)
        if unknown:
            raise _payment_config_error(
                f"Unsupported Telegram Stars settings: {unknown}. Runtime endpoint overrides are not allowed."
            )
        normalized.setdefault("topup_enabled", True)
        normalized.setdefault("topup_min_amount", "1")
        normalized.setdefault("topup_max_amount", "1000")
        normalized.setdefault("transaction_scan_pages", 10)

    if "topup_min_amount" in normalized or "topup_max_amount" in normalized:
        try:
            min_amount = Decimal(str(normalized.get("topup_min_amount", "1")))
            max_amount = Decimal(str(normalized.get("topup_max_amount", "1000")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise _payment_config_error("Wallet top-up limits must be valid decimal amounts.") from exc
        if not min_amount.is_finite() or not max_amount.is_finite() or min_amount <= 0 or max_amount < min_amount:
            raise _payment_config_error("Wallet top-up limits must be positive and max must be >= min.")
        normalized["topup_min_amount"] = str(min_amount)
        normalized["topup_max_amount"] = str(max_amount)

    if "topup_currencies" in normalized:
        raw_currencies = normalized["topup_currencies"]
        if not isinstance(raw_currencies, list) or not raw_currencies:
            raise _payment_config_error("topup_currencies must be a non-empty list.")
        currencies: list[str] = []
        for currency in raw_currencies:
            code = str(currency).strip().upper()
            if len(code) != 3 or not code.isalpha():
                raise _payment_config_error("Each top-up currency must be a three-letter alphabetic code.")
            if code not in currencies:
                currencies.append(code)
        normalized["topup_currencies"] = currencies

    if normalized.get("terms_required") is True:
        terms_url = str(normalized.get("terms_url") or "").strip()
        parsed = urlsplit(terms_url)
        if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise _payment_config_error("terms_url must be a credential-free HTTPS URL when terms are required.")
        normalized["terms_url"] = terms_url

    if provider_name == "telegram_stars":
        currencies = normalized.get("topup_currencies", ["XTR"])
        if currencies != ["XTR"]:
            raise _payment_config_error("Telegram Stars top-ups must use XTR as the only currency.")
        normalized["topup_currencies"] = ["XTR"]

        try:
            min_amount = Decimal(str(normalized["topup_min_amount"]))
            max_amount = Decimal(str(normalized["topup_max_amount"]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise _payment_config_error("Telegram Stars top-up limits are invalid.") from exc
        if min_amount != min_amount.to_integral_value() or max_amount != max_amount.to_integral_value():
            raise _payment_config_error("Telegram Stars top-up limits must use whole XTR amounts.")
        normalized["topup_whole_units_only"] = True

        if normalized.get("checkout_mode", "telegram_invoice") != "telegram_invoice":
            raise _payment_config_error("Telegram Stars must use checkout_mode=telegram_invoice.")
        normalized["checkout_mode"] = "telegram_invoice"

        if normalized.get("terms_required", True) is not True:
            raise _payment_config_error("Telegram Stars configuration requires explicit payment terms.")
        normalized["terms_required"] = True
        terms_url = str(normalized.get("terms_url") or "").strip()
        parsed = urlsplit(terms_url)
        if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise _payment_config_error("Telegram Stars requires a credential-free HTTPS terms_url.")
        normalized["terms_url"] = terms_url

        try:
            scan_pages = int(normalized.get("transaction_scan_pages", 10))
        except (TypeError, ValueError) as exc:
            raise _payment_config_error("transaction_scan_pages must be an integer between 1 and 50.") from exc
        if scan_pages < 1 or scan_pages > 50:
            raise _payment_config_error("transaction_scan_pages must be between 1 and 50.")
        normalized["transaction_scan_pages"] = scan_pages

    return normalized


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


class SupplierCredentialStatus(BaseModel):
    credential_type: str
    configured: bool = True


class SupplierProviderResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    provider_type: str
    is_enabled: bool
    priority: int
    health_status: ProviderHealthStatus
    consecutive_failures: int
    metadata: dict[str, Any]
    credentials: list[SupplierCredentialStatus]
    mapping_count: int


class SupplierProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    provider_type: str = Field(min_length=1, max_length=50)
    is_enabled: bool = True
    priority: int = Field(default=1, ge=1, le=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SupplierProviderUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_enabled: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=1000)
    metadata: dict[str, Any] | None = None


class SupplierCredentialUpsertRequest(BaseModel):
    credential_type: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    secret_ref: str = Field(min_length=2, max_length=255, pattern=_SECRET_REF_PATTERN)


class SupplierMappingRequest(BaseModel):
    product_id: uuid.UUID
    product_variant_id: uuid.UUID | None = None
    external_product_id: str = Field(min_length=1, max_length=100)
    is_enabled: bool = True
    cost_price: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    cost_currency: str = Field(default="USD", min_length=3, max_length=3)
    priority_override: int | None = Field(default=None, ge=1, le=1000)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class SupplierMappingUpdateRequest(BaseModel):
    external_product_id: str | None = Field(default=None, min_length=1, max_length=100)
    is_enabled: bool | None = None
    cost_price: Decimal | None = Field(default=None, ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    cost_currency: str | None = Field(default=None, min_length=3, max_length=3)
    priority_override: int | None = Field(default=None, ge=1, le=1000)
    provider_metadata: dict[str, Any] | None = None


class SupplierMappingResponse(BaseModel):
    id: uuid.UUID
    provider_id: uuid.UUID
    provider_name: str
    product_id: uuid.UUID
    product_title: str
    product_variant_id: uuid.UUID | None
    variant_title: str | None
    external_product_id: str
    is_enabled: bool
    cost_price: Decimal
    cost_currency: str
    priority_override: int | None
    provider_metadata: dict[str, Any]


class SupplierHealthResponse(BaseModel):
    provider_id: uuid.UUID
    status: ProviderHealthStatus
    latency_ms: float | None
    message: str | None
    balance: Decimal | None
    balance_currency: str | None


class PaymentProviderConfigResponse(BaseModel):
    id: uuid.UUID
    provider_name: str
    is_enabled: bool
    credentials_configured: bool
    webhook_secret_configured: bool
    settings: dict[str, Any]


class PaymentProviderConfigRequest(BaseModel):
    is_enabled: bool = True
    credentials_ref: str | None = Field(default=None, min_length=2, max_length=255, pattern=_SECRET_REF_PATTERN)
    webhook_secret_ref: str | None = Field(default=None, min_length=2, max_length=255, pattern=_SECRET_REF_PATTERN)
    settings: dict[str, Any] = Field(default_factory=dict)


class ProviderCapabilitiesResponse(BaseModel):
    supplier_provider_types: list[str]
    payment_provider_names: list[str]


def _supplier_response(provider: Provider, mapping_count: int) -> SupplierProviderResponse:
    return SupplierProviderResponse(
        id=provider.id,
        name=provider.name,
        slug=provider.slug,
        provider_type=provider.provider_type,
        is_enabled=provider.is_enabled,
        priority=provider.priority,
        health_status=provider.health_status,
        consecutive_failures=provider.consecutive_failures,
        metadata=provider.metadata_json or {},
        credentials=[
            SupplierCredentialStatus(credential_type=credential.credential_type)
            for credential in sorted(provider.credentials, key=lambda row: row.credential_type)
        ],
        mapping_count=mapping_count,
    )


@router.get("/provider-capabilities", response_model=ProviderCapabilitiesResponse)
async def provider_capabilities(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
) -> ProviderCapabilitiesResponse:
    del principal
    return ProviderCapabilitiesResponse(
        supplier_provider_types=list(provider_registry.registered_types()),
        payment_provider_names=list(default_payment_provider_registry.registered_provider_names()),
    )


@router.get("/providers", response_model=list[SupplierProviderResponse])
async def list_supplier_providers(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[SupplierProviderResponse]:
    providers = list(
        (
            await session.execute(
                select(Provider)
                .where(Provider.tenant_id == principal.tenant_id)
                .options(selectinload(Provider.credentials))
                .order_by(Provider.priority.asc(), Provider.name.asc())
            )
        ).scalars().unique().all()
    )
    counts: dict[uuid.UUID, int] = {}
    if providers:
        rows = (
            await session.execute(
                select(ProviderProductMapping.provider_id, func.count())
                .where(
                    ProviderProductMapping.tenant_id == principal.tenant_id,
                    ProviderProductMapping.provider_id.in_([provider.id for provider in providers]),
                )
                .group_by(ProviderProductMapping.provider_id)
            )
        ).all()
        counts = {provider_id: count for provider_id, count in rows}
    return [_supplier_response(provider, counts.get(provider.id, 0)) for provider in providers]


@router.post("/providers", response_model=SupplierProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier_provider(
    req: SupplierProviderCreateRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> SupplierProviderResponse:
    provider_type = req.provider_type.strip().upper()
    if provider_type not in provider_registry.registered_types():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported provider type. Supported: {list(provider_registry.registered_types())}",
        )
    _reject_secret_material(req.metadata, path="metadata")
    provider = Provider(
        tenant_id=principal.tenant_id,
        name=req.name.strip(),
        slug=req.slug.strip().lower(),
        provider_type=provider_type,
        is_enabled=req.is_enabled,
        priority=req.priority,
        metadata_json=req.metadata,
        health_status=ProviderHealthStatus.UNKNOWN,
    )
    session.add(provider)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Provider slug already exists.") from exc
    await _audit(
        session,
        principal,
        action="SUPPLIER_PROVIDER_CREATED",
        resource_type="provider",
        resource_id=provider.id,
        details={"slug": provider.slug, "provider_type": provider.provider_type},
    )
    await session.commit()
    provider = (
        await session.execute(
            select(Provider).where(Provider.id == provider.id).options(selectinload(Provider.credentials))
        )
    ).scalar_one()
    return _supplier_response(provider, 0)


@router.patch("/providers/{provider_id}", response_model=SupplierProviderResponse)
async def update_supplier_provider(
    provider_id: uuid.UUID,
    req: SupplierProviderUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> SupplierProviderResponse:
    provider = (
        await session.execute(
            select(Provider)
            .where(Provider.id == provider_id, Provider.tenant_id == principal.tenant_id)
            .options(selectinload(Provider.credentials))
        )
    ).scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found.")
    changes = req.model_dump(exclude_unset=True)
    if "metadata" in changes:
        _reject_secret_material(changes["metadata"] or {}, path="metadata")
        provider.metadata_json = changes.pop("metadata") or {}
    for field, value in changes.items():
        if field == "name" and value is not None:
            value = value.strip()
        setattr(provider, field, value)
    await _audit(
        session,
        principal,
        action="SUPPLIER_PROVIDER_UPDATED",
        resource_type="provider",
        resource_id=provider.id,
        details={"fields": sorted(req.model_dump(exclude_unset=True))},
    )
    await session.commit()
    mapping_count = (
        await session.scalar(
            select(func.count()).select_from(ProviderProductMapping).where(
                ProviderProductMapping.tenant_id == principal.tenant_id,
                ProviderProductMapping.provider_id == provider.id,
            )
        )
        or 0
    )
    return _supplier_response(provider, mapping_count)


@router.put("/providers/{provider_id}/credentials", response_model=SupplierCredentialStatus)
async def upsert_supplier_credential(
    provider_id: uuid.UUID,
    req: SupplierCredentialUpsertRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> SupplierCredentialStatus:
    provider = (
        await session.execute(
            select(Provider).where(Provider.id == provider_id, Provider.tenant_id == principal.tenant_id)
        )
    ).scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found.")
    credential_type = req.credential_type.strip().upper()
    credential = (
        await session.execute(
            select(ProviderCredential).where(
                ProviderCredential.provider_id == provider.id,
                ProviderCredential.tenant_id == principal.tenant_id,
                ProviderCredential.credential_type == credential_type,
            )
        )
    ).scalar_one_or_none()
    if credential is None:
        credential = ProviderCredential(
            tenant_id=principal.tenant_id,
            provider_id=provider.id,
            credential_type=credential_type,
            secret_ref=req.secret_ref,
        )
        session.add(credential)
    else:
        credential.secret_ref = req.secret_ref
    await _audit(
        session,
        principal,
        action="SUPPLIER_CREDENTIAL_REFERENCE_UPDATED",
        resource_type="provider",
        resource_id=provider.id,
        details={"credential_type": credential_type},
    )
    await session.commit()
    return SupplierCredentialStatus(credential_type=credential_type)


async def _mapping_response(session: AsyncSession, mapping: ProviderProductMapping) -> SupplierMappingResponse:
    provider = await session.get(Provider, mapping.provider_id)
    product = await session.get(Product, mapping.product_id)
    variant = await session.get(ProductVariant, mapping.product_variant_id) if mapping.product_variant_id else None
    assert provider is not None and product is not None
    return SupplierMappingResponse(
        id=mapping.id,
        provider_id=mapping.provider_id,
        provider_name=provider.name,
        product_id=mapping.product_id,
        product_title=product.title,
        product_variant_id=mapping.product_variant_id,
        variant_title=variant.title if variant else None,
        external_product_id=mapping.external_product_id,
        is_enabled=mapping.is_enabled,
        cost_price=mapping.cost_price,
        cost_currency=mapping.cost_currency,
        priority_override=mapping.priority_override,
        provider_metadata=mapping.provider_metadata or {},
    )


@router.get("/provider-mappings", response_model=list[SupplierMappingResponse])
async def list_supplier_mappings(
    provider_id: uuid.UUID | None = Query(None),
    product_id: uuid.UUID | None = Query(None),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[SupplierMappingResponse]:
    stmt = select(ProviderProductMapping).where(ProviderProductMapping.tenant_id == principal.tenant_id)
    if provider_id:
        stmt = stmt.where(ProviderProductMapping.provider_id == provider_id)
    if product_id:
        stmt = stmt.where(ProviderProductMapping.product_id == product_id)
    mappings = list((await session.execute(stmt.order_by(ProviderProductMapping.created_at.desc()))).scalars().all())
    return [await _mapping_response(session, mapping) for mapping in mappings]


async def _validate_mapping_ownership(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider_id: uuid.UUID,
    product_id: uuid.UUID,
    variant_id: uuid.UUID | None,
) -> tuple[Provider, Product]:
    provider = (
        await session.execute(select(Provider).where(Provider.id == provider_id, Provider.tenant_id == tenant_id))
    ).scalar_one_or_none()
    product = (
        await session.execute(
            select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id, Product.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if provider is None or product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider or product not found.")
    if variant_id is not None:
        variant = await session.get(ProductVariant, variant_id)
        if variant is None or variant.product_id != product.id or variant.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product variant not found.")
    return provider, product


@router.post("/providers/{provider_id}/mappings", response_model=SupplierMappingResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier_mapping(
    provider_id: uuid.UUID,
    req: SupplierMappingRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> SupplierMappingResponse:
    await _validate_mapping_ownership(
        session,
        tenant_id=principal.tenant_id,
        provider_id=provider_id,
        product_id=req.product_id,
        variant_id=req.product_variant_id,
    )
    _reject_secret_material(req.provider_metadata, path="provider_metadata")
    mapping = ProviderProductMapping(
        tenant_id=principal.tenant_id,
        provider_id=provider_id,
        product_id=req.product_id,
        product_variant_id=req.product_variant_id,
        external_product_id=req.external_product_id.strip(),
        is_enabled=req.is_enabled,
        cost_price=req.cost_price,
        cost_currency=req.cost_currency.upper(),
        priority_override=req.priority_override,
        provider_metadata=req.provider_metadata,
    )
    session.add(mapping)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Provider mapping already exists.") from exc
    await _audit(
        session,
        principal,
        action="SUPPLIER_MAPPING_CREATED",
        resource_type="provider_product_mapping",
        resource_id=mapping.id,
        details={"provider_id": str(provider_id), "product_id": str(req.product_id)},
    )
    await session.commit()
    return await _mapping_response(session, mapping)


@router.patch("/provider-mappings/{mapping_id}", response_model=SupplierMappingResponse)
async def update_supplier_mapping(
    mapping_id: uuid.UUID,
    req: SupplierMappingUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> SupplierMappingResponse:
    mapping = (
        await session.execute(
            select(ProviderProductMapping).where(
                ProviderProductMapping.id == mapping_id,
                ProviderProductMapping.tenant_id == principal.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if mapping is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider mapping not found.")
    changes = req.model_dump(exclude_unset=True)
    if "provider_metadata" in changes:
        _reject_secret_material(changes["provider_metadata"] or {}, path="provider_metadata")
    for field, value in changes.items():
        if field == "cost_currency" and value is not None:
            value = value.upper()
        if field == "external_product_id" and isinstance(value, str):
            value = value.strip()
        setattr(mapping, field, value)
    await _audit(
        session,
        principal,
        action="SUPPLIER_MAPPING_UPDATED",
        resource_type="provider_product_mapping",
        resource_id=mapping.id,
        details={"fields": sorted(changes)},
    )
    await session.commit()
    return await _mapping_response(session, mapping)


@router.post("/providers/{provider_id}/health-check", response_model=SupplierHealthResponse)
async def supplier_provider_health_check(
    provider_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> SupplierHealthResponse:
    provider = (
        await session.execute(
            select(Provider)
            .where(Provider.id == provider_id, Provider.tenant_id == principal.tenant_id)
            .options(selectinload(Provider.credentials))
        )
    ).scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found.")
    router_service = ProviderRouter()
    try:
        config = await router_service.build_provider_config(provider)
        client = provider_registry.get_client(
            provider_type=provider.provider_type,
            provider_name=provider.name,
            config=config,
            provider_id=str(provider.id),
        )
        health = await client.health_check()
        balance = await client.get_balance()
        provider.health_status = health.status
        if health.status == ProviderHealthStatus.HEALTHY:
            provider.consecutive_failures = 0
        await _audit(
            session,
            principal,
            action="SUPPLIER_PROVIDER_HEALTH_CHECKED",
            resource_type="provider",
            resource_id=provider.id,
            details={"status": health.status.value},
        )
        await session.commit()
        return SupplierHealthResponse(
            provider_id=provider.id,
            status=health.status,
            latency_ms=health.latency_ms,
            message=health.message,
            balance=balance.balance,
            balance_currency=balance.currency,
        )
    except Exception as exc:  # noqa: BLE001
        provider.health_status = ProviderHealthStatus.UNAVAILABLE
        provider.consecutive_failures += 1
        await _audit(
            session,
            principal,
            action="SUPPLIER_PROVIDER_HEALTH_CHECK_FAILED",
            resource_type="provider",
            resource_id=provider.id,
            details={"error_classification": type(exc).__name__},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Provider health check failed. Verify adapter configuration and secret references.",
        ) from exc


@router.get("/payment-providers", response_model=list[PaymentProviderConfigResponse])
async def list_payment_providers(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[PaymentProviderConfigResponse]:
    configs = list(
        (
            await session.execute(
                select(PaymentProviderConfig)
                .where(PaymentProviderConfig.tenant_id == principal.tenant_id)
                .order_by(PaymentProviderConfig.provider_name.asc())
            )
        ).scalars().all()
    )
    return [
        PaymentProviderConfigResponse(
            id=config.id,
            provider_name=config.provider_name,
            is_enabled=config.is_enabled,
            credentials_configured=bool(config.credentials_ref),
            webhook_secret_configured=bool(config.webhook_secret_ref),
            settings=config.settings_json or {},
        )
        for config in configs
    ]


@router.put("/payment-providers/{provider_name}", response_model=PaymentProviderConfigResponse)
async def upsert_payment_provider(
    provider_name: str,
    req: PaymentProviderConfigRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> PaymentProviderConfigResponse:
    normalized = provider_name.strip().lower()
    supported = default_payment_provider_registry.registered_provider_names()
    if normalized not in supported:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported payment provider. Supported: {list(supported)}",
        )
    normalized_settings = _normalize_payment_provider_settings(normalized, req.settings)
    config = (
        await session.execute(
            select(PaymentProviderConfig).where(
                PaymentProviderConfig.tenant_id == principal.tenant_id,
                PaymentProviderConfig.provider_name == normalized,
            )
        )
    ).scalar_one_or_none()
    fields_set = req.model_fields_set
    if config is None:
        if not req.credentials_ref:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="credentials_ref is required when creating a payment provider configuration.",
            )
        config = PaymentProviderConfig(
            tenant_id=principal.tenant_id,
            provider_name=normalized,
            is_enabled=req.is_enabled,
            credentials_ref=req.credentials_ref,
            webhook_secret_ref=req.webhook_secret_ref,
            settings_json=normalized_settings,
        )
        session.add(config)
        action = "PAYMENT_PROVIDER_CREATED"
    else:
        config.is_enabled = req.is_enabled
        config.settings_json = normalized_settings
        if "credentials_ref" in fields_set and req.credentials_ref:
            config.credentials_ref = req.credentials_ref
        if "webhook_secret_ref" in fields_set:
            config.webhook_secret_ref = req.webhook_secret_ref
        action = "PAYMENT_PROVIDER_UPDATED"
    await session.flush()
    await _audit(
        session,
        principal,
        action=action,
        resource_type="payment_provider_config",
        resource_id=config.id,
        details={"provider_name": normalized, "is_enabled": config.is_enabled},
    )
    await session.commit()
    default_payment_provider_registry.invalidate_cached_instance(principal.tenant_id, normalized)
    return PaymentProviderConfigResponse(
        id=config.id,
        provider_name=config.provider_name,
        is_enabled=config.is_enabled,
        credentials_configured=bool(config.credentials_ref),
        webhook_secret_configured=bool(config.webhook_secret_ref),
        settings=config.settings_json or {},
    )
