from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.economics_models import PricingTier
from packages.payments.models import PaymentMethodConfig
from packages.providers.models import Provider, ProviderCategory, ProviderRoutingStrategy


class BotBusinessType(str, enum.Enum):
    GENERAL = "GENERAL"
    RESELLER = "RESELLER"
    NUMBER_SMS = "NUMBER_SMS"
    ACCOUNT = "ACCOUNT"
    GIFT_CARD = "GIFT_CARD"
    DIGITAL_PRODUCT = "DIGITAL_PRODUCT"
    HYBRID = "HYBRID"


@dataclass(frozen=True, slots=True)
class BotBusinessProfile:
    business_type: BotBusinessType = BotBusinessType.GENERAL
    provider_ids: tuple[uuid.UUID, ...] = ()
    payment_method_ids: tuple[uuid.UUID, ...] = ()
    routing_strategy: ProviderRoutingStrategy | None = None
    preferred_provider_id: uuid.UUID | None = None
    default_pricing_tier_id: uuid.UUID | None = None
    allow_flexible_auto_credit: bool = True

    def public_payload(self) -> dict[str, Any]:
        return {
            "business_type": self.business_type.value,
            "provider_ids": [str(value) for value in self.provider_ids],
            "payment_method_ids": [str(value) for value in self.payment_method_ids],
            "routing_strategy": self.routing_strategy.value if self.routing_strategy else None,
            "preferred_provider_id": str(self.preferred_provider_id) if self.preferred_provider_id else None,
            "default_pricing_tier_id": (
                str(self.default_pricing_tier_id) if self.default_pricing_tier_id else None
            ),
            "allow_flexible_auto_credit": self.allow_flexible_auto_credit,
        }


_BUSINESS_PROVIDER_CATEGORIES: dict[BotBusinessType, frozenset[ProviderCategory]] = {
    BotBusinessType.GENERAL: frozenset(ProviderCategory),
    BotBusinessType.RESELLER: frozenset(ProviderCategory),
    BotBusinessType.HYBRID: frozenset(ProviderCategory),
    BotBusinessType.NUMBER_SMS: frozenset({ProviderCategory.NUMBER}),
    BotBusinessType.ACCOUNT: frozenset({ProviderCategory.ACCOUNT}),
    BotBusinessType.GIFT_CARD: frozenset({ProviderCategory.GIFT, ProviderCategory.DIGITAL_PRODUCT}),
    BotBusinessType.DIGITAL_PRODUCT: frozenset(
        {ProviderCategory.DIGITAL_PRODUCT, ProviderCategory.GIFT, ProviderCategory.SERVICE}
    ),
}


def allowed_provider_categories(business_type: BotBusinessType) -> frozenset[ProviderCategory]:
    return _BUSINESS_PROVIDER_CATEGORIES[business_type]


def _uuid_list(value: Any, field: str) -> tuple[uuid.UUID, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of UUIDs.")  # noqa: TRY004 - public configuration validation uses ValueError
    result: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw in value:
        try:
            parsed = uuid.UUID(str(raw))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} contains an invalid UUID.") from exc
        if parsed not in seen:
            result.append(parsed)
            seen.add(parsed)
    if len(result) > 100:
        raise ValueError(f"{field} cannot contain more than 100 entries.")
    return tuple(result)


def _optional_uuid(value: Any, field: str) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a UUID or null.") from exc


