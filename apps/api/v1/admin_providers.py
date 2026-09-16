import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, SecretStr
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
from packages.providers.aggregation import ProviderOfferAggregator
from packages.providers.clients.registry import provider_registry
from packages.providers.exceptions import ProviderConfigurationError, ProviderError
from packages.providers.http_generic import inspect_openapi_document
from packages.providers.models import (
    Provider,
    ProviderCategory,
    ProviderCredential,
    ProviderHealthStatus,
    ProviderOfferSnapshot,
    ProviderProductMapping,
    ProviderRoutingPolicy,
    ProviderRoutingStrategy,
)
from packages.providers.operations import ProviderOperationsService
from packages.providers.service import (
    delete_provider_credential,
    test_provider_connection,
    upsert_provider_credential_value,
)
from packages.telegram.secrets import get_default_secret_storage
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

_NOWPAYMENTS_SETTING_KEYS = {
    "display_name",
    "topup_enabled",
    "topup_min_amount",
    "topup_max_amount",
    "topup_currencies",
    "checkout_mode",
    "terms_required",
    "terms_url",
    "terms_version",
    "pay_currency",
    "pay_currencies",
    "default_pay_currency",
    "ipn_callback_url",
    "timeout_seconds",
}


_TRIPLEA_SETTING_KEYS = {
    "display_name",
    "topup_enabled",
    "topup_min_amount",
    "topup_max_amount",
    "topup_currencies",
    "checkout_mode",
    "terms_required",
    "terms_url",
    "terms_version",
    "sandbox",
    "timeout_seconds",
}


_BINANCE_PAY_SETTING_KEYS = {
    "display_name",
    "topup_enabled",
    "topup_min_amount",
    "topup_max_amount",
    "topup_currencies",
    "checkout_mode",
    "terms_required",
    "terms_url",
    "terms_version",
    "timeout_seconds",
    "terminal_type",
    "goods_type",
    "goods_category",
    "goods_name",
    "goods_detail",
    "description",
    "order_expire_seconds",
    "support_pay_currencies",
}


_GOZAPAY_SETTING_KEYS = {
    "display_name",
    "topup_enabled",
    "topup_min_amount",
    "topup_max_amount",
    "topup_currencies",
    "checkout_mode",
    "terms_required",
    "terms_url",
    "terms_version",
    "chain",
    "coin",
    "callback_url",
    "redirect_url",
    "timeout_seconds",
    "webhook_tolerance_seconds",
    "nominal_stablecoin_parity_acknowledged",
    "experimental_risk_acknowledged",
}


