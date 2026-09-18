from collections.abc import Callable
from typing import Any

from packages.providers.catalog import (
    CORE_PROVIDER_CAPABILITIES,
    ProviderCapability,
    ProviderConfigFieldSpec,
    ProviderCredentialSpec,
    ProviderDefinition,
)
from packages.providers.clients.base import BaseProviderClient
from packages.providers.clients.example_digital import ExampleDigitalCodesProvider
from packages.providers.clients.mock import MockProvider
from packages.providers.clients.ventebot import VenteBotClient
from packages.providers.exceptions import ProviderConfigurationError, ProviderError
from packages.providers.http_generic import (
    GenericHttpProvider,
    generic_http_capabilities,
    generic_http_required_credentials,
    validate_generic_http_metadata,
)
from packages.providers.models import ProviderCategory

ProviderFactory = Callable[[str, dict[str, Any]], BaseProviderClient]
ProviderConfigValidator = Callable[[dict[str, Any], ProviderCategory], None]
ProviderCapabilityResolver = Callable[[dict[str, Any], ProviderCategory], tuple[ProviderCapability, ...]]
ProviderCredentialResolver = Callable[[dict[str, Any]], set[str]]


class ProviderClientRegistry:
    """Dependency-injection registry plus public manifests for upstream provider adapters."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}
        self._singletons: dict[str, BaseProviderClient] = {}
        self._definitions: dict[str, ProviderDefinition] = {}
        self._config_validators: dict[str, ProviderConfigValidator] = {}
        self._capability_resolvers: dict[str, ProviderCapabilityResolver] = {}
        self._credential_resolvers: dict[str, ProviderCredentialResolver] = {}

        self.register_type(
            "MOCK",
            lambda name, cfg: MockProvider(provider_name=name, config=cfg),
            definition=ProviderDefinition(
                key="MOCK",
                display_name="Mock / Sandbox Provider",
                description="Deterministic local provider used to build and validate reseller flows without real money or external APIs.",
                categories=tuple(ProviderCategory),
                capabilities=CORE_PROVIDER_CAPABILITIES
                + (ProviderCapability.CANCEL_ORDER, ProviderCapability.POLLING),
                credentials=(),
                driver_family="sandbox",
                category_capabilities={
                    ProviderCategory.NUMBER: CORE_PROVIDER_CAPABILITIES
                    + (
                        ProviderCapability.CANCEL_ORDER,
                        ProviderCapability.POLLING,
                        ProviderCapability.NUMBER_SERVICES,
                        ProviderCapability.NUMBER_COUNTRIES,
                        ProviderCapability.NUMBER_OFFERS,
                        ProviderCapability.NUMBER_RESERVE,
                        ProviderCapability.NUMBER_ACTIVATION,
                        ProviderCapability.NUMBER_CANCEL,
                        ProviderCapability.NUMBER_FINISH,
                    ),
                },
            ),
        )
        self.register_type(
            "DIGITAL_CODES",
            lambda name, cfg: ExampleDigitalCodesProvider(provider_name=name, config=cfg),
            definition=ProviderDefinition(
                key="DIGITAL_CODES",
                display_name="Example Digital Codes",
                description="Built-in deterministic digital-code adapter used as a provider integration reference implementation.",
                categories=(ProviderCategory.GIFT, ProviderCategory.DIGITAL_PRODUCT),
                capabilities=CORE_PROVIDER_CAPABILITIES + (ProviderCapability.POLLING,),
                credentials=(),
                driver_family="example",
            ),
        )
        self.register_type(
            "HTTP_OPENAPI",
            lambda name, cfg: GenericHttpProvider(provider_name=name, config=cfg),
            definition=ProviderDefinition(
                key="HTTP_OPENAPI",
                display_name="Generic HTTP / OpenAPI",
                description=(
                    "Constrained declarative REST adapter for Swagger/OpenAPI-style reseller APIs. "
                    "It executes only explicitly mapped canonical operations and never generated code."
                ),
                categories=tuple(ProviderCategory),
                capabilities=(
                    ProviderCapability.HEALTH,
                    ProviderCapability.BALANCE,
                    ProviderCapability.CATALOG,
                    ProviderCapability.PRODUCT_DETAIL,
                    ProviderCapability.CREATE_ORDER,
                    ProviderCapability.ORDER_STATUS,
                    ProviderCapability.CANCEL_ORDER,
                    ProviderCapability.POLLING,
                ),
                category_capabilities={
                    ProviderCategory.NUMBER: (
                        ProviderCapability.HEALTH,
                        ProviderCapability.BALANCE,
                        ProviderCapability.CATALOG,
                        ProviderCapability.PRODUCT_DETAIL,
                        ProviderCapability.CREATE_ORDER,
                        ProviderCapability.ORDER_STATUS,
                        ProviderCapability.CANCEL_ORDER,
                        ProviderCapability.POLLING,
                        ProviderCapability.NUMBER_SERVICES,
                        ProviderCapability.NUMBER_COUNTRIES,
                        ProviderCapability.NUMBER_OFFERS,
                        ProviderCapability.NUMBER_RESERVE,
                        ProviderCapability.NUMBER_ACTIVATION,
                        ProviderCapability.NUMBER_CANCEL,
                        ProviderCapability.NUMBER_FINISH,
                    )
                },
                credentials=(
                    ProviderCredentialSpec(key="API_KEY", label="API Key", required=False),
                    ProviderCredentialSpec(key="API_SECRET", label="API Secret", required=False),
                    ProviderCredentialSpec(key="BEARER_TOKEN", label="Bearer Token", required=False),
                    ProviderCredentialSpec(key="USERNAME", label="Username", required=False),
                    ProviderCredentialSpec(key="PASSWORD", label="Password", required=False),
                ),
                driver_family="generic_http",
            ),
            config_validator=validate_generic_http_metadata,
            capability_resolver=generic_http_capabilities,
            credential_resolver=generic_http_required_credentials,
        )
        self.register_type(
            "VENTEBOT",
            lambda name, cfg: VenteBotClient(provider_name=name, config=cfg),
            definition=ProviderDefinition(
                key="VENTEBOT",
                display_name="VenteBot Reseller API",
                description=(
                    "Wholesale automated supplier for digital accounts, AI subscriptions (Grok, ChatGPT Plus), "
                    "and service activations via the VenteBot Reseller network."
                ),
                categories=(
                    ProviderCategory.ACCOUNT,
                    ProviderCategory.DIGITAL_PRODUCT,
                    ProviderCategory.SERVICE,
                ),
                capabilities=(
                    ProviderCapability.HEALTH,
                    ProviderCapability.BALANCE,
                    ProviderCapability.CATALOG,
                    ProviderCapability.PRODUCT_DETAIL,
                    ProviderCapability.CREATE_ORDER,
                    ProviderCapability.ORDER_STATUS,
                    ProviderCapability.POLLING,
                ),
                credentials=(
                    ProviderCredentialSpec(
                        key="API_KEY",
                        label="Reseller API Key (X-Reseller-Key)",
                        required=True,
                        description="API key generated from VenteBot or created by admin in Resellers tab.",
                    ),
                ),
                config_fields=(
                    ProviderConfigFieldSpec(
                        key="base_url",
                        label="VenteBot API Base URL",
                        default="https://ventetelegrambotrailway-production.up.railway.app",
                        required=False,
                        description="Base URL for the VenteBot reseller API.",
                    ),
                    ProviderConfigFieldSpec(
                        key="lang",
                        label="Catalog Language",
                        default="en",
                        required=False,
                        description="Default language for product descriptions (e.g. 'ar' for Arabic, 'en' for English).",
                    ),
                ),
                driver_family="ventebot",
                docs_url="https://ventetelegrambotrailway-production.up.railway.app/api/swagger/",
            ),
        )

    def register_type(
        self,
        provider_type: str,
        factory: ProviderFactory,
        *,
        definition: ProviderDefinition | None = None,
        config_validator: ProviderConfigValidator | None = None,
        capability_resolver: ProviderCapabilityResolver | None = None,
        credential_resolver: ProviderCredentialResolver | None = None,
    ) -> None:
        key = provider_type.strip().upper()
        if not key:
            raise ValueError("provider_type cannot be empty")
        self._factories[key] = factory
        self._definitions[key] = definition or ProviderDefinition(
            key=key,
            display_name=key.replace("_", " ").title(),
            description="Custom provider adapter.",
            categories=(ProviderCategory.DIGITAL_PRODUCT,),
            capabilities=CORE_PROVIDER_CAPABILITIES,
        )
        if config_validator is not None:
            self._config_validators[key] = config_validator
        if capability_resolver is not None:
            self._capability_resolvers[key] = capability_resolver
        if credential_resolver is not None:
            self._credential_resolvers[key] = credential_resolver

    def registered_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def definitions(self) -> tuple[ProviderDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def get_definition(self, provider_type: str) -> ProviderDefinition:
        key = provider_type.strip().upper()
        definition = self._definitions.get(key)
        if definition is None:
            raise ProviderError(
                f"Unsupported provider_type '{provider_type}'. Registered: {list(self.registered_types())}"
            )
        return definition

    def validate_config(
        self,
        provider_type: str,
        metadata: dict[str, Any],
        category: ProviderCategory,
    ) -> None:
        key = provider_type.strip().upper()
        definition = self.get_definition(key)
        if not definition.supports_category(category):
            raise ProviderConfigurationError(
                f"Adapter '{key}' does not support category '{category.value}'."
            )
        validator = self._config_validators.get(key)
        if validator is not None:
            validator(metadata, category)

    def capabilities_for(
        self,
        provider_type: str,
        category: ProviderCategory,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[ProviderCapability, ...]:
        key = provider_type.strip().upper()
        definition = self.get_definition(key)
        resolver = self._capability_resolvers.get(key)
        if resolver is not None:
            return resolver(metadata or {}, category)
        return definition.capabilities_for(category)

    def required_credentials_for(
        self,
        provider_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> set[str]:
        key = provider_type.strip().upper()
        resolver = self._credential_resolvers.get(key)
        if resolver is not None:
            return resolver(metadata or {})
        return set(self.get_definition(key).required_credential_keys())

    def register_singleton(self, provider_id_or_name: str, instance: BaseProviderClient) -> None:
        """Allows injecting pre-configured instances (for example failure-mode tests)."""
        self._singletons[provider_id_or_name] = instance

    def get_client(
        self,
        provider_type: str,
        provider_name: str,
        config: dict[str, Any] | None = None,
        provider_id: str | None = None,
    ) -> BaseProviderClient:
        if provider_id and provider_id in self._singletons:
            return self._singletons[provider_id]
        if provider_name in self._singletons:
            return self._singletons[provider_name]

        key = provider_type.strip().upper()
        definition = self.get_definition(key)
        if provider_id and definition.driver_family == "sandbox":
            cached = self._singletons.get(provider_id)
            if cached is not None:
                return cached
        factory = self._factories.get(key)
        if not factory:
            raise ProviderError(
                f"Unsupported provider_type '{provider_type}'. Registered: {list(self._factories.keys())}"
            )
        client = factory(provider_name, config or {})
        if provider_id and definition.driver_family == "sandbox":
            self._singletons[provider_id] = client
        return client


provider_registry = ProviderClientRegistry()
