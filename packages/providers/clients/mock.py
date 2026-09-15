import uuid
from decimal import Decimal
from typing import Any

from packages.providers.clients.base import BaseProviderClient
from packages.providers.exceptions import (
    ProviderAuthenticationError,
    ProviderInsufficientBalanceError,
    ProviderProductUnavailableError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from packages.providers.interface import (
    ProviderBalanceResult,
    ProviderHealthResult,
    ProviderOrderCheckResponse,
    ProviderOrderRequest,
    ProviderOrderResponse,
    ProviderProductDTO,
)
from packages.providers.models import ProviderHealthStatus


class MockProvider(BaseProviderClient):
    """Production-grade mock provider with full idempotency support and configurable failure modes for testing."""

    def __init__(
        self,
        provider_name: str = "MockProvider",
        balance: Decimal = Decimal("1000.00"),
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(provider_name=provider_name, config=config)
        self._balance = balance
        self.orders: dict[str, dict[str, Any]] = {}
        self.idempotency_map: dict[str, str] = {}  # idempotency_key -> external_order_id

        # Configurable failure injections
        self.fail_with_timeout = False
        self.fail_with_rate_limit = False
        self.fail_with_insufficient_balance = False
        self.fail_with_auth_error = False
        self.fail_with_unavailable_product = False
        self.health_override: ProviderHealthStatus | None = None

    async def health_check(self) -> ProviderHealthResult:
        status = self.health_override or ProviderHealthStatus.HEALTHY
        return ProviderHealthResult(
            status=status,
            latency_ms=12.5,
            message="Mock Provider operational",
        )

    async def get_balance(self) -> ProviderBalanceResult:
        return ProviderBalanceResult(balance=self._balance, currency="USD")

    async def list_products(self) -> list[ProviderProductDTO]:
        return [
            ProviderProductDTO(
                external_id="mock-prod-stars-50",
                name="Telegram Stars (50 Stars)",
                cost=Decimal("1.25"),
                currency="USD",
                is_available=True,
            ),
            ProviderProductDTO(
                external_id="mock-prod-premium-1m",
                name="Telegram Premium (1 Month)",
                cost=Decimal("3.99"),
                currency="USD",
                is_available=True,
            ),
        ]

    async def get_product(self, external_id: str) -> ProviderProductDTO:
        products = await self.list_products()
        for p in products:
            if p.external_id == external_id:
                return p
        raise ProviderProductUnavailableError(f"External product '{external_id}' not found.")

    async def create_order(self, request: ProviderOrderRequest) -> ProviderOrderResponse:
        # 1. Evaluate failure injections
        if self.fail_with_auth_error:
            raise ProviderAuthenticationError("Invalid or expired API token for mock provider.")
        if self.fail_with_timeout:
            raise ProviderTimeoutError("Mock provider connection timed out after 10000ms.")
        if self.fail_with_rate_limit:
            raise ProviderRateLimitError("Rate limit exceeded: 429 Too Many Requests.")
        if self.fail_with_insufficient_balance:
            raise ProviderInsufficientBalanceError("Upstream account balance insufficient.")
        if self.fail_with_unavailable_product:
            raise ProviderProductUnavailableError(f"Product '{request.external_product_id}' is temporarily out of stock.")

        # 2. Strict Idempotency Check: Return existing external order if key was already executed
        if request.idempotency_key in self.idempotency_map:
            existing_order_id = self.idempotency_map[request.idempotency_key]
            existing_data = self.orders[existing_order_id]
            return ProviderOrderResponse(
                external_order_id=existing_order_id,
                status=existing_data["status"],
                cost=existing_data["cost"],
                is_success=True,
                raw_data={"idempotent_replay": True, **existing_data},
            )

        # 3. Simulate cost and placement
        unit_cost = Decimal("1.25")
        if "premium" in request.external_product_id:
            unit_cost = Decimal("3.99")
        total_cost = unit_cost * request.quantity

        if self._balance < total_cost:
            raise ProviderInsufficientBalanceError("Mock provider account balance is depleted.")

        self._balance -= total_cost
        ext_order_id = f"ext-{uuid.uuid4().hex[:10]}"

        order_record = {
            "external_order_id": ext_order_id,
            "status": "COMPLETED",
            "cost": str(total_cost),
            "recipient": request.recipient,
            "quantity": request.quantity,
            "idempotency_key": request.idempotency_key,
        }
        self.orders[ext_order_id] = order_record
        self.idempotency_map[request.idempotency_key] = ext_order_id

        return ProviderOrderResponse(
            external_order_id=ext_order_id,
            status="COMPLETED",
            cost=total_cost,
            is_success=True,
            raw_data=order_record,
        )

    async def get_order(self, external_order_id: str) -> ProviderOrderCheckResponse:
        data = self.orders.get(external_order_id)
        if not data:
            return ProviderOrderCheckResponse(
                external_order_id=external_order_id,
                status="NOT_FOUND",
                is_completed=False,
                is_failed=True,
                raw_data={"error": "Order not found"},
            )

        is_completed = data["status"] == "COMPLETED"
        return ProviderOrderCheckResponse(
            external_order_id=external_order_id,
            status=data["status"],
            is_completed=is_completed,
            is_failed=False,
            raw_data=data,
        )
