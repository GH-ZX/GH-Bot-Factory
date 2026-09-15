from abc import ABC, abstractmethod
from typing import Any

from packages.providers.interface import (
    Provider,
    ProviderBalanceResult,
    ProviderHealthResult,
    ProviderOrderCheckResponse,
    ProviderOrderRequest,
    ProviderOrderResponse,
    ProviderProductDTO,
)


class BaseProviderClient(Provider, ABC):
    """Abstract base class for external provider API integration clients."""

    def __init__(self, provider_name: str, config: dict[str, Any] | None = None) -> None:
        self.provider_name = provider_name
        self.config = config or {}

    @abstractmethod
    async def health_check(self) -> ProviderHealthResult: ...

    @abstractmethod
    async def get_balance(self) -> ProviderBalanceResult: ...

    @abstractmethod
    async def list_products(self) -> list[ProviderProductDTO]: ...

    @abstractmethod
    async def get_product(self, external_id: str) -> ProviderProductDTO: ...

    @abstractmethod
    async def create_order(self, request: ProviderOrderRequest) -> ProviderOrderResponse: ...

    @abstractmethod
    async def get_order(self, external_order_id: str) -> ProviderOrderCheckResponse: ...

    async def cancel_order(self, external_order_id: str) -> bool:
        """Default no-op for providers that do not support upstream cancellation."""
        return False

    # Backward-compatible protocol methods
    async def get_products(self) -> list[ProviderProductDTO]:
        return await self.list_products()

    async def purchase(self, request: ProviderOrderRequest) -> ProviderOrderResponse:
        return await self.create_order(request)
