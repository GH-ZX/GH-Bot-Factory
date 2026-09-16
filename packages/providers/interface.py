from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from packages.providers.contracts import (
    ProviderDeliveryArtifact,
    ProviderOrderState,
    normalize_provider_order_state,
)
from packages.providers.models import ProviderHealthStatus


@dataclass
class ProviderHealthResult:
    status: ProviderHealthStatus
    latency_ms: float = 0.0
    message: str = "Healthy"


@dataclass
class ProviderBalanceResult:
    balance: Decimal
    currency: str = "USD"


@dataclass
class ProviderProductDTO:
    external_id: str
    name: str
    cost: Decimal = Decimal("0.00")
    currency: str = "USD"
    is_available: bool = True
    min_quantity: int = 1
    max_quantity: int = 100000
    price: Decimal | None = None
    stock: int | None = None

    def __post_init__(self) -> None:
        if self.price is not None and self.cost == Decimal("0.00"):
            self.cost = self.price
        elif self.price is None:
            self.price = self.cost



@dataclass
class ProviderOrderRequest:
    external_product_id: str
    quantity: int
    recipient: str
    idempotency_key: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            import uuid

            self.idempotency_key = str(uuid.uuid4())



@dataclass
class ProviderOrderResponse:
    external_order_id: str
    status: str
    cost: Decimal = Decimal("0.00")
    is_success: bool = True
    raw_data: dict[str, Any] = field(default_factory=dict)
    total_cost: Decimal | None = None
    details: dict[str, Any] | None = None
    canonical_state: ProviderOrderState | None = None
    delivery: tuple[ProviderDeliveryArtifact, ...] = ()

    def __post_init__(self) -> None:
        if self.total_cost is not None and self.cost == Decimal("0.00"):
            self.cost = self.total_cost
        elif self.total_cost is None:
            self.total_cost = self.cost
        if self.details is not None and not self.raw_data:
            self.raw_data = self.details
        elif self.details is None:
            self.details = self.raw_data
        if self.canonical_state is None:
            self.canonical_state = normalize_provider_order_state(self.status)



@dataclass
class ProviderOrderCheckResponse:
    external_order_id: str
    status: str
    is_completed: bool
    is_failed: bool = False
    raw_data: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] | None = None
    canonical_state: ProviderOrderState | None = None
    delivery: tuple[ProviderDeliveryArtifact, ...] = ()

    def __post_init__(self) -> None:
        if self.details is not None and not self.raw_data:
            self.raw_data = self.details
        elif self.details is None:
            self.details = self.raw_data
        if self.canonical_state is None:
            self.canonical_state = normalize_provider_order_state(self.status)



# Backward-compatible aliases for Phase 2 test suite
ProviderProduct = ProviderProductDTO
ProviderBalance = ProviderBalanceResult
ProviderPurchaseRequest = ProviderOrderRequest
ProviderPurchaseResult = ProviderOrderResponse
ProviderOrderStatus = ProviderOrderCheckResponse


@runtime_checkable
class Provider(Protocol):
    """Protocol defining the interface that every upstream provider client must implement."""

    async def health_check(self) -> ProviderHealthResult:
        """Check availability and responsiveness of upstream API."""
        ...

    async def get_balance(self) -> ProviderBalanceResult:
        """Fetch current account balance from provider."""
        ...

    async def list_products(self) -> list[ProviderProductDTO]:
        """Fetch all available products from upstream provider."""
        ...

    async def get_product(self, external_id: str) -> ProviderProductDTO:
        """Fetch specific product details from upstream provider."""
        ...

    async def create_order(self, request: ProviderOrderRequest) -> ProviderOrderResponse:
        """Place an order with the upstream provider idempotently."""
        ...

    async def get_order(self, external_order_id: str) -> ProviderOrderCheckResponse:
        """Query status of an existing order with provider."""
        ...

    async def cancel_order(self, external_order_id: str) -> bool:
        """Attempt to cancel an unfulfilled upstream order where supported."""
        ...

    # Backward-compatible methods
    async def get_products(self) -> list[ProviderProductDTO]:
        ...

    async def purchase(self, request: ProviderOrderRequest) -> ProviderOrderResponse:
        ...
