import hashlib
import uuid
from decimal import Decimal
from typing import Any

from packages.providers.clients.base import BaseProviderClient
from packages.providers.exceptions import ProviderProductUnavailableError
from packages.providers.interface import (
    ProviderBalanceResult,
    ProviderHealthResult,
    ProviderOrderCheckResponse,
    ProviderOrderRequest,
    ProviderOrderResponse,
    ProviderProductDTO,
)
from packages.providers.models import ProviderHealthStatus


class ExampleDigitalCodesProvider(BaseProviderClient):
    """Deterministic example digital codes vendor client.
    
    Generates deterministic activation keys without calling external networks.
    """

    def __init__(
        self,
        provider_name: str = "DigitalVoucherCorp",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(provider_name=provider_name, config=config)
        self.dispatched_orders: dict[str, dict[str, Any]] = {}
        self.idempotency_cache: dict[str, str] = {}

    async def health_check(self) -> ProviderHealthResult:
        return ProviderHealthResult(
            status=ProviderHealthStatus.HEALTHY,
            latency_ms=25.0,
            message="Digital Voucher API reachable",
        )

    async def get_balance(self) -> ProviderBalanceResult:
        return ProviderBalanceResult(balance=Decimal("5000.00"), currency="USD")

    async def list_products(self) -> list[ProviderProductDTO]:
        return [
            ProviderProductDTO(
                external_id="code-steam-10usd",
                name="Steam Gift Card 10 USD",
                cost=Decimal("9.20"),
                currency="USD",
                is_available=True,
            ),
            ProviderProductDTO(
                external_id="code-playstation-25usd",
                name="PlayStation Network 25 USD",
                cost=Decimal("23.50"),
                currency="USD",
                is_available=True,
            ),
        ]

    async def get_product(self, external_id: str) -> ProviderProductDTO:
        for item in await self.list_products():
            if item.external_id == external_id:
                return item
        raise ProviderProductUnavailableError(f"Digital voucher '{external_id}' not available.")

    async def create_order(self, request: ProviderOrderRequest) -> ProviderOrderResponse:
        # Idempotency check
        if request.idempotency_key in self.idempotency_cache:
            ext_id = self.idempotency_cache[request.idempotency_key]
            return ProviderOrderResponse(
                external_order_id=ext_id,
                status="COMPLETED",
                cost=self.dispatched_orders[ext_id]["cost"],
                is_success=True,
                raw_data={"idempotent_replay": True, **self.dispatched_orders[ext_id]},
            )

        product = await self.get_product(request.external_product_id)
        total_cost = product.cost * request.quantity

        ext_order_id = f"VOUCHER-{uuid.uuid4().hex[:8].upper()}"

        # Deterministic voucher code based on order key
        hash_seed = hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:16].upper()
        generated_code = f"CODE-{hash_seed[:4]}-{hash_seed[4:8]}-{hash_seed[8:12]}"

        order_data = {
            "external_order_id": ext_order_id,
            "status": "COMPLETED",
            "cost": total_cost,
            "voucher_code": generated_code,
            "recipient": request.recipient,
            "quantity": request.quantity,
        }

        self.dispatched_orders[ext_order_id] = order_data
        self.idempotency_cache[request.idempotency_key] = ext_order_id

        return ProviderOrderResponse(
            external_order_id=ext_order_id,
            status="COMPLETED",
            cost=total_cost,
            is_success=True,
            raw_data=order_data,
        )

    async def get_order(self, external_order_id: str) -> ProviderOrderCheckResponse:
        data = self.dispatched_orders.get(external_order_id)
        if not data:
            return ProviderOrderCheckResponse(
                external_order_id=external_order_id,
                status="NOT_FOUND",
                is_completed=False,
                is_failed=True,
                raw_data={"error": "Unknown external voucher order"},
            )

        return ProviderOrderCheckResponse(
            external_order_id=external_order_id,
            status="COMPLETED",
            is_completed=True,
            is_failed=False,
            raw_data=data,
        )
