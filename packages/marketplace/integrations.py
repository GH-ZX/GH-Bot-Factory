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
            description="Automated number reservations and real-time SMS activation codes via 5sim / SMS-Activate.",
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
    ]
}


def list_integration_offerings() -> list[IntegrationOffering]:
    return [item for item in _INTEGRATIONS.values() if item.is_active]


def get_integration_offering(key: str) -> IntegrationOffering | None:
    return _INTEGRATIONS.get(key.strip().lower())
