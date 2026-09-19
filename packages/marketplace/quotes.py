from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, ClassVar

from packages.factory.templates import TemplateValidationError, get_bot_template
from packages.marketplace.integrations import get_integration_offering


@dataclass(frozen=True)
class QuoteLineItem:
    name: str
    category: str  # "product_format", "template", "product_source", "integration", "hosting"
    item_type: str  # "one_time", "recurring"
    amount: Decimal
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "item_type": self.item_type,
            "amount": str(self.amount),
            "description": self.description,
        }


@dataclass(frozen=True)
class ConfiguratorEstimate:
    format: str
    template_key: str
    template_name: str
    product_source: str
    delivery_model: str
    selected_integrations: tuple[str, ...]
    items: tuple[QuoteLineItem, ...]
    total_one_time: Decimal
    total_monthly: Decimal
    currency: str = "USD"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "template_key": self.template_key,
            "template_name": self.template_name,
            "product_source": self.product_source,
            "delivery_model": self.delivery_model,
            "selected_integrations": list(self.selected_integrations),
            "items": [item.to_dict() for item in self.items],
            "total_one_time": str(self.total_one_time),
            "total_monthly": str(self.total_monthly),
            "currency": self.currency,
            "notes": self.notes,
        }


class QuoteEngine:
    # Product Format Pricing
    FORMAT_PRICING: ClassVar[dict[str, dict[str, Any]]] = {
        "bot": {
            "name": "Telegram Bot Storefront",
            "setup": Decimal("49.00"),
            "monthly": Decimal("29.00"),
            "desc": "Full-featured automated Telegram store with messaging, commands, and order alerts.",
        },
        "miniapp": {
            "name": "Telegram Mini App Storefront",
            "setup": Decimal("69.00"),
            "monthly": Decimal("39.00"),
            "desc": "Rich visual web app launched directly inside Telegram with modern UI and catalog search.",
        },
        "combo": {
            "name": "Telegram Bot + Mini App Companion Bundle",
            "setup": Decimal("89.00"),
            "monthly": Decimal("49.00"),
            "desc": "The complete package: native Bot chat interactions plus high-conversion Mini App interface.",
        },
    }

    # Product Source Complexity
    SOURCE_PRICING: ClassVar[dict[str, dict[str, Any]]] = {
        "stored": {
            "name": "Stored Manual Inventory Setup",
            "setup": Decimal("0.00"),
            "monthly": Decimal("0.00"),
            "desc": "Client-managed stored inventory, manual vouchers, or consulting service catalog.",
        },
        "provider_api": {
            "name": "Wholesale Provider API Routing Engine",
            "setup": Decimal("15.00"),
            "monthly": Decimal("10.00"),
            "desc": "Automated multi-supplier routing, upstream balance reconciliation, and failover.",
        },
        "hybrid": {
            "name": "Hybrid (Stored Inventory + Live APIs)",
            "setup": Decimal("25.00"),
            "monthly": Decimal("15.00"),
            "desc": "Combines manual stored goods with live wholesale provider APIs in one unified bot.",
        },
    }

    # Delivery & Hosting Models
    HOSTING_PRICING: ClassVar[dict[str, dict[str, Any]]] = {
        "managed": {
            "name": "Managed Cloud Hosting & Daily Backups",
            "setup": Decimal("0.00"),
            "monthly": Decimal("0.00"),  # Included in base format monthly
            "desc": "Fully hosted on our high-availability cloud cluster with SSL, database backups, and updates.",
        },
        "supabase_cloud": {
            "name": "Supabase Dedicated Cloud Deployment",
            "setup": Decimal("150.00"),
            "monthly": Decimal("25.00"),
            "desc": "Dedicated managed PostgreSQL on customer's Supabase project with automated migrations.",
        },
        "dedicated": {
            "name": "Dedicated Single-Tenant VPS Deployment",
            "setup": Decimal("450.00"),
            "monthly": Decimal("50.00"),
            "desc": "Isolated export deployed to your own VPS with private database and dedicated Redis.",
        },
        "source_license": {
            "name": "Perpetual Source Code Buyout License",
            "setup": Decimal("2000.00"),
            "monthly": Decimal("0.00"),
            "desc": "Full source code ownership for single-business deployment with complete technical freedom.",
        },
    }

    @classmethod
    def calculate_estimate(
        cls,
        *,
        format: str,
        template_key: str,
        product_source: str,
        delivery_model: str,
        integration_keys: list[str] | None = None,
    ) -> ConfiguratorEstimate:
        norm_format = format.strip().lower()
        if norm_format not in cls.FORMAT_PRICING:
            norm_format = "combo"
        f_spec = cls.FORMAT_PRICING[norm_format]

        norm_source = product_source.strip().lower()
        if norm_source not in cls.SOURCE_PRICING:
            norm_source = "stored"
        s_spec = cls.SOURCE_PRICING[norm_source]

        norm_model = delivery_model.strip().lower()
        if norm_model not in cls.HOSTING_PRICING:
            norm_model = "managed"
        h_spec = cls.HOSTING_PRICING[norm_model]

        try:
            template = get_bot_template(template_key)
            t_name = template.name
        except (TemplateValidationError, ValueError):
            t_name = "General Store Template"
        items: list[QuoteLineItem] = []

        # 1. Base format
        items.append(
            QuoteLineItem(
                name=f_spec["name"],
                category="product_format",
                item_type="one_time",
                amount=f_spec["setup"],
                description=f_spec["desc"],
            )
        )
        if norm_model != "source_license":
            items.append(
                QuoteLineItem(
                    name=f"{f_spec['name']} (Monthly Hosting)",
                    category="product_format",
                    item_type="recurring",
                    amount=f_spec["monthly"],
                    description="Cloud infrastructure, rate-limiting, and Telegram webhook maintenance.",
                )
            )

        # 2. Product source add-on
        if s_spec["setup"] > Decimal("0.00"):
            items.append(
                QuoteLineItem(
                    name=s_spec["name"],
                    category="product_source",
                    item_type="one_time",
                    amount=s_spec["setup"],
                    description=s_spec["desc"],
                )
            )
        if norm_model != "source_license" and s_spec["monthly"] > Decimal("0.00"):
            items.append(
                QuoteLineItem(
                    name=f"{s_spec['name']} (Maintenance)",
                    category="product_source",
                    item_type="recurring",
                    amount=s_spec["monthly"],
                    description="Continuous API polling and balance health monitoring.",
                )
            )

        # 3. Delivery / Hosting model
        if h_spec["setup"] > Decimal("0.00"):
            items.append(
                QuoteLineItem(
                    name=h_spec["name"],
                    category="hosting",
                    item_type="one_time",
                    amount=h_spec["setup"],
                    description=h_spec["desc"],
                )
            )
        if norm_model != "source_license" and h_spec["monthly"] > Decimal("0.00"):
            items.append(
                QuoteLineItem(
                    name=f"{h_spec['name']} (Maintenance)",
                    category="hosting",
                    item_type="recurring",
                    amount=h_spec["monthly"],
                    description="Dedicated server operational maintenance.",
                )
            )

        # 4. Integration add-ons
        valid_integrations: list[str] = []
        for ikey in (integration_keys or []):
            offering = get_integration_offering(ikey)
            if not offering:
                continue
            valid_integrations.append(offering.key)
            if offering.setup_fee > Decimal("0.00"):
                items.append(
                    QuoteLineItem(
                        name=f"{offering.name} Setup",
                        category="integration",
                        item_type="one_time",
                        amount=offering.setup_fee,
                        description=f"Initial configuration and testing for {offering.name}.",
                    )
                )
            if norm_model != "source_license" and offering.monthly_fee > Decimal("0.00"):
                items.append(
                    QuoteLineItem(
                        name=f"{offering.name} License",
                        category="integration",
                        item_type="recurring",
                        amount=offering.monthly_fee,
                        description=f"Ongoing integration entitlement for {offering.name}.",
                    )
                )

        total_one_time = sum((item.amount for item in items if item.item_type == "one_time"), start=Decimal("0.00"))
        total_monthly = sum((item.amount for item in items if item.item_type == "recurring"), start=Decimal("0.00"))

        notes = (
            "Source code buyout gives you complete ownership; monthly hosting is 0. "
            if norm_model == "source_license"
            else "First payment includes one-time setup plus first month's hosting."
        )

        return ConfiguratorEstimate(
            format=norm_format,
            template_key=template_key,
            template_name=t_name,
            product_source=norm_source,
            delivery_model=norm_model,
            selected_integrations=tuple(valid_integrations),
            items=tuple(items),
            total_one_time=total_one_time,
            total_monthly=total_monthly,
            currency="USD",
            notes=notes,
        )
