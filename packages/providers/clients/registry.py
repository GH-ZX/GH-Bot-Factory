from collections.abc import Callable
from typing import Any

from packages.providers.clients.base import BaseProviderClient
from packages.providers.clients.example_digital import ExampleDigitalCodesProvider
from packages.providers.clients.mock import MockProvider
from packages.providers.exceptions import ProviderError

ProviderFactory = Callable[[str, dict[str, Any]], BaseProviderClient]


class ProviderClientRegistry:
    """Dependency injection registry for upstream vendor client instances."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}
        self._singletons: dict[str, BaseProviderClient] = {}

        # Register default internal client implementations
        self.register_type("MOCK", lambda name, cfg: MockProvider(provider_name=name, config=cfg))
        self.register_type(
            "DIGITAL_CODES",
            lambda name, cfg: ExampleDigitalCodesProvider(provider_name=name, config=cfg),
        )

    def register_type(self, provider_type: str, factory: ProviderFactory) -> None:
        self._factories[provider_type.upper()] = factory

    def registered_types(self) -> tuple[str, ...]:
        """Return the supported provider adapter types without exposing registry internals."""
        return tuple(sorted(self._factories))

    def register_singleton(self, provider_id_or_name: str, instance: BaseProviderClient) -> None:
        """Allows injecting pre-configured instances (e.g. for testing specific failure modes)."""
        self._singletons[provider_id_or_name] = instance

    def get_client(
        self,
        provider_type: str,
        provider_name: str,
        config: dict[str, Any] | None = None,
        provider_id: str | None = None,
    ) -> BaseProviderClient:
        # Check singleton override first
        if provider_id and provider_id in self._singletons:
            return self._singletons[provider_id]
        if provider_name in self._singletons:
            return self._singletons[provider_name]

        factory = self._factories.get(provider_type.upper())
        if not factory:
            raise ProviderError(f"Unsupported provider_type '{provider_type}'. Registered: {list(self._factories.keys())}")

        return factory(provider_name, config or {})


provider_registry = ProviderClientRegistry()