_BYBIT_PAY_SETTING_KEYS = {
    "display_name",
    "topup_enabled",
    "topup_min_amount",
    "topup_max_amount",
    "topup_currencies",
    "checkout_mode",
    "terms_required",
    "terms_url",
    "terms_version",
    "sandbox",
    "timeout_seconds",
    "recv_window_ms",
    "webhook_tolerance_seconds",
    "success_url",
    "failed_url",
    "webhook_url",
    "shopping_name",
    "goods_name",
    "goods_detail",
    "mcc_code",
    "merchant_name",
    "client_id",
    "terminal_type",
    "currency_types",
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

    if provider_name == "nowpayments":
        unknown = sorted(set(normalized) - _NOWPAYMENTS_SETTING_KEYS)
        if unknown:
            raise _payment_config_error(
                f"Unsupported NOWPayments settings: {unknown}. Runtime endpoint overrides are not allowed."
            )
        raw_pay_currencies = normalized.get("pay_currencies")
        if raw_pay_currencies is None:
            raw_pay_currencies = [normalized.get("pay_currency")]
        if not isinstance(raw_pay_currencies, list):
            raise _payment_config_error("NOWPayments pay_currencies must be a list.")
        pay_currencies: list[str] = []
        for raw_currency in raw_pay_currencies:
            pay_currency = str(raw_currency or "").strip().lower()
            if not pay_currency:
                continue
            if len(pay_currency) > 32 or not all(
                char.isalnum() or char in {"_", "-"} for char in pay_currency
            ):
                raise _payment_config_error("NOWPayments pay currency code is invalid.")
            if pay_currency not in pay_currencies:
                pay_currencies.append(pay_currency)
        if not pay_currencies:
            raise _payment_config_error(
                "NOWPayments requires at least one explicit pay currency/network code."
            )
        default_pay_currency = str(
            normalized.get("default_pay_currency")
            or normalized.get("pay_currency")
            or pay_currencies[0]
        ).strip().lower()
        if default_pay_currency not in pay_currencies:
            raise _payment_config_error(
                "NOWPayments default_pay_currency must be included in pay_currencies."
            )
        normalized["pay_currencies"] = pay_currencies
        normalized["default_pay_currency"] = default_pay_currency
        normalized.pop("pay_currency", None)
        callback = str(normalized.get("ipn_callback_url") or "").strip()
        if callback:
            parsed_callback = urlsplit(callback)
            if parsed_callback.scheme.lower() != "https" or not parsed_callback.netloc:
                raise _payment_config_error("NOWPayments ipn_callback_url must be an absolute HTTPS URL.")
            if parsed_callback.username or parsed_callback.password:
                raise _payment_config_error("NOWPayments ipn_callback_url must not contain URL credentials.")
            normalized["ipn_callback_url"] = callback
        else:
            normalized.pop("ipn_callback_url", None)
        timeout_raw = normalized.get("timeout_seconds", 15)
        try:
            timeout_seconds = float(timeout_raw)
        except (TypeError, ValueError) as exc:
            raise _payment_config_error("NOWPayments timeout_seconds must be numeric.") from exc
        if not 2 <= timeout_seconds <= 30:
            raise _payment_config_error("NOWPayments timeout_seconds must be between 2 and 30 seconds.")
        normalized["timeout_seconds"] = timeout_seconds
        normalized.setdefault("topup_enabled", True)
        normalized.setdefault("topup_min_amount", "1")
        normalized.setdefault("topup_max_amount", "1000")
        normalized.setdefault("topup_currencies", ["USD"])
        normalized.setdefault("checkout_mode", "direct_crypto")

    if provider_name == "triplea":
        unknown = sorted(set(normalized) - _TRIPLEA_SETTING_KEYS)
        if unknown:
            raise _payment_config_error(
                f"Unsupported Triple-A settings: {unknown}. Runtime endpoint overrides are not allowed."
            )
        timeout_raw = normalized.get("timeout_seconds", 15)
        try:
            timeout_seconds = float(timeout_raw)
        except (TypeError, ValueError) as exc:
            raise _payment_config_error("Triple-A timeout_seconds must be numeric.") from exc
        if not 2 <= timeout_seconds <= 30:
            raise _payment_config_error("Triple-A timeout_seconds must be between 2 and 30 seconds.")
        normalized["timeout_seconds"] = timeout_seconds
        normalized["sandbox"] = bool(normalized.get("sandbox", False))
        normalized.setdefault("topup_enabled", True)
        normalized.setdefault("topup_min_amount", "1")
        normalized.setdefault("topup_max_amount", "1000")
        normalized.setdefault("topup_currencies", ["USD"])
        normalized.setdefault("checkout_mode", "hosted_redirect")

    if provider_name == "binance_pay":
        unknown = sorted(set(normalized) - _BINANCE_PAY_SETTING_KEYS)
        if unknown:
            raise _payment_config_error(
                f"Unsupported Binance Pay settings: {unknown}. Runtime endpoint overrides are not allowed."
            )
        normalized.setdefault("topup_enabled", True)
        normalized.setdefault("topup_min_amount", "1")
        normalized.setdefault("topup_max_amount", "1000")
        normalized.setdefault("topup_currencies", ["USD"])
        normalized["checkout_mode"] = "hosted_redirect"

        try:
            timeout_seconds = float(normalized.get("timeout_seconds", 15))
            order_expire_seconds = int(normalized.get("order_expire_seconds", 3600))
        except (TypeError, ValueError) as exc:
            raise _payment_config_error("Binance Pay timeout/order expiry settings are invalid.") from exc
        if not 2 <= timeout_seconds <= 30:
            raise _payment_config_error("Binance Pay timeout_seconds must be between 2 and 30 seconds.")
        if not 60 <= order_expire_seconds <= 15 * 24 * 60 * 60:
            raise _payment_config_error(
                "Binance Pay order_expire_seconds must be between 60 seconds and 15 days."
            )
        normalized["timeout_seconds"] = timeout_seconds
        normalized["order_expire_seconds"] = order_expire_seconds

        terminal_type = str(normalized.get("terminal_type") or "WEB").strip().upper()
        if terminal_type not in {"APP", "WEB", "WAP", "MINI_PROGRAM", "OTHERS"}:
            raise _payment_config_error("Binance Pay terminal_type is invalid.")
        normalized["terminal_type"] = terminal_type
        goods_type = str(normalized.get("goods_type") or "02").strip()
        if goods_type not in {"01", "02"}:
            raise _payment_config_error("Binance Pay goods_type must be 01 or 02.")
        normalized["goods_type"] = goods_type
        goods_category = str(normalized.get("goods_category") or "6000").strip().upper()
        if goods_category not in {
            "0000", "1000", "2000", "3000", "4000", "5000", "6000", "7000",
            "8000", "9000", "A000", "B000", "C000", "D000", "E000", "F000", "Z000",
        }:
            raise _payment_config_error("Binance Pay goods_category is invalid.")
        normalized["goods_category"] = goods_category

        defaults = {
            "goods_name": "Wallet top up",
            "goods_detail": "Digital wallet balance top up",
            "description": "Digital wallet balance top up",
        }
        for key, default in defaults.items():
            value = str(normalized.get(key, default) or "").strip()
            if len(value) > 256 or any(ord(char) < 32 for char in value):
                raise _payment_config_error(f"Binance Pay {key} is invalid or exceeds 256 characters.")
            if any(char in value for char in ('\\', '"')) or any(ord(char) > 0xFFFF for char in value):
                raise _payment_config_error(f"Binance Pay {key} contains unsupported characters.")
            if key != "goods_detail" and not value:
                raise _payment_config_error(f"Binance Pay {key} is required.")
            normalized[key] = value

        raw_support = normalized.get("support_pay_currencies", [])
        if not isinstance(raw_support, list):
            raise _payment_config_error("Binance Pay support_pay_currencies must be a list.")
        support: list[str] = []
        for raw_code in raw_support:
            code = str(raw_code).strip().upper()
            if not code or len(code) > 16 or not code.isalnum():
                raise _payment_config_error("Binance Pay support_pay_currencies contains an invalid code.")
            if code not in support:
                support.append(code)
        if len(",".join(support)) > 1024:
            raise _payment_config_error("Binance Pay support_pay_currencies exceeds 1024 characters.")
        normalized["support_pay_currencies"] = support

    if provider_name == "gozapay":
        unknown = sorted(set(normalized) - _GOZAPAY_SETTING_KEYS)
        if unknown:
            raise _payment_config_error(
                f"Unsupported GoZaPay settings: {unknown}. Runtime endpoint overrides are not allowed."
            )
        chain = str(normalized.get("chain") or "tron").strip().lower()
        if chain not in {"ethereum", "bsc", "polygon", "tron"}:
            raise _payment_config_error("GoZaPay chain must be ethereum, bsc, polygon, or tron.")
        coin = str(normalized.get("coin") or "USDT").strip().upper()
        if coin not in {"USDT", "USDC"}:
            raise _payment_config_error("GoZaPay coin must be USDT or USDC.")
        normalized["chain"] = chain
        normalized["coin"] = coin
        try:
            timeout_seconds = float(normalized.get("timeout_seconds", 15))
            tolerance = int(normalized.get("webhook_tolerance_seconds", 300))
        except (TypeError, ValueError) as exc:
            raise _payment_config_error("GoZaPay timeout/webhook tolerance settings are invalid.") from exc
        if not 2 <= timeout_seconds <= 30:
            raise _payment_config_error("GoZaPay timeout_seconds must be between 2 and 30 seconds.")
        if not 30 <= tolerance <= 3600:
            raise _payment_config_error(
                "GoZaPay webhook_tolerance_seconds must be between 30 and 3600 seconds."
            )
        normalized["timeout_seconds"] = timeout_seconds
        normalized["webhook_tolerance_seconds"] = tolerance
        for field in ("callback_url", "redirect_url"):
            raw_url = str(normalized.get(field) or "").strip()
            if not raw_url:
                normalized.pop(field, None)
                continue
            parsed = urlsplit(raw_url)
            if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise _payment_config_error(f"GoZaPay {field} must be an absolute HTTPS URL without credentials.")
            normalized[field] = raw_url
        normalized.setdefault("topup_enabled", True)
        normalized.setdefault("topup_min_amount", "1")
        normalized.setdefault("topup_max_amount", "1000")
        normalized["topup_currencies"] = ["USD"]
        normalized["checkout_mode"] = "hosted_redirect"
        normalized["nominal_stablecoin_parity_acknowledged"] = bool(
            normalized.get("nominal_stablecoin_parity_acknowledged", False)
        )
        normalized["experimental_risk_acknowledged"] = bool(
            normalized.get("experimental_risk_acknowledged", False)
        )

    if provider_name == "bybit_pay":
        unknown = sorted(set(normalized) - _BYBIT_PAY_SETTING_KEYS)
        if unknown:
            raise _payment_config_error(
                f"Unsupported Bybit Pay settings: {unknown}. Runtime endpoint overrides are not allowed."
            )
        normalized.setdefault("topup_enabled", True)
        normalized.setdefault("topup_min_amount", "1")
        normalized.setdefault("topup_max_amount", "1000")
        normalized.setdefault("topup_currencies", ["USD"])
        normalized["checkout_mode"] = "qr_or_redirect"
        normalized["sandbox"] = bool(normalized.get("sandbox", False))

        def require_https(name: str) -> str:
            value = str(normalized.get(name) or "").strip()
            parsed = urlsplit(value)
            if (
                not value
                or len(value) > 256
                or parsed.scheme.lower() != "https"
                or not parsed.netloc
                or parsed.username
                or parsed.password
            ):
                raise _payment_config_error(
                    f"Bybit Pay {name} must be a credential-free absolute HTTPS URL."
                )
            return value

        normalized["success_url"] = require_https("success_url")
        normalized["failed_url"] = require_https("failed_url")
        normalized["webhook_url"] = require_https("webhook_url")

        timeout_raw = normalized.get("timeout_seconds", 15)
        recv_raw = normalized.get("recv_window_ms", 5000)
        tolerance_raw = normalized.get("webhook_tolerance_seconds", 300)
        try:
            timeout_seconds = float(timeout_raw)
            recv_window = int(recv_raw)
            webhook_tolerance = int(tolerance_raw)
        except (TypeError, ValueError) as exc:
            raise _payment_config_error("Bybit Pay timeout/recv-window/webhook-tolerance settings are invalid.") from exc
        if not 2 <= timeout_seconds <= 30:
            raise _payment_config_error("Bybit Pay timeout_seconds must be between 2 and 30 seconds.")
        if not 1000 <= recv_window <= 10000:
            raise _payment_config_error("Bybit Pay recv_window_ms must be between 1000 and 10000.")
        if not 30 <= webhook_tolerance <= 3600:
            raise _payment_config_error("Bybit Pay webhook_tolerance_seconds must be between 30 and 3600.")
        normalized["timeout_seconds"] = timeout_seconds
        normalized["recv_window_ms"] = recv_window
        normalized["webhook_tolerance_seconds"] = webhook_tolerance

        terminal_type = str(normalized.get("terminal_type") or "WEB").strip().upper()
        if terminal_type not in {"APP", "WEB", "WAP", "MINIAPP", "OTHERS"}:
            raise _payment_config_error("Bybit Pay terminal_type is invalid.")
        normalized["terminal_type"] = terminal_type

        limits = {
            "shopping_name": 120,
            "goods_name": 160,
            "goods_detail": 256,
            "mcc_code": 16,
            "merchant_name": 120,
            "client_id": 128,
        }
        defaults = {
            "shopping_name": "GH Bot Factory",
            "goods_name": "Wallet top-up",
            "goods_detail": "Digital wallet balance top-up",
            "mcc_code": "5816",
        }
        for key, limit in limits.items():
            value = str(normalized.get(key, defaults.get(key, "")) or "").strip()
            if len(value) > limit or any(ord(char) < 32 for char in value):
                raise _payment_config_error(f"Bybit Pay {key} is invalid or exceeds {limit} characters.")
            if value:
                normalized[key] = value
            else:
                normalized.pop(key, None)

        currency_types_raw = normalized.get("currency_types")
        if not isinstance(currency_types_raw, dict) or not currency_types_raw:
            raise _payment_config_error(
                "Bybit Pay currency_types must explicitly map each wallet currency to fiat or crypto."
            )
        currency_types: dict[str, str] = {}
        for raw_currency, raw_kind in currency_types_raw.items():
            currency = str(raw_currency).strip().upper()
            kind = str(raw_kind).strip().lower()
            if len(currency) != 3 or not currency.isalpha() or kind not in {"fiat", "crypto"}:
                raise _payment_config_error("Bybit Pay currency_types contains an invalid mapping.")
            currency_types[currency] = kind
        normalized["currency_types"] = currency_types

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
        if provider_name == "bybit_pay":
            currency_types = normalized.get("currency_types", {})
            missing = sorted(set(currencies) - set(currency_types))
            if missing:
                raise _payment_config_error(
                    f"Bybit Pay currency_types is missing top-up currencies: {missing}."
                )

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


class SupplierAdapterCredentialResponse(BaseModel):
    key: str
    label: str
    required: bool
    description: str


class SupplierAdapterDefinitionResponse(BaseModel):
    key: str
    display_name: str
    description: str
    driver_family: str
    categories: list[ProviderCategory]
    capabilities: list[str]
    capabilities_by_category: dict[str, list[str]]
    credentials: list[SupplierAdapterCredentialResponse]
    docs_url: str | None


class SupplierProviderResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    provider_type: str
    category: ProviderCategory
    capabilities: list[str]
    is_enabled: bool
    priority: int
    health_status: ProviderHealthStatus
    consecutive_failures: int
    last_health_check_at: datetime | None
    last_health_latency_ms: float | None
    last_health_message: str | None
    metadata: dict[str, Any]
    credentials: list[SupplierCredentialStatus]
    missing_required_credentials: list[str]
    mapping_count: int


class SupplierProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    provider_type: str = Field(min_length=1, max_length=50)
    category: ProviderCategory = ProviderCategory.DIGITAL_PRODUCT
    is_enabled: bool = True
    priority: int = Field(default=1, ge=1, le=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SupplierProviderUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    category: ProviderCategory | None = None
    is_enabled: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=1000)
    metadata: dict[str, Any] | None = None


class SupplierCredentialUpsertRequest(BaseModel):
    credential_type: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    secret_ref: str | None = Field(default=None, min_length=2, max_length=255, pattern=_SECRET_REF_PATTERN)
    secret_value: SecretStr | None = None


class SupplierRoutingPolicyRequest(BaseModel):
    product_id: uuid.UUID
    product_variant_id: uuid.UUID | None = None
    strategy: ProviderRoutingStrategy = ProviderRoutingStrategy.PRIORITY
    preferred_provider_id: uuid.UUID | None = None
    failover_enabled: bool = True
    weights: dict[str, int] = Field(default_factory=dict)


class SupplierRoutingPolicyResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_variant_id: uuid.UUID | None
    strategy: ProviderRoutingStrategy
    preferred_provider_id: uuid.UUID | None
    failover_enabled: bool
    weights: dict[str, int]


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


class OpenApiInspectRequest(BaseModel):
    document: dict[str, Any]


class OpenApiOperationResponse(BaseModel):
    operation_id: str
    method: str
    path: str
    summary: str | None


class OpenApiInspectionResponse(BaseModel):
    version: str
    title: str | None
    operations: list[OpenApiOperationResponse]


class NumberServiceResponse(BaseModel):
    code: str
    name: str
    metadata: dict[str, Any]


class NumberCountryResponse(BaseModel):
    code: str
    name: str
    dial_code: str | None
    metadata: dict[str, Any]


class NumberOfferResponse(BaseModel):
    service: str
    country: str
    cost: Decimal
    currency: str
    available_quantity: int | None
    operator: str | None
    provider_offer_id: str | None
    metadata: dict[str, Any]


class SupplierHealthResponse(BaseModel):
    provider_id: uuid.UUID
    status: ProviderHealthStatus
    latency_ms: float | None
    message: str | None
    balance: Decimal | None
    balance_currency: str | None


class SupplierOfferRefreshRequest(BaseModel):
    product_id: uuid.UUID
    product_variant_id: uuid.UUID | None = None


class SupplierOfferSnapshotResponse(BaseModel):
    id: uuid.UUID
    provider_id: uuid.UUID
    mapping_id: uuid.UUID
    product_id: uuid.UUID
    product_variant_id: uuid.UUID | None
    external_product_id: str
    display_name: str | None
    cost_amount: Decimal
    cost_currency: str
    is_available: bool
    stock_quantity: int | None
    min_quantity: int
    max_quantity: int
    observed_at: datetime
    expires_at: datetime
    is_fresh: bool
    last_error_at: datetime | None
    last_error_code: str | None


class SupplierOfferRefreshFailureResponse(BaseModel):
    mapping_id: uuid.UUID
    provider_id: uuid.UUID
    error_code: str


class SupplierOfferRefreshResponse(BaseModel):
    refreshed: int
    unavailable: int
    failed: int
    failures: list[SupplierOfferRefreshFailureResponse]
    offers: list[SupplierOfferSnapshotResponse]


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
    credentials_value: SecretStr | None = Field(default=None, min_length=1, max_length=16384)
    webhook_secret_ref: str | None = Field(default=None, min_length=2, max_length=255, pattern=_SECRET_REF_PATTERN)
    webhook_secret_value: SecretStr | None = Field(default=None, min_length=1, max_length=16384)
    settings: dict[str, Any] = Field(default_factory=dict)


class ProviderCapabilitiesResponse(BaseModel):
    supplier_provider_types: list[str]
    supplier_provider_categories: list[ProviderCategory]
    payment_provider_names: list[str]


def _supplier_response(provider: Provider, mapping_count: int) -> SupplierProviderResponse:
    provider_registry.get_definition(provider.provider_type)
    configured = {credential.credential_type.upper() for credential in provider.credentials}
    required = provider_registry.required_credentials_for(
        provider.provider_type, provider.metadata_json or {}
    )
    missing = [key for key in required if key not in configured]
    return SupplierProviderResponse(
        id=provider.id,
        name=provider.name,
        slug=provider.slug,
        provider_type=provider.provider_type,
        category=provider.category,
        capabilities=[
            capability.value
            for capability in provider_registry.capabilities_for(
                provider.provider_type, provider.category, provider.metadata_json or {}
            )
        ],
        is_enabled=provider.is_enabled,
        priority=provider.priority,
        health_status=provider.health_status,
        consecutive_failures=provider.consecutive_failures,
        last_health_check_at=provider.last_health_check_at,
        last_health_latency_ms=provider.last_health_latency_ms,
        last_health_message=provider.last_health_message,
        metadata=provider.metadata_json or {},
        credentials=[
            SupplierCredentialStatus(credential_type=credential.credential_type)
            for credential in sorted(provider.credentials, key=lambda row: row.credential_type)
        ],
        missing_required_credentials=missing,
        mapping_count=mapping_count,
    )


@router.get("/provider-capabilities", response_model=ProviderCapabilitiesResponse)
async def provider_capabilities(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
) -> ProviderCapabilitiesResponse:
    del principal
    return ProviderCapabilitiesResponse(
        supplier_provider_types=list(provider_registry.registered_types()),
        supplier_provider_categories=list(ProviderCategory),
        payment_provider_names=list(default_payment_provider_registry.registered_provider_names()),
    )


@router.get("/provider-adapters", response_model=list[SupplierAdapterDefinitionResponse])
async def provider_adapter_catalog(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
) -> list[SupplierAdapterDefinitionResponse]:
    del principal
    return [
        SupplierAdapterDefinitionResponse(
            key=definition.normalized_key,
            display_name=definition.display_name,
            description=definition.description,
            driver_family=definition.driver_family,
            categories=list(definition.categories),
            capabilities=[capability.value for capability in definition.capabilities],
            capabilities_by_category={
                category.value: [
                    capability.value for capability in definition.capabilities_for(category)
                ]
                for category in definition.categories
            },
            credentials=[
                SupplierAdapterCredentialResponse(
                    key=credential.normalized_key(),
                    label=credential.label,
                    required=credential.required,
                    description=credential.description,
                )
                for credential in definition.credentials
            ],
            docs_url=definition.docs_url,
        )
        for definition in provider_registry.definitions()
    ]


@router.post("/provider-openapi/inspect", response_model=OpenApiInspectionResponse)
async def inspect_provider_openapi(
    req: OpenApiInspectRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
) -> OpenApiInspectionResponse:
    del principal
    try:
        result = inspect_openapi_document(req.document)
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return OpenApiInspectionResponse(
        version=result.version,
        title=result.title,
        operations=[
            OpenApiOperationResponse(
                operation_id=item.operation_id,
                method=item.method,
                path=item.path,
                summary=item.summary,
            )
            for item in result.operations
        ],
    )


async def _number_operation_error(exc: Exception) -> None:
    if isinstance(exc, ProviderConfigurationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Provider operation failed. Verify provider health and configuration.",
    ) from exc


@router.get(
    "/providers/{provider_id}/number/services",
    response_model=list[NumberServiceResponse],
)
async def list_provider_number_services(
    provider_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[NumberServiceResponse]:
    try:
        items = await ProviderOperationsService().list_number_services(
            session, tenant_id=principal.tenant_id, provider_id=provider_id
        )
    except ProviderError as exc:
        await _number_operation_error(exc)
        raise AssertionError("unreachable")
    return [
        NumberServiceResponse(code=item.code, name=item.name, metadata=item.metadata)
        for item in items
    ]


@router.get(
    "/providers/{provider_id}/number/countries",
    response_model=list[NumberCountryResponse],
)
async def list_provider_number_countries(
    provider_id: uuid.UUID,
    service: str | None = Query(default=None, min_length=1, max_length=100),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[NumberCountryResponse]:
    try:
        items = await ProviderOperationsService().list_number_countries(
            session,
            tenant_id=principal.tenant_id,
            provider_id=provider_id,
            service=service,
        )
    except ProviderError as exc:
        await _number_operation_error(exc)
        raise AssertionError("unreachable")
    return [
        NumberCountryResponse(
            code=item.code,
            name=item.name,
            dial_code=item.dial_code,
            metadata=item.metadata,
        )
        for item in items
    ]


@router.get(
    "/providers/{provider_id}/number/offers",
    response_model=list[NumberOfferResponse],
)
async def list_provider_number_offers(
    provider_id: uuid.UUID,
    service: str = Query(min_length=1, max_length=100),
    country: str = Query(min_length=1, max_length=20),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[NumberOfferResponse]:
    try:
        items = await ProviderOperationsService().list_number_offers(
            session,
            tenant_id=principal.tenant_id,
            provider_id=provider_id,
            service=service,
            country=country,
        )
    except ProviderError as exc:
        await _number_operation_error(exc)
        raise AssertionError("unreachable")
    return [
        NumberOfferResponse(
            service=item.service,
            country=item.country,
            cost=item.cost,
            currency=item.currency,
            available_quantity=item.available_quantity,
            operator=item.operator,
            provider_offer_id=item.provider_offer_id,
            metadata=item.metadata,
        )
        for item in items
    ]


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
    try:
        provider_registry.validate_config(provider_type, req.metadata, req.category)
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    provider = Provider(
        tenant_id=principal.tenant_id,
        name=req.name.strip(),
        slug=req.slug.strip().lower(),
        provider_type=provider_type,
        category=req.category,
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
        details={"slug": provider.slug, "provider_type": provider.provider_type, "category": provider.category.value},
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
    candidate_category = changes.get("category") or provider.category
    candidate_metadata = changes.get("metadata", provider.metadata_json or {}) or {}
    if "metadata" in changes:
        _reject_secret_material(candidate_metadata, path="metadata")
    try:
        provider_registry.validate_config(
            provider.provider_type, candidate_metadata, candidate_category
        )
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if "metadata" in changes:
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
    if (req.secret_ref is None) == (req.secret_value is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Provide exactly one of secret_ref or secret_value.",
        )
    credential_type = req.credential_type.strip().upper()
    definition = provider_registry.get_definition(provider.provider_type)
    declared = {spec.normalized_key() for spec in definition.credentials}
    if declared and credential_type not in declared:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Credential type {credential_type} is not declared by adapter {definition.normalized_key}.",
        )

    if req.secret_value is not None:
        try:
            credential = await upsert_provider_credential_value(
                session,
                provider=provider,
                credential_type=credential_type,
                secret_value=req.secret_value.get_secret_value(),
            )
        except ProviderConfigurationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        action = "SUPPLIER_CREDENTIAL_SECRET_UPDATED"
    else:
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
                secret_ref=req.secret_ref or "",
            )
            session.add(credential)
        else:
            credential.secret_ref = req.secret_ref or credential.secret_ref
        action = "SUPPLIER_CREDENTIAL_REFERENCE_UPDATED"

    await _audit(
        session,
        principal,
        action=action,
        resource_type="provider",
        resource_id=provider.id,
        details={"credential_type": credential_type, "managed_secret": req.secret_value is not None},
    )
    await session.commit()
    return SupplierCredentialStatus(credential_type=credential_type)


@router.delete("/providers/{provider_id}/credentials/{credential_type}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_supplier_credential(
    provider_id: uuid.UUID,
    credential_type: str,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    normalized = credential_type.strip().upper()
    credential = await session.scalar(
        select(ProviderCredential).where(
            ProviderCredential.provider_id == provider_id,
            ProviderCredential.tenant_id == principal.tenant_id,
            ProviderCredential.credential_type == normalized,
        )
    )
    if credential is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider credential not found.")
    await delete_provider_credential(session, credential=credential)
    await _audit(
        session,
        principal,
        action="SUPPLIER_CREDENTIAL_REMOVED",
        resource_type="provider",
        resource_id=provider_id,
        details={"credential_type": normalized},
    )
    await session.commit()


def _offer_snapshot_response(snapshot: ProviderOfferSnapshot) -> SupplierOfferSnapshotResponse:
    expires_at = snapshot.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return SupplierOfferSnapshotResponse(
        id=snapshot.id,
        provider_id=snapshot.provider_id,
        mapping_id=snapshot.mapping_id,
        product_id=snapshot.product_id,
        product_variant_id=snapshot.product_variant_id,
        external_product_id=snapshot.external_product_id,
        display_name=snapshot.display_name,
        cost_amount=snapshot.cost_amount,
        cost_currency=snapshot.cost_currency,
        is_available=snapshot.is_available,
        stock_quantity=snapshot.stock_quantity,
        min_quantity=snapshot.min_quantity,
        max_quantity=snapshot.max_quantity,
        observed_at=snapshot.observed_at,
        expires_at=snapshot.expires_at,
        is_fresh=expires_at > datetime.now(UTC),
        last_error_at=snapshot.last_error_at,
        last_error_code=snapshot.last_error_code,
    )


async def _require_owned_product_scope(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    variant_id: uuid.UUID | None,
) -> None:
    product = await session.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.tenant_id == tenant_id,
            Product.deleted_at.is_(None),
        )
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")
    if variant_id is not None:
        variant = await session.scalar(
            select(ProductVariant).where(
                ProductVariant.id == variant_id,
                ProductVariant.product_id == product_id,
                ProductVariant.deleted_at.is_(None),
            )
        )
        if variant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Product variant not found."
            )


@router.get("/provider-offers", response_model=list[SupplierOfferSnapshotResponse])
async def list_supplier_offer_snapshots(
    product_id: uuid.UUID = Query(...),
    product_variant_id: uuid.UUID | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[SupplierOfferSnapshotResponse]:
    await _require_owned_product_scope(
        session,
        tenant_id=principal.tenant_id,
        product_id=product_id,
        variant_id=product_variant_id,
    )
    offers = await ProviderOfferAggregator().list_offers(
        session,
        tenant_id=principal.tenant_id,
        product_id=product_id,
        variant_id=product_variant_id,
    )
    return [_offer_snapshot_response(offer) for offer in offers]


@router.post("/provider-offers/refresh", response_model=SupplierOfferRefreshResponse)
async def refresh_supplier_offer_snapshots(
    req: SupplierOfferRefreshRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> SupplierOfferRefreshResponse:
    await _require_owned_product_scope(
        session,
        tenant_id=principal.tenant_id,
        product_id=req.product_id,
        variant_id=req.product_variant_id,
    )
    aggregator = ProviderOfferAggregator()
    result = await aggregator.refresh_product(
        session,
        tenant_id=principal.tenant_id,
        product_id=req.product_id,
        variant_id=req.product_variant_id,
    )
    await _audit(
        session,
        principal,
        action="SUPPLIER_OFFERS_REFRESHED",
        resource_type="product",
        resource_id=req.product_id,
        details={
            "product_variant_id": str(req.product_variant_id) if req.product_variant_id else None,
            "refreshed": result.refreshed,
            "unavailable": result.unavailable,
            "failed": result.failed,
        },
    )
    await session.commit()
    offers = await aggregator.list_offers(
        session,
        tenant_id=principal.tenant_id,
        product_id=req.product_id,
        variant_id=req.product_variant_id,
    )
    return SupplierOfferRefreshResponse(
        refreshed=result.refreshed,
        unavailable=result.unavailable,
        failed=result.failed,
        failures=[
            SupplierOfferRefreshFailureResponse(
                mapping_id=item.mapping_id,
                provider_id=item.provider_id,
                error_code=item.error_code,
            )
            for item in result.failures
        ],
        offers=[_offer_snapshot_response(offer) for offer in offers],
    )


def _routing_policy_response(policy: ProviderRoutingPolicy) -> SupplierRoutingPolicyResponse:
    return SupplierRoutingPolicyResponse(
        id=policy.id,
        product_id=policy.product_id,
        product_variant_id=policy.product_variant_id,
        strategy=policy.strategy,
        preferred_provider_id=policy.preferred_provider_id,
        failover_enabled=policy.failover_enabled,
        weights={str(key): int(value) for key, value in (policy.weights_json or {}).items()},
    )


async def _validate_routing_policy_request(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    req: SupplierRoutingPolicyRequest,
) -> dict[str, int]:
    product = await session.scalar(
        select(Product).where(
            Product.id == req.product_id,
            Product.tenant_id == tenant_id,
            Product.deleted_at.is_(None),
        )
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")
    if req.product_variant_id is not None:
        variant = await session.scalar(
            select(ProductVariant).where(
                ProductVariant.id == req.product_variant_id,
                ProductVariant.product_id == product.id,
                ProductVariant.deleted_at.is_(None),
            )
        )
        if variant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product variant not found.")

    if req.strategy == ProviderRoutingStrategy.MANUAL and req.preferred_provider_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="MANUAL routing requires preferred_provider_id.",
        )
    if req.strategy != ProviderRoutingStrategy.MANUAL and req.preferred_provider_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="preferred_provider_id is only valid for MANUAL routing.",
        )
    if req.strategy != ProviderRoutingStrategy.WEIGHTED and req.weights:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="weights are only valid for WEIGHTED routing.",
        )

    normalized_weights: dict[str, int] = {}
    provider_ids: set[uuid.UUID] = set()
    if req.preferred_provider_id is not None:
        provider_ids.add(req.preferred_provider_id)
    for raw_id, raw_weight in req.weights.items():
        try:
            provider_id = uuid.UUID(str(raw_id))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Routing weight keys must be provider UUIDs.",
            ) from exc
        if raw_weight < 1 or raw_weight > 1000:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Routing weights must be integers between 1 and 1000.",
            )
        provider_ids.add(provider_id)
        normalized_weights[str(provider_id)] = int(raw_weight)

    if provider_ids:
        owned_ids = set(
            (
                await session.execute(
                    select(Provider.id).where(
                        Provider.tenant_id == tenant_id,
                        Provider.id.in_(provider_ids),
                        Provider.is_enabled.is_(True),
                    )
                )
            ).scalars().all()
        )
        if owned_ids != provider_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Routing policy references an unavailable provider connection.",
            )
        mapping_stmt = select(ProviderProductMapping.provider_id).where(
            ProviderProductMapping.tenant_id == tenant_id,
            ProviderProductMapping.product_id == req.product_id,
            ProviderProductMapping.provider_id.in_(provider_ids),
            ProviderProductMapping.is_enabled.is_(True),
        )
        if req.product_variant_id is not None:
            mapping_stmt = mapping_stmt.where(
                (ProviderProductMapping.product_variant_id == req.product_variant_id)
                | (ProviderProductMapping.product_variant_id.is_(None))
            )
        mapped_ids = set((await session.execute(mapping_stmt)).scalars().all())
        if mapped_ids != provider_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Routing policy providers must have enabled mappings for this product scope.",
            )
    return normalized_weights


