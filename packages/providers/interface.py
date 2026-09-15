from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@dataclass
class ProviderProduct:
    external_id: str
    name: str
    price: Decimal
    currency: str = "USD"
    stock: int | None = None
    min_quantity: int = 1
    max_quantity: int = 100000


@dataclass
class ProviderBalance:
    balance: Decimal
    currency: str = "USD"


@dataclass
class ProviderPurchaseRequest:
    external_product_id: str
    quantity: int
    recipient: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderPurchaseResult:
    external_order_id: str
    status: str
    total_cost: Decimal
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderOrderStatus:
    external_order_id: str
    status: str
    is_completed: bool
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Provider(Protocol):
    """Protocol defining the interface that every upstream provider must implement."""

    async def get_products(self) -> list[ProviderProduct]:
        """Fetch available products and pricing from provider."""
        ...

    async def get_balance(self) -> ProviderBalance:
        """Fetch current account balance from provider."""
        ...

    async def purchase(self, request: ProviderPurchaseRequest) -> ProviderPurchaseResult:
        """Place an order/purchase with provider."""
        ...

    async def get_order(self, external_order_id: str) -> ProviderOrderStatus:
        """Query status of an existing order with provider."""
        ...
