from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from packages.providers.models import ProviderCategory


class ProviderCapability(str, enum.Enum):
    """Canonical upstream capabilities understood by the commerce/provider layer."""

    HEALTH = "HEALTH"
    BALANCE = "BALANCE"
    CATALOG = "CATALOG"
    PRODUCT_DETAIL = "PRODUCT_DETAIL"
    CREATE_ORDER = "CREATE_ORDER"
    ORDER_STATUS = "ORDER_STATUS"
    CANCEL_ORDER = "CANCEL_ORDER"
    WEBHOOK = "WEBHOOK"
    POLLING = "POLLING"

    # Category-specific canonical operations (Phase 10.1).
    NUMBER_SERVICES = "NUMBER_SERVICES"
    NUMBER_COUNTRIES = "NUMBER_COUNTRIES"
    NUMBER_OFFERS = "NUMBER_OFFERS"
    NUMBER_RESERVE = "NUMBER_RESERVE"
    NUMBER_ACTIVATION = "NUMBER_ACTIVATION"
    NUMBER_CANCEL = "NUMBER_CANCEL"
    NUMBER_FINISH = "NUMBER_FINISH"


@dataclass(frozen=True, slots=True)
class ProviderCredentialSpec:
    key: str
    label: str
    required: bool = True
    description: str = ""

    def normalized_key(self) -> str:
        return self.key.strip().upper()


@dataclass(frozen=True, slots=True)
class ProviderConfigFieldSpec:
    key: str
    label: str
    value_type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    """Public adapter manifest.

    It intentionally contains no tenant credentials and no executable config. It is safe to
    expose to the tenant Admin UI so the factory can drive provider setup from capabilities
    instead of hard-coding vendor names into product logic.
    """

    key: str
    display_name: str
    description: str
    categories: tuple[ProviderCategory, ...]
    capabilities: tuple[ProviderCapability, ...]
    credentials: tuple[ProviderCredentialSpec, ...] = ()
    config_fields: tuple[ProviderConfigFieldSpec, ...] = ()
    driver_family: str = "python"
    docs_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    category_capabilities: dict[ProviderCategory, tuple[ProviderCapability, ...]] = field(
        default_factory=dict
    )

    @property
    def normalized_key(self) -> str:
        return self.key.strip().upper()

    def supports_category(self, category: ProviderCategory) -> bool:
        return category in self.categories

    def capabilities_for(self, category: ProviderCategory) -> tuple[ProviderCapability, ...]:
        return self.category_capabilities.get(category, self.capabilities)

    def required_credential_keys(self) -> tuple[str, ...]:
        return tuple(spec.normalized_key() for spec in self.credentials if spec.required)


CORE_PROVIDER_CAPABILITIES = (
    ProviderCapability.HEALTH,
    ProviderCapability.BALANCE,
    ProviderCapability.CATALOG,
    ProviderCapability.PRODUCT_DETAIL,
    ProviderCapability.CREATE_ORDER,
    ProviderCapability.ORDER_STATUS,
)
