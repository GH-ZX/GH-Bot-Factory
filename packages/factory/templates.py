from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

_TEMPLATE_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,49}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?$")
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_TELEGRAM_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9_]{5,32}$")

SUPPORTED_MODULES = frozenset({"catalog", "orders", "account"})
PUBLIC_BRANDING_KEYS = frozenset(
    {
        "welcome_text",
        "store_tagline",
        "store_description",
        "brand_accent",
        "brand_logo_url",
        "support_contact",
        "support_url",
        "menu_text",
        "store_button_text",
    }
)


class TemplateValidationError(ValueError):
    pass


@dataclass(frozen=True)
class BotTemplate:
    key: str
    version: int
    name: str
    description: str
    recommended_for: str
    default_config: dict[str, Any]

    def public_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "recommended_for": self.recommended_for,
            "default_config": copy.deepcopy(self.default_config),
        }


def _template(
    key: str,
    name: str,
    description: str,
    recommended_for: str,
    *,
    accent: str,
    welcome: str,
    tagline: str,
    modules: list[str] | None = None,
) -> BotTemplate:
    return BotTemplate(
        key=key,
        version=1,
        name=name,
        description=description,
        recommended_for=recommended_for,
        default_config={
            "currency": "USD",
            "locale": "en",
            "branding": {
                "welcome_text": welcome,
                "store_tagline": tagline,
                "store_description": description,
                "brand_accent": accent,
                "brand_logo_url": "",
                "support_contact": "",
                "support_url": "",
                "menu_text": "Open Store",
                "store_button_text": "🛍️ Open Store",
            },
            "enabled_modules": modules or ["catalog", "orders", "account"],
        },
    )


_TEMPLATES: dict[str, BotTemplate] = {
    item.key: item
    for item in [
        _template(
            "general-commerce",
            "General Commerce",
            "Balanced storefront for most digital catalogs and service businesses.",
            "Default choice for the first production bot.",
            accent="#7C6CFF",
            welcome="Welcome to our store!",
            tagline="Fast checkout. Secure delivery. Built for Telegram.",
        ),
        _template(
            "digital-goods",
            "Digital Goods",
            "Delivery-first layout for keys, subscriptions, gift cards, and downloadable goods.",
            "Stores where fulfillment speed and order history matter most.",
            accent="#2D8CFF",
            welcome="Welcome! Your digital products are only a few taps away.",
            tagline="Instant digital delivery with a clear order trail.",
        ),
        _template(
            "gift-cards",
            "Gift Cards",
            "Simple catalog for prepaid cards, vouchers, and denomination-based products.",
            "Gift-card and voucher storefronts with fast digital fulfillment.",
            accent="#F59E0B",
            welcome="Choose a gift card and we will deliver it securely.",
            tagline="Digital gift cards and vouchers with tracked delivery.",
        ),
        _template(
            "gaming-store",
            "Gaming Store",
            "High-energy storefront for game credits, top-ups, memberships, and codes.",
            "Gaming communities and virtual goods catalogs.",
            accent="#9B5CFF",
            welcome="Game on. Choose what you need and we will handle the delivery.",
            tagline="Credits, codes, and gaming essentials delivered securely.",
        ),
        _template(
            "services",
            "Services",
            "Clean storefront for fixed-price services and assisted fulfillment.",
            "Consulting, setup packages, digital services, and managed work.",
            accent="#00A884",
            welcome="Welcome. Choose the service that fits what you need.",
            tagline="Simple service ordering with transparent status tracking.",
        ),
    ]
}


def list_bot_templates() -> list[BotTemplate]:
    return list(_TEMPLATES.values())


def get_bot_template(key: str, version: int | None = None) -> BotTemplate:
    normalized = key.strip().lower()
    if not _TEMPLATE_KEY_RE.fullmatch(normalized) or normalized not in _TEMPLATES:
        raise TemplateValidationError("Unknown bot template.")
    template = _TEMPLATES[normalized]
    if version is not None and version != template.version:
        raise TemplateValidationError(
            f"Template {normalized!r} version {version} is unavailable; current version is {template.version}."
        )
    return template


def _validate_https_url(value: str, field_name: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise TemplateValidationError(f"{field_name} must be an HTTPS URL without embedded credentials.")
    return value


def validate_branding(branding: dict[str, Any] | None) -> dict[str, str]:
    if not branding:
        return {}
    unknown = set(branding) - PUBLIC_BRANDING_KEYS
    if unknown:
        raise TemplateValidationError(f"Unsupported branding keys: {', '.join(sorted(unknown))}.")

    normalized: dict[str, str] = {}
    limits = {
        "welcome_text": 500,
        "store_tagline": 240,
        "store_description": 500,
        "support_contact": 64,
        "menu_text": 64,
        "store_button_text": 64,
    }
    for key, value in branding.items():
        if value is None:
            continue
        if not isinstance(value, str):
            raise TemplateValidationError(f"branding.{key} must be a string.")
        text = value.strip()
        if key in limits and len(text) > limits[key]:
            raise TemplateValidationError(f"branding.{key} exceeds {limits[key]} characters.")
        normalized[key] = text

    accent = normalized.get("brand_accent")
    if accent and not _HEX_COLOR_RE.fullmatch(accent):
        raise TemplateValidationError("branding.brand_accent must be a six-digit hex color such as #7C6CFF.")
    if "brand_logo_url" in normalized:
        normalized["brand_logo_url"] = _validate_https_url(normalized["brand_logo_url"], "branding.brand_logo_url")
    if "support_url" in normalized:
        normalized["support_url"] = _validate_https_url(normalized["support_url"], "branding.support_url")
    support_contact = normalized.get("support_contact")
    if support_contact and not _TELEGRAM_HANDLE_RE.fullmatch(support_contact):
        raise TemplateValidationError("branding.support_contact must be a Telegram @handle or blank.")
    return normalized


def build_template_config(
    *,
    template_key: str,
    template_version: int | None = None,
    currency: str | None = None,
    locale: str | None = None,
    branding: dict[str, Any] | None = None,
    enabled_modules: list[str] | None = None,
) -> dict[str, Any]:
    template = get_bot_template(template_key, template_version)
    config = copy.deepcopy(template.default_config)

    if currency is not None:
        normalized_currency = currency.strip().upper()
        if not _CURRENCY_RE.fullmatch(normalized_currency):
            raise TemplateValidationError("currency must be a three-letter ISO-style code such as USD or XTR.")
        config["currency"] = normalized_currency

    if locale is not None:
        normalized_locale = locale.strip()
        if not _LOCALE_RE.fullmatch(normalized_locale):
            raise TemplateValidationError("locale must look like en, de, ar, or en-US.")
        config["locale"] = normalized_locale

    if enabled_modules is not None:
        modules = list(dict.fromkeys(enabled_modules))
        unknown_modules = set(modules) - SUPPORTED_MODULES
        if unknown_modules:
            raise TemplateValidationError(f"Unsupported modules: {', '.join(sorted(unknown_modules))}.")
        if not modules:
            raise TemplateValidationError("At least one bot module must be enabled.")
        config["enabled_modules"] = modules

    config["branding"].update(validate_branding(branding))
    config["_factory"] = {
        "template_key": template.key,
        "template_version": template.version,
    }
    return config


def template_metadata(config: dict[str, Any] | None) -> tuple[str | None, int | None]:
    factory = (config or {}).get("_factory")
    if not isinstance(factory, dict):
        return None, None
    key = factory.get("template_key")
    version = factory.get("template_version")
    return (str(key) if key else None, int(version) if isinstance(version, int) else None)
