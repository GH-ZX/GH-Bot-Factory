from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class IntegrationOffering:
    key: str
    name: str
    category: str
    description: str
    setup_fee: Decimal
    monthly_fee: Decimal
    supported_templates: tuple[str, ...]
    features: tuple[str, ...]
    requirements: tuple[str, ...]
    currency: str = "USD"
    is_active: bool = True

    def public_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "setup_fee": str(self.setup_fee),
            "monthly_fee": str(self.monthly_fee),
            "supported_templates": list(self.supported_templates),
            "features": list(self.features),
            "requirements": list(self.requirements),
            "currency": self.currency,
            "is_active": self.is_active,
        }


_INTEGRATIONS: dict[str, IntegrationOffering] = {
    item.key: item
    for item in [
        IntegrationOffering(
            key="numbers-sms",
            name="Virtual Numbers & SMS Activation",
            category="supplier_api",
            description="Automated number reservations and real-time SMS activation codes via Spider Service, 5sim, or SMS-Activate.",
            setup_fee=Decimal("30.00"),
            monthly_fee=Decimal("15.00"),
            supported_templates=("numbers-sms", "reseller-hub", "hybrid-store"),
            features=(
                "Real-time incoming SMS polling & timer",
                "Instant cancellation & balance refund",
                "Multiple country and service routing",
            ),
            requirements=(
                "Customer's own supplier API key",
                "Funded supplier balance",
            ),
        ),
        IntegrationOffering(
            key="spider-service",
            name="Spider Service (Virtual Numbers & SMS)",
            category="supplier_api",
            description="Automated numbers and SMS activation codes via api.spider-service.com with real-time code polling.",
            setup_fee=Decimal("30.00"),
            monthly_fee=Decimal("15.00"),
            supported_templates=("numbers-sms", "reseller-hub", "hybrid-store"),
            features=(
                "Real-time SMS code retrieval (getCode)",
                "Instant multi-country number reservation (getNumber)",
                "Live wallet balance tracking (getBalance)",
            ),
            requirements=(
                "Spider Service API key (apiKay)",
                "Funded Spider Service balance",
            ),
        ),
        IntegrationOffering(
            key="crypto-payments",
            name="Self-Custody Crypto & Stablecoins",
            category="payment_gateway",
            description="Direct-to-wallet on-chain payments in TRON USDt and EVM tokens without third-party escrow.",
            setup_fee=Decimal("25.00"),
            monthly_fee=Decimal("10.00"),
            supported_templates=(
                "general-commerce",
                "digital-goods",
                "gift-cards",
                "gaming-store",
                "services",
                "reseller-hub",
                "numbers-sms",
                "accounts-store",
                "gift-reseller",
                "digital-reseller",
                "hybrid-store",
            ),
            features=(
                "Zero platform payment processing fees",
                "Instant database-verified credit",
                "TRC-20 and ERC-20 token tracking",
            ),
            requirements=(
                "Customer's receiving wallet address",
            ),
        ),
        IntegrationOffering(
            key="binance-pay",
            name="Binance Pay Merchant Integration",
            category="payment_gateway",
            description="Official Binance Pay checkout with QR codes, mobile deep-links, and multi-crypto acceptance.",
            setup_fee=Decimal("35.00"),
            monthly_fee=Decimal("15.00"),
            supported_templates=(
                "general-commerce",
                "digital-goods",
                "gift-cards",
                "gaming-store",
                "services",
                "reseller-hub",
                "numbers-sms",
                "accounts-store",
                "gift-reseller",
                "digital-reseller",
                "hybrid-store",
            ),
            features=(
                "Seamless Binance mobile app deep linking",
                "Multi-crypto conversion to stablecoins",
                "Automated instant wallet settlement",
            ),
            requirements=(
                "Binance Merchant account & API credentials",
            ),
        ),
        IntegrationOffering(
            key="gift-cards-api",
            name="Wholesale Gift Cards & Codes API",
            category="supplier_api",
            description="Connect to digital gift card aggregators with lowest-cost supplier routing.",
            setup_fee=Decimal("30.00"),
            monthly_fee=Decimal("15.00"),
            supported_templates=("gift-reseller", "reseller-hub", "hybrid-store"),
            features=(
                "Automated digital code retrieval",
                "Lowest-cost supplier comparison",
                "Instant voucher reveal to shopper",
            ),
            requirements=(
                "Gift provider wholesale API account",
            ),
        ),
        IntegrationOffering(
            key="accounts-api",
            name="Automated Accounts Provider",
            category="supplier_api",
            description="Wholesale account delivery via automated credential feeds and warranty management.",
            setup_fee=Decimal("30.00"),
            monthly_fee=Decimal("15.00"),
            supported_templates=("accounts-store", "reseller-hub", "hybrid-store"),
            features=(
                "Structured username:password:token delivery",
                "Automated replacement tracking",
                "Multi-provider failover routing",
            ),
            requirements=(
                "Account provider account & API key",
            ),
        ),
        IntegrationOffering(
            key="ventebot",
            name="VenteBot Reseller Network",
            category="supplier_api",
            description="Direct connection to VenteBot wholesale API for automated account delivery, Grok/ChatGPT subscriptions, and activations.",
            setup_fee=Decimal("30.00"),
            monthly_fee=Decimal("15.00"),
            supported_templates=("accounts-store", "digital-reseller", "reseller-hub", "hybrid-store"),
            features=(
                "Instant wholesale catalog sync and stock checking",
                "Automated account credentials and activation fulfillment",
                "Idempotent order placement and live wallet balance tracking",
            ),
            requirements=(
                "VenteBot reseller API key (X-Reseller-Key)",
                "Funded VenteBot wallet balance",
            ),
        ),
        IntegrationOffering(
            key="g2bulk",
            name="G2Bulk Gaming Top-Ups & Vouchers",
            category="supplier_api",
            description="Direct connection to G2Bulk wholesale API for 180+ game top-ups (PUBG Mobile, Free Fire, MLBB), Razer Gold, Steam, and gift cards with sub-second delivery.",
            setup_fee=Decimal("30.00"),
            monthly_fee=Decimal("15.00"),
            supported_templates=(
                "gaming-store",
                "digital-goods",
                "gift-cards",
                "reseller-hub",
                "hybrid-store",
            ),
            features=(
                "Direct in-game player ID validation before charging",
                "1,100+ digital game vouchers and cards with live stock",
                "Instant voucher code delivery and automated status polling",
            ),
            requirements=(
                "G2Bulk API key (X-API-Key)",
                "Funded G2Bulk wallet balance via @G2BULKBOT",
            ),
        ),
        IntegrationOffering(
            key="supabase",
            name="Supabase Managed Database",
            category="database_backend",
            description="Connect your own Supabase project: dedicated managed PostgreSQL, Supavisor connection pooling, and direct SQL access.",
            setup_fee=Decimal("25.00"),
            monthly_fee=Decimal("10.00"),
            supported_templates=(
                "general-commerce",
                "digital-goods",
                "gift-cards",
                "gaming-store",
                "services",
                "reseller-hub",
                "numbers-sms",
                "accounts-store",
                "gift-reseller",
                "digital-reseller",
                "hybrid-store",
            ),
            features=(
                "Dedicated customer-owned PostgreSQL database on Supabase",
                "Full direct SQL access and Supabase Studio dashboard",
                "Supavisor transaction pooler compatibility (statement_cache_size=0)",
            ),
            requirements=(
                "Customer's Supabase project URL and database password",
            ),
        ),
        IntegrationOffering(
            key="custom-http-api",
            name="Custom OpenAPI / Generic HTTP Adapter",
            category="supplier_api",
            description="Declarative integration for any custom supplier REST/Swagger API with SSRF defenses.",
            setup_fee=Decimal("50.00"),
            monthly_fee=Decimal("20.00"),
            supported_templates=("digital-reseller", "reseller-hub", "hybrid-store"),
            features=(
                "Custom JSON endpoint and header mapping",
                "Strict allowlisted host and redirect defenses",
                "Credential redaction and audit trails",
            ),
            requirements=(
                "Swagger / OpenAPI specification or API docs",
            ),
        ),
        IntegrationOffering(
            key="custom-api-request",
            name="💡 Request Custom / Unlisted API",
            category="supplier_api",
            description="Need a supplier, bot API, or service not listed here? Tell us what you need and we will integrate it into the factory for you.",
            setup_fee=Decimal("0.00"),
            monthly_fee=Decimal("0.00"),
            supported_templates=(
                "general-commerce",
                "digital-goods",
                "gift-cards",
                "gaming-store",
                "services",
                "reseller-hub",
                "numbers-sms",
                "accounts-store",
                "gift-reseller",
                "digital-reseller",
                "hybrid-store",
            ),
            features=(
                "Connect any wholesale API of your choice",
                "Factory Owner develops adapter for your store",
                "Pre-configured in your tenant prior to launch",
            ),
            requirements=(
                "Supplier API documentation or website",
            ),
        ),
    ]
}


def list_integration_offerings() -> list[IntegrationOffering]:
    return [item for item in _INTEGRATIONS.values() if item.is_active]


def get_integration_offering(key: str) -> IntegrationOffering | None:
    return _INTEGRATIONS.get(key.strip().lower())
