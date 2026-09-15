import uuid
from decimal import Decimal

from packages.providers.interface import (
    Provider,
    ProviderBalance,
    ProviderOrderStatus,
    ProviderProduct,
    ProviderPurchaseRequest,
    ProviderPurchaseResult,
)


class MockProvider(Provider):
    """In-memory mock provider implementing the Provider Protocol for testing and fallback."""

    def __init__(self, name: str = "MockProvider", balance: Decimal = Decimal("500.00")):
        self.name = name
        self._balance = balance
        self.orders: dict[str, dict] = {}

    async def get_products(self) -> list[ProviderProduct]:
        return [
            ProviderProduct(
                external_id="prod-101",
                name="Telegram Stars 50",
                price=Decimal("1.25"),
                currency="USD",
                stock=1000,
            ),
            ProviderProduct(
                external_id="prod-102",
                name="Telegram Premium 1 Month",
                price=Decimal("3.99"),
                currency="USD",
                stock=500,
            ),
        ]

    async def get_balance(self) -> ProviderBalance:
        return ProviderBalance(balance=self._balance, currency="USD")

    async def purchase(self, request: ProviderPurchaseRequest) -> ProviderPurchaseResult:
        order_id = f"mock-order-{uuid.uuid4().hex[:8]}"
        total_cost = Decimal("1.25") * request.quantity
        self._balance -= total_cost
        self.orders[order_id] = {
            "status": "COMPLETED",
            "quantity": request.quantity,
            "recipient": request.recipient,
            "cost": total_cost,
        }
        return ProviderPurchaseResult(
            external_order_id=order_id,
            status="COMPLETED",
            total_cost=total_cost,
            details={"provider": self.name, "recipient": request.recipient},
        )

    async def get_order(self, external_order_id: str) -> ProviderOrderStatus:
        order_data = self.orders.get(external_order_id)
        if not order_data:
            return ProviderOrderStatus(
                external_order_id=external_order_id,
                status="NOT_FOUND",
                is_completed=False,
                details={"error": "Order not found"},
            )
        return ProviderOrderStatus(
            external_order_id=external_order_id,
            status=order_data["status"],
            is_completed=order_data["status"] == "COMPLETED",
            details=order_data,
        )