@router.get("/provider-routing-policies", response_model=list[SupplierRoutingPolicyResponse])
async def list_supplier_routing_policies(
    product_id: uuid.UUID | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[SupplierRoutingPolicyResponse]:
    stmt = select(ProviderRoutingPolicy).where(
        ProviderRoutingPolicy.tenant_id == principal.tenant_id
    )
    if product_id is not None:
        stmt = stmt.where(ProviderRoutingPolicy.product_id == product_id)
    policies = list(
        (
            await session.execute(
                stmt.order_by(
                    ProviderRoutingPolicy.product_id.asc(),
                    ProviderRoutingPolicy.product_variant_id.asc(),
                )
            )
        ).scalars().all()
    )
    return [_routing_policy_response(policy) for policy in policies]


@router.put("/provider-routing-policies", response_model=SupplierRoutingPolicyResponse)
async def upsert_supplier_routing_policy(
    req: SupplierRoutingPolicyRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> SupplierRoutingPolicyResponse:
    normalized_weights = await _validate_routing_policy_request(
        session, tenant_id=principal.tenant_id, req=req
    )
    scope_filters = [
        ProviderRoutingPolicy.tenant_id == principal.tenant_id,
        ProviderRoutingPolicy.product_id == req.product_id,
    ]
    if req.product_variant_id is None:
        scope_filters.append(ProviderRoutingPolicy.product_variant_id.is_(None))
    else:
        scope_filters.append(ProviderRoutingPolicy.product_variant_id == req.product_variant_id)
    policy = await session.scalar(select(ProviderRoutingPolicy).where(*scope_filters).with_for_update())
    created = policy is None
    if policy is None:
        policy = ProviderRoutingPolicy(
            tenant_id=principal.tenant_id,
            product_id=req.product_id,
            product_variant_id=req.product_variant_id,
        )
        session.add(policy)
    policy.strategy = req.strategy
    policy.preferred_provider_id = req.preferred_provider_id
    policy.failover_enabled = req.failover_enabled
    policy.weights_json = normalized_weights
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Routing policy was updated concurrently; retry the request.",
        ) from exc
    await _audit(
        session,
        principal,
        action="SUPPLIER_ROUTING_POLICY_CREATED" if created else "SUPPLIER_ROUTING_POLICY_UPDATED",
        resource_type="provider_routing_policy",
        resource_id=policy.id,
        details={
            "product_id": str(req.product_id),
            "product_variant_id": str(req.product_variant_id) if req.product_variant_id else None,
            "strategy": req.strategy.value,
            "failover_enabled": req.failover_enabled,
            "weighted_provider_count": len(normalized_weights),
        },
    )
    await session.commit()
    return _routing_policy_response(policy)


@router.delete("/provider-routing-policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier_routing_policy(
    policy_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    policy = await session.scalar(
        select(ProviderRoutingPolicy).where(
            ProviderRoutingPolicy.id == policy_id,
            ProviderRoutingPolicy.tenant_id == principal.tenant_id,
        )
    )
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Routing policy not found.")
    await _audit(
        session,
        principal,
        action="SUPPLIER_ROUTING_POLICY_DELETED",
        resource_type="provider_routing_policy",
        resource_id=policy.id,
        details={"product_id": str(policy.product_id)},
    )
    await session.delete(policy)
    await session.commit()


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
    try:
        result = await test_provider_connection(session, provider=provider)
        await _audit(
            session,
            principal,
            action="SUPPLIER_PROVIDER_HEALTH_CHECKED",
            resource_type="provider",
            resource_id=provider.id,
            details={"status": result.status.value, "adapter": provider.provider_type, "category": provider.category.value},
        )
        await session.commit()
        return SupplierHealthResponse(
            provider_id=provider.id,
            status=result.status,
            latency_ms=result.latency_ms,
            message=result.message,
            balance=result.balance,
            balance_currency=result.balance_currency,
        )
    except ProviderConfigurationError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except ProviderError as exc:
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
            detail="Provider connection test failed. Verify adapter configuration and credentials.",
        ) from exc
    except Exception as exc:
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
            detail="Provider connection test failed. Verify adapter configuration and credentials.",
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


def _managed_payment_secret_ref(tenant_id: uuid.UUID, provider_name: str, purpose: str) -> str:
    normalized_provider = provider_name.upper().replace("-", "_")
    normalized_purpose = purpose.upper().replace("-", "_")
    return f"GHBF_PAYMENT_{tenant_id.hex}_{normalized_provider}_{normalized_purpose}"


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
    if req.credentials_ref and req.credentials_value is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Provide exactly one of credentials_ref or credentials_value.",
        )
    if req.webhook_secret_ref and req.webhook_secret_value is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Provide exactly one of webhook_secret_ref or webhook_secret_value.",
        )
    storage = get_default_secret_storage()
    config = (
        await session.execute(
            select(PaymentProviderConfig).where(
                PaymentProviderConfig.tenant_id == principal.tenant_id,
                PaymentProviderConfig.provider_name == normalized,
            )
        )
    ).scalar_one_or_none()
    fields_set = req.model_fields_set
    if normalized == "gozapay" and req.is_enabled:
        if not normalized_settings.get("experimental_risk_acknowledged"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "GoZaPay is classified as an experimental gateway in GH Bot Factory; "
                    "set experimental_risk_acknowledged=true after reviewing the provider risk notice."
                ),
            )
        if not normalized_settings.get("nominal_stablecoin_parity_acknowledged"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "GoZaPay fixed top-ups currently require nominal_stablecoin_parity_acknowledged=true. "
                    "Flexible amount auto-credit is intentionally disabled until multi-asset/FX ledger support exists."
                ),
            )
    if normalized == "bybit_pay" and req.is_enabled:
        has_existing_webhook_key = bool(config and config.webhook_secret_ref)
        has_requested_webhook_key = bool(req.webhook_secret_ref or req.webhook_secret_value is not None)
        if not has_existing_webhook_key and not has_requested_webhook_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Bybit Pay requires the platform webhook public key when enabled.",
            )
    if config is None:
        if not req.credentials_ref and req.credentials_value is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="credentials_ref or credentials_value is required when creating a payment provider configuration.",
            )
        credentials_ref = req.credentials_ref
        if req.credentials_value is not None:
            credentials_ref = _managed_payment_secret_ref(principal.tenant_id, normalized, "credentials")
            await storage.set_secret(credentials_ref, req.credentials_value.get_secret_value())
        webhook_secret_ref = req.webhook_secret_ref
        if req.webhook_secret_value is not None:
            webhook_secret_ref = _managed_payment_secret_ref(principal.tenant_id, normalized, "webhook")
            await storage.set_secret(webhook_secret_ref, req.webhook_secret_value.get_secret_value())
        config = PaymentProviderConfig(
            tenant_id=principal.tenant_id,
            provider_name=normalized,
            is_enabled=req.is_enabled,
            credentials_ref=credentials_ref,
            webhook_secret_ref=webhook_secret_ref,
            settings_json=normalized_settings,
        )
        session.add(config)
        action = "PAYMENT_PROVIDER_CREATED"
    else:
        config.is_enabled = req.is_enabled
        config.settings_json = normalized_settings
        if "credentials_value" in fields_set and req.credentials_value is not None:
            managed_ref = _managed_payment_secret_ref(principal.tenant_id, normalized, "credentials")
            await storage.set_secret(managed_ref, req.credentials_value.get_secret_value())
            config.credentials_ref = managed_ref
        elif "credentials_ref" in fields_set and req.credentials_ref:
            config.credentials_ref = req.credentials_ref
        if "webhook_secret_value" in fields_set and req.webhook_secret_value is not None:
            managed_ref = _managed_payment_secret_ref(principal.tenant_id, normalized, "webhook")
            await storage.set_secret(managed_ref, req.webhook_secret_value.get_secret_value())
            config.webhook_secret_ref = managed_ref
        elif "webhook_secret_ref" in fields_set:
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
