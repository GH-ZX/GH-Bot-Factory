import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from packages.providers.clients.base import BaseProviderClient
from packages.providers.contracts import (
    NumberActivationSnapshot,
    NumberActivationState,
    NumberCountryDTO,
    NumberOfferDTO,
    NumberReservationRequest,
    NumberServiceDTO,
    SmsMessageDTO,
)
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
        self.number_activations: dict[str, NumberActivationSnapshot] = {}
        self.number_idempotency_map: dict[str, str] = {}

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
        target_id = self.idempotency_map.get(external_order_id, external_order_id)
        data = self.orders.get(target_id)
        if not data:
            return ProviderOrderCheckResponse(
                external_order_id=external_order_id,
                status="NOT_FOUND",
                is_completed=False,
                is_failed=True,
                raw_data={"error": "Order not found"},
            )

        is_completed = data["status"] == "COMPLETED"
        is_failed = data["status"] in ("FAILED", "CANCELLED", "REJECTED")
        return ProviderOrderCheckResponse(
            external_order_id=data.get("external_order_id", target_id),
            status=data["status"],
            is_completed=is_completed,
            is_failed=is_failed,
            raw_data=data,
        )


    async def list_number_services(self) -> list[NumberServiceDTO]:
        return [
            NumberServiceDTO(code="telegram", name="Telegram"),
            NumberServiceDTO(code="whatsapp", name="WhatsApp"),
            NumberServiceDTO(code="google", name="Google"),
        ]

    async def list_number_countries(self, service: str | None = None) -> list[NumberCountryDTO]:
        del service
        return [
            NumberCountryDTO(code="US", name="United States", dial_code="+1"),
            NumberCountryDTO(code="DE", name="Germany", dial_code="+49"),
            NumberCountryDTO(code="GB", name="United Kingdom", dial_code="+44"),
        ]

    async def list_number_offers(
        self,
        *,
        service: str,
        country: str,
    ) -> list[NumberOfferDTO]:
        normalized_service = service.strip().lower()
        normalized_country = country.strip().upper()
        services = {item.code for item in await self.list_number_services()}
        countries = {item.code for item in await self.list_number_countries(normalized_service)}
        if normalized_service not in services or normalized_country not in countries:
            return []
        base = Decimal("0.75") if normalized_country == "US" else Decimal("0.95")
        return [
            NumberOfferDTO(
                service=normalized_service,
                country=normalized_country,
                operator="any",
                cost=base,
                currency="USD",
                available_quantity=25,
                provider_offer_id=f"mock:{normalized_service}:{normalized_country}:any",
            )
        ]

    async def reserve_number(self, request: NumberReservationRequest) -> NumberActivationSnapshot:
        if request.idempotency_key in self.number_idempotency_map:
            existing_id = self.number_idempotency_map[request.idempotency_key]
            return self.number_activations[existing_id]
        offers = await self.list_number_offers(service=request.service, country=request.country)
        if not offers:
            raise ProviderProductUnavailableError("No mock number offer is available for this selection.")
        offer = offers[0]
        if request.max_price is not None and offer.cost > request.max_price:
            raise ProviderProductUnavailableError("Available mock number exceeds max_price.")
        external_order_id = f"num-{uuid.uuid4().hex[:10]}"
        suffix = int(uuid.UUID(int=uuid.uuid4().int).int % 10_000_000)
        country = request.country.strip().upper()
        dial = {"US": "+1", "DE": "+49", "GB": "+44"}.get(country, "+999")
        phone_number = f"{dial}{suffix:07d}"
        snapshot = NumberActivationSnapshot(
            external_order_id=external_order_id,
            state=NumberActivationState.WAITING_SMS,
            phone_number=phone_number,
            cost=offer.cost,
            currency=offer.currency,
            expires_at=datetime.now(UTC) + timedelta(minutes=20),
            raw_data={
                "sandbox": True,
                "service": request.service.strip().lower(),
                "country": country,
                "operator": request.operator or "any",
            },
        )
        self.number_activations[external_order_id] = snapshot
        self.number_idempotency_map[request.idempotency_key] = external_order_id
        return snapshot

    async def get_number_activation(self, external_order_id: str) -> NumberActivationSnapshot:
        snapshot = self.number_activations.get(external_order_id)
        if snapshot is None:
            return NumberActivationSnapshot(
                external_order_id=external_order_id,
                state=NumberActivationState.FAILED,
                raw_data={"error": "Activation not found"},
            )
        return snapshot

    async def cancel_number_activation(self, external_order_id: str) -> NumberActivationSnapshot:
        current = await self.get_number_activation(external_order_id)
        if current.state in {NumberActivationState.COMPLETED, NumberActivationState.CANCELLED}:
            return current
        updated = NumberActivationSnapshot(
            external_order_id=current.external_order_id,
            state=NumberActivationState.CANCELLED,
            phone_number=current.phone_number,
            cost=current.cost,
            currency=current.currency,
            expires_at=current.expires_at,
            messages=current.messages,
            raw_data={**current.raw_data, "cancelled": True},
        )
        self.number_activations[external_order_id] = updated
        return updated

    async def finish_number_activation(self, external_order_id: str) -> NumberActivationSnapshot:
        current = await self.get_number_activation(external_order_id)
        if current.state in {NumberActivationState.CANCELLED, NumberActivationState.FAILED}:
            return current
        messages = current.messages or (
            SmsMessageDTO(
                code="123456",
                text="Your verification code is 123456",
                sender="MockService",
                received_at=datetime.now(UTC),
                metadata={"sandbox": True},
            ),
        )
        updated = NumberActivationSnapshot(
            external_order_id=current.external_order_id,
            state=NumberActivationState.COMPLETED,
            phone_number=current.phone_number,
            cost=current.cost,
            currency=current.currency,
            expires_at=current.expires_at,
            messages=messages,
            raw_data={**current.raw_data, "completed": True},
        )
        self.number_activations[external_order_id] = updated
        return updated
