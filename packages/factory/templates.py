from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from packages.factory.business_profiles import BotBusinessType
from packages.providers.models import ProviderCategory, ProviderRoutingStrategy

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
class TemplateGuidance:
    product_source: str
    what_you_can_sell: str
    delivery_experience: str
    operational_complexity: str
    setup_requirements: tuple[str, ...]
    example_business: str
    limitations: str
    supported_hosting: tuple[str, ...] = ("managed", "dedicated", "source_license")

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_source": self.product_source,
            "what_you_can_sell": self.what_you_can_sell,
            "delivery_experience": self.delivery_experience,
            "operational_complexity": self.operational_complexity,
            "setup_requirements": list(self.setup_requirements),
            "example_business": self.example_business,
            "limitations": self.limitations,
            "supported_hosting": list(self.supported_hosting),
        }


@dataclass(frozen=True)
class BotTemplate:
    key: str
    version: int
    name: str
    description: str
    recommended_for: str
    default_config: dict[str, Any]
    business_type: BotBusinessType = BotBusinessType.GENERAL
    provider_categories: tuple[ProviderCategory, ...] = ()
    default_routing_strategy: ProviderRoutingStrategy = ProviderRoutingStrategy.PRIORITY
    guidance: TemplateGuidance | None = None

    def public_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "recommended_for": self.recommended_for,
            "default_config": copy.deepcopy(self.default_config),
            "business_type": self.business_type.value,
            "provider_categories": [item.value for item in self.provider_categories],
            "default_routing_strategy": self.default_routing_strategy.value,
            "guidance": self.guidance.to_dict() if self.guidance else None,
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
    business_type: BotBusinessType = BotBusinessType.GENERAL,
    provider_categories: tuple[ProviderCategory, ...] = (),
    routing_strategy: ProviderRoutingStrategy = ProviderRoutingStrategy.PRIORITY,
    guidance: TemplateGuidance | None = None,
) -> BotTemplate:
    return BotTemplate(
        key=key,
        version=1,
        name=name,
        description=description,
        recommended_for=recommended_for,
        business_type=business_type,
        provider_categories=provider_categories,
        default_routing_strategy=routing_strategy,
        guidance=guidance,
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
            "_business": {
                "business_type": business_type.value,
                "provider_ids": [],
                "payment_method_ids": [],
                "routing_strategy": routing_strategy.value,
                "preferred_provider_id": None,
                "default_pricing_tier_id": None,
                "allow_flexible_auto_credit": False,
            },
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
            guidance=TemplateGuidance(
                product_source="stored",
                what_you_can_sell="Physical or digital items, manual orders, mixed catalogs, and standard merchandise.",
                delivery_experience="Standard cart checkout, order confirmation, and customer status tracking.",
                operational_complexity="Low",
                setup_requirements=(
                    "Upload product catalog and product images",
                    "Configure stock inventory and retail pricing",
                    "Enable at least one payment method",
                ),
                example_business="A boutique merchandise store or general goods merchant selling directly via Telegram.",
                limitations="Does not automate live 3rd-party supplier API fulfillment out of the box.",
                supported_hosting=("managed", "dedicated", "source_license"),
            ),
        ),
        _template(
            "digital-goods",
            "Digital Goods",
            "Delivery-first layout for keys, subscriptions, gift cards, and downloadable goods.",
            "Stores where fulfillment speed and order history matter most.",
            accent="#2D8CFF",
            welcome="Welcome! Your digital products are only a few taps away.",
            tagline="Instant digital delivery with a clear order trail.",
            guidance=TemplateGuidance(
                product_source="stored",
                what_you_can_sell="License keys, software downloads, digital files, pre-generated vouchers, and subscriptions.",
                delivery_experience="Immediate digital artifact delivery in bot chat and order history screen with one-tap clipboard copy.",
                operational_complexity="Low",
                setup_requirements=(
                    "Add digital product variants",
                    "Load digital stock or keys inventory",
                    "Enable payment gateway",
                ),
                example_business="A software license seller or digital asset merchant fulfilling downloadable items instantly.",
                limitations="Keys and digital artifacts must be stocked in advance unless connected to a supplier API.",
                supported_hosting=("managed", "dedicated", "source_license"),
            ),
        ),
        _template(
            "gift-cards",
            "Gift Cards",
            "Simple catalog for prepaid cards, vouchers, and denomination-based products.",
            "Gift-card and voucher storefronts with fast digital fulfillment.",
            accent="#F59E0B",
            welcome="Choose a gift card and we will deliver it securely.",
            tagline="Digital gift cards and vouchers with tracked delivery.",
            guidance=TemplateGuidance(
                product_source="stored",
                what_you_can_sell="Fixed-denomination gift cards, store credit vouchers, and digital coupons.",
                delivery_experience="Fast digital code delivery with redemption instructions and one-tap copy button.",
                operational_complexity="Low",
                setup_requirements=(
                    "Create card denominations (e.g. $10, $25, $50)",
                    "Pre-stock voucher codes in inventory",
                    "Configure customer payment method",
                ),
                example_business="A store credit or brand voucher distributor with managed manual stock.",
                limitations="Fixed denominations only; no live balance polling against third-party gift networks.",
                supported_hosting=("managed", "dedicated", "source_license"),
            ),
        ),
        _template(
            "gaming-store",
            "Gaming Store",
            "High-energy storefront for game credits, top-ups, memberships, and codes.",
            "Gaming communities and virtual goods catalogs.",
            accent="#9B5CFF",
            welcome="Game on. Choose what you need and we will handle the delivery.",
            tagline="Credits, codes, and gaming essentials delivered securely.",
            guidance=TemplateGuidance(
                product_source="hybrid",
                what_you_can_sell="Game keys, points top-ups, in-game currency, game accounts, and subscription passes.",
                delivery_experience="High-energy storefront with instant key delivery or player ID top-up confirmation.",
                operational_complexity="Medium",
                setup_requirements=(
                    "Categorize game titles and products",
                    "Stock game keys or connect gaming provider APIs",
                    "Configure crypto or wallet payments",
                ),
                example_business="A gaming community shop selling Steam/PlayStation codes, Riot Points, and gamer passes.",
                limitations="Player ID collection is required for assisted direct top-ups.",
                supported_hosting=("managed", "dedicated", "source_license"),
            ),
        ),
        _template(
            "services",
            "Services",
            "Clean storefront for fixed-price services and assisted fulfillment.",
            "Consulting, setup packages, digital services, and managed work.",
            accent="#00A884",
            welcome="Welcome. Choose the service that fits what you need.",
            tagline="Simple service ordering with transparent status tracking.",
            guidance=TemplateGuidance(
                product_source="stored",
                what_you_can_sell="Fixed-price consulting, technical setup, graphic design, translation, or assisted services.",
                delivery_experience="Consultation booking, milestone tracking, and manual order status completion.",
                operational_complexity="Low",
                setup_requirements=(
                    "Define service offerings and deliverables",
                    "Set turnaround timelines",
                    "Specify customer contact and intake channel",
                ),
                example_business="A freelance design or IT setup agency selling scoped service packages.",
                limitations="Fulfillment requires human operator action; not fully automated.",
                supported_hosting=("managed", "dedicated", "source_license"),
            ),
        ),
        _template(
            "reseller-hub",
            "Multi-API Reseller",
            "Provider-agnostic reseller storefront with routing, wallet funding, and margin controls.",
            "Multi-supplier stores that aggregate APIs and choose providers dynamically.",
            accent="#6D5EF7",
            welcome="Welcome. Choose a product and we will route it through the best available supplier.",
            tagline="Multi-provider catalog, automated fulfillment, and transparent order tracking.",
            business_type=BotBusinessType.RESELLER,
            provider_categories=tuple(ProviderCategory),
            routing_strategy=ProviderRoutingStrategy.AVAILABILITY,
            guidance=TemplateGuidance(
                product_source="provider_api",
                what_you_can_sell="Multi-category digital catalog aggregating multiple suppliers with automated routing.",
                delivery_experience="Real-time upstream supplier execution with automatic fallback and live fulfillment status.",
                operational_complexity="High",
                setup_requirements=(
                    "Connect 2+ provider adapters",
                    "Fund upstream supplier deposits",
                    "Map supplier products to catalog",
                    "Set pricing markup and profit margin rules",
                ),
                example_business="A digital goods reseller aggregating wholesale APIs across several countries and suppliers.",
                limitations="Requires active monitoring of upstream supplier balances and API health.",
                supported_hosting=("managed", "dedicated", "source_license"),
            ),
        ),
        _template(
            "numbers-sms",
            "Numbers & SMS",
            "Number reservation and SMS activation storefront using canonical number-provider operations.",
            "5sim-style number/SMS providers and similar activation services.",
            accent="#00A8E8",
            welcome="Choose a service and country to reserve a number securely.",
            tagline="Fast number reservations with tracked activation status.",
            business_type=BotBusinessType.NUMBER_SMS,
            provider_categories=(ProviderCategory.NUMBER,),
            routing_strategy=ProviderRoutingStrategy.AVAILABILITY,
            guidance=TemplateGuidance(
                product_source="provider_api",
                what_you_can_sell="Virtual phone numbers, SMS verification codes, and temporary activation lines.",
                delivery_experience="Interactive number reservation timer, real-time incoming SMS polling, and one-tap cancellation/refund.",
                operational_complexity="Medium",
                setup_requirements=(
                    "Connect number provider adapter (e.g. 5sim/SMS-Activate)",
                    "Fund supplier account balance",
                    "Configure country and service pricing tiers",
                ),
                example_business="A virtual number service allowing customers to activate WhatsApp, Telegram, or OpenAI accounts.",
                limitations="Depends on supplier carrier stock and carrier SMS reception rates.",
                supported_hosting=("managed", "dedicated", "source_license"),
            ),
        ),
        _template(
            "accounts-store",
            "Accounts Store",
            "Storefront for account inventory supplied through one or more account providers.",
            "Account reseller APIs with provider fallback and margin rules.",
            accent="#EC4899",
            welcome="Browse available accounts and receive tracked digital delivery.",
            tagline="Account inventory from trusted suppliers with resilient fulfillment.",
            business_type=BotBusinessType.ACCOUNT,
            provider_categories=(ProviderCategory.ACCOUNT,),
            routing_strategy=ProviderRoutingStrategy.PRIORITY,
            guidance=TemplateGuidance(
                product_source="hybrid",
                what_you_can_sell="Verified digital accounts, social media profiles, gaming handles, and pre-configured accounts.",
                delivery_experience="Instant credentials delivery (username:password:token) with security instructions and copy buttons.",
                operational_complexity="Medium",
                setup_requirements=(
                    "Upload pre-made accounts or link account supplier API",
                    "Set replacement warranty and check rules",
                    "Configure customer onboarding instructions",
                ),
                example_business="An agency selling aged social accounts, verified developer profiles, or gaming accounts.",
                limitations="Requires clear warranty policy for account replacements if credentials expire.",
                supported_hosting=("managed", "dedicated", "source_license"),
            ),
        ),
        _template(
            "gift-reseller",
            "Gift Cards & Codes",
            "Gift-card, voucher, and code reseller storefront backed by multiple APIs.",
            "Gift-code sellers such as game, prepaid, and voucher catalogs.",
            accent="#F97316",
            welcome="Choose your gift card or code and we will deliver it securely.",
            tagline="Gift cards and digital codes with supplier-aware pricing.",
            business_type=BotBusinessType.GIFT_CARD,
            provider_categories=(ProviderCategory.GIFT, ProviderCategory.DIGITAL_PRODUCT),
            routing_strategy=ProviderRoutingStrategy.LOWEST_COST,
            guidance=TemplateGuidance(
                product_source="provider_api",
                what_you_can_sell="Automated wholesale gift card codes, prepaid cards, and game cards via supplier APIs.",
                delivery_experience="Instant code retrieval from lowest-cost active supplier with zero manual inventory holding.",
                operational_complexity="Medium",
                setup_requirements=(
                    "Connect gift provider APIs",
                    "Configure lowest-cost routing strategy",
                    "Set profit margins and tier discounts",
                ),
                example_business="An automated gift card reseller offering Apple, Google Play, and Amazon cards.",
                limitations="Wholesale suppliers may run out of stock during peak seasonal periods.",
                supported_hosting=("managed", "dedicated", "source_license"),
            ),
        ),
        _template(
            "digital-reseller",
            "Digital Products Reseller",
            "General digital-product reseller template for subscriptions, codes, services, and API inventory.",
            "Swagger/OpenAPI-backed resellers and mixed digital product catalogs.",
            accent="#10B981",
            welcome="Browse our digital catalog and track every delivery from one place.",
            tagline="Digital inventory, multi-provider routing, and automated fulfillment.",
            business_type=BotBusinessType.DIGITAL_PRODUCT,
            provider_categories=(ProviderCategory.DIGITAL_PRODUCT, ProviderCategory.GIFT, ProviderCategory.SERVICE),
            routing_strategy=ProviderRoutingStrategy.PRIORITY,
            guidance=TemplateGuidance(
                product_source="provider_api",
                what_you_can_sell="Subscriptions, cloud accounts, software licenses, and digital services via OpenAPI/HTTP suppliers.",
                delivery_experience="Automated order forwarding to supplier with tracked webhook or polling delivery.",
                operational_complexity="Medium",
                setup_requirements=(
                    "Configure generic HTTP or built-in provider adapters",
                    "Map endpoints and response fields",
                    "Set pricing markup tier",
                ),
                example_business="A reseller connecting to custom supplier REST APIs to deliver VPN or SaaS subscriptions.",
                limitations="Custom HTTP providers must comply with SSRF and schema allowlists.",
                supported_hosting=("managed", "dedicated", "source_license"),
            ),
        ),
        _template(
            "hybrid-store",
            "Hybrid Store",
            "One bot for numbers, accounts, gifts, digital products, and services.",
            "Tenants that want one storefront across several provider categories.",
            accent="#8B5CF6",
            welcome="Everything you need in one store. Choose a category to get started.",
            tagline="One storefront across multiple supplier and payment integrations.",
            business_type=BotBusinessType.HYBRID,
            provider_categories=tuple(ProviderCategory),
            routing_strategy=ProviderRoutingStrategy.AVAILABILITY,
            guidance=TemplateGuidance(
                product_source="hybrid",
                what_you_can_sell="Full-spectrum digital catalog: numbers, accounts, gift cards, digital files, and services.",
                delivery_experience="Unified store with category-specific fulfillment (SMS timer, key reveal, account credentials).",
                operational_complexity="High",
                setup_requirements=(
                    "Configure multiple provider categories",
                    "Load manual inventory for stored items",
                    "Establish routing policies per category",
                ),
                example_business="A full-scale digital mega-store catering to gaming, virtual numbers, and software in one bot.",
                limitations="Higher operational complexity to monitor multiple supplier balances simultaneously.",
                supported_hosting=("managed", "dedicated", "source_license"),
            ),
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
    business_profile: dict[str, Any] | None = None,
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
    if business_profile is not None:
        # Cross-entity tenant validation is performed by the API/service layer.
        from packages.factory.business_profiles import parse_business_profile
        config["_business"] = parse_business_profile(business_profile).public_payload()
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