def parse_business_profile(value: Any) -> BotBusinessProfile:
    if value is None:
        return BotBusinessProfile()
    if not isinstance(value, dict):
        raise ValueError("business_profile must be an object.")  # noqa: TRY004 - public configuration validation uses ValueError
    allowed = {
        "business_type",
        "provider_ids",
        "payment_method_ids",
        "routing_strategy",
        "preferred_provider_id",
        "default_pricing_tier_id",
        "allow_flexible_auto_credit",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"Unsupported business profile fields: {', '.join(sorted(unknown))}.")
    try:
        business_type = BotBusinessType(str(value.get("business_type") or "GENERAL").strip().upper())
    except ValueError as exc:
        raise ValueError("Unsupported business_type.") from exc
    raw_routing = value.get("routing_strategy")
    if raw_routing in (None, ""):
        routing_strategy = None
    else:
        try:
            routing_strategy = ProviderRoutingStrategy(str(raw_routing).strip().upper())
        except ValueError as exc:
            raise ValueError("Unsupported routing_strategy.") from exc
    allow_auto_credit = value.get("allow_flexible_auto_credit", False)
    if not isinstance(allow_auto_credit, bool):
        raise ValueError("allow_flexible_auto_credit must be a boolean.")  # noqa: TRY004 - public configuration validation uses ValueError
    profile = BotBusinessProfile(
        business_type=business_type,
        provider_ids=_uuid_list(value.get("provider_ids"), "provider_ids"),
        payment_method_ids=_uuid_list(value.get("payment_method_ids"), "payment_method_ids"),
        routing_strategy=routing_strategy,
        preferred_provider_id=_optional_uuid(value.get("preferred_provider_id"), "preferred_provider_id"),
        default_pricing_tier_id=_optional_uuid(
            value.get("default_pricing_tier_id"), "default_pricing_tier_id"
        ),
        allow_flexible_auto_credit=allow_auto_credit,
    )
    if profile.preferred_provider_id and profile.preferred_provider_id not in profile.provider_ids:
        raise ValueError("preferred_provider_id must also be present in provider_ids.")
    if profile.routing_strategy == ProviderRoutingStrategy.MANUAL and not profile.preferred_provider_id:
        raise ValueError("MANUAL routing requires preferred_provider_id.")
    return profile


def business_profile_from_config(config: dict[str, Any] | None) -> BotBusinessProfile:
    if not isinstance(config, dict):
        return BotBusinessProfile()
    try:
        return parse_business_profile(config.get("_business"))
    except ValueError:
        # Persisted malformed configuration must fail closed rather than broaden access.
        # Non-empty sentinel selections resolve to no tenant-owned providers/payment methods.
        deny = uuid.UUID(int=0)
        return BotBusinessProfile(
            provider_ids=(deny,),
            payment_method_ids=(deny,),
            routing_strategy=None,
            allow_flexible_auto_credit=False,
        )


async def validate_business_profile_for_tenant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    profile: BotBusinessProfile,
) -> BotBusinessProfile:
    if profile.provider_ids:
        providers = list(
            (
                await session.execute(
                    select(Provider).where(
                        Provider.tenant_id == tenant_id,
                        Provider.id.in_(profile.provider_ids),
                    )
                )
            ).scalars().all()
        )
        if {row.id for row in providers} != set(profile.provider_ids):
            raise ValueError("One or more selected providers do not belong to this tenant.")
        allowed_categories = allowed_provider_categories(profile.business_type)
        incompatible = [row.name for row in providers if row.category not in allowed_categories]
        if incompatible:
            raise ValueError(
                "Selected providers are incompatible with this business template: "
                + ", ".join(sorted(incompatible))
            )

    if profile.payment_method_ids:
        method_ids = set(
            (
                await session.execute(
                    select(PaymentMethodConfig.id).where(
                        PaymentMethodConfig.tenant_id == tenant_id,
                        PaymentMethodConfig.id.in_(profile.payment_method_ids),
                    )
                )
            ).scalars().all()
        )
        if method_ids != set(profile.payment_method_ids):
            raise ValueError("One or more selected payment methods do not belong to this tenant.")

    if profile.default_pricing_tier_id is not None:
        tier = await session.scalar(
            select(PricingTier).where(
                PricingTier.id == profile.default_pricing_tier_id,
                PricingTier.tenant_id == tenant_id,
                PricingTier.is_active.is_(True),
            )
        )
        if tier is None:
            raise ValueError("Selected default pricing tier is unavailable for this tenant.")
    return profile
