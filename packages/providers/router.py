import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.providers.clients.base import BaseProviderClient
from packages.providers.clients.registry import ProviderClientRegistry, provider_registry
from packages.providers.exceptions import (
    ProviderError,
    ProviderProductUnavailableError,
)
from packages.providers.interface import (
    ProviderOrderRequest,
    ProviderOrderResponse,
)
from packages.providers.models import (
    Provider,
    ProviderHealthStatus,
    ProviderProductMapping,
)

logger = logging.getLogger("providers.router")


class ProviderRouter:
    """Selects eligible providers, evaluates health, and executes resilient failover routing."""

    def __init__(
        self,
        registry: ProviderClientRegistry | None = None,
        max_consecutive_failures: int = 3,
    ) -> None:
        self.registry = registry or provider_registry
        self.max_consecutive_failures = max_consecutive_failures

    async def get_eligible_mappings(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        variant_id: uuid.UUID | None = None,
    ) -> list[ProviderProductMapping]:
        """Queries and sorts eligible provider mappings for a given product/variant."""
        stmt = (
            select(ProviderProductMapping)
            .join(Provider, ProviderProductMapping.provider_id == Provider.id)
            .where(
                ProviderProductMapping.tenant_id == tenant_id,
                ProviderProductMapping.product_id == product_id,
                ProviderProductMapping.is_enabled.is_(True),
                Provider.is_enabled.is_(True),
            )
            .options(selectinload(ProviderProductMapping.provider))
        )

        if variant_id:
            # Prefer specific variant mapping, or fallback to product-level mapping
            stmt = stmt.where(
                (ProviderProductMapping.product_variant_id == variant_id)
                | (ProviderProductMapping.product_variant_id.is_(None))
            )

        result = await session.execute(stmt)
        mappings = list(result.scalars().all())

        # Filter out UNAVAILABLE providers or providers exceeding failure threshold
        eligible: list[ProviderProductMapping] = []
        for m in mappings:
            prov = m.provider
            if prov.health_status == ProviderHealthStatus.UNAVAILABLE:
                logger.warning(
                    "Skipping provider '%s' (id=%s): health status is UNAVAILABLE",
                    prov.name,
                    prov.id,
                )
                continue
            if prov.consecutive_failures >= self.max_consecutive_failures:
                logger.warning(
                    "Skipping provider '%s' (id=%s): consecutive failures (%d) >= threshold (%d)",
                    prov.name,
                    prov.id,
                    prov.consecutive_failures,
                    self.max_consecutive_failures,
                )
                continue
            eligible.append(m)

        # Sort: priority_override takes precedence, then provider.priority (1 is highest)
        eligible.sort(
            key=lambda m: (
                m.priority_override if m.priority_override is not None else m.provider.priority,
                m.cost_price,
            )
        )
        return eligible

    async def route_and_execute_order(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity: int,
        recipient: str,
        idempotency_key: str,
        variant_id: uuid.UUID | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> tuple[Provider, ProviderProductMapping, ProviderOrderResponse]:
        """Attempts order creation across eligible providers with automatic retryable fallback."""
        mappings = await self.get_eligible_mappings(
            session=session,
            tenant_id=tenant_id,
            product_id=product_id,
            variant_id=variant_id,
        )

        if not mappings:
            raise ProviderProductUnavailableError(
                f"No eligible providers available for product {product_id} in tenant {tenant_id}."
            )

        last_exception: Exception | None = None

        for mapping in mappings:
            provider_record = mapping.provider
            logger.info(
                "Attempting provider '%s' (type=%s, priority=%d) for product=%s [idempotency_key=%s]",
                provider_record.name,
                provider_record.provider_type,
                provider_record.priority,
                product_id,
                idempotency_key,
            )

            client: BaseProviderClient = self.registry.get_client(
                provider_type=provider_record.provider_type,
                provider_name=provider_record.name,
                config=provider_record.metadata_json,
                provider_id=str(provider_record.id),
            )

            request = ProviderOrderRequest(
                external_product_id=mapping.external_product_id,
                quantity=quantity,
                recipient=recipient,
                idempotency_key=idempotency_key,
                parameters=parameters or {},
            )

            try:
                response = await client.create_order(request)

                # Reset failure count on success
                if provider_record.consecutive_failures > 0:
                    provider_record.consecutive_failures = 0
                    provider_record.health_status = ProviderHealthStatus.HEALTHY
                    await session.flush()

                logger.info(
                    "Provider '%s' SUCCEEDED order creation (ext_id=%s, cost=%s)",
                    provider_record.name,
                    response.external_order_id,
                    response.cost,
                )
                return provider_record, mapping, response

            except ProviderError as p_err:
                last_exception = p_err
                provider_record.consecutive_failures += 1
                if provider_record.consecutive_failures >= self.max_consecutive_failures:
                    provider_record.health_status = ProviderHealthStatus.DEGRADED
                await session.flush()

                logger.warning(
                    "Provider '%s' FAILED: %s (retryable=%s, consecutive_failures=%d)",
                    provider_record.name,
                    p_err,
                    p_err.is_retryable,
                    provider_record.consecutive_failures,
                )

                # If the error is non-retryable (e.g. Auth failure or client error), abort failover immediately
                if not p_err.is_retryable:
                    logger.error(
                        "Halting failover: non-retryable provider error encountered on '%s'",
                        provider_record.name,
                    )
                    raise

                # Otherwise loop continues to next eligible provider in priority order

            except Exception as unhandled_err:
                logger.exception("Unexpected error executing provider '%s'", provider_record.name)
                raise ProviderError(f"Unexpected provider execution failure: {unhandled_err}") from unhandled_err

        # If all providers were exhausted
        raise ProviderError(
            f"All eligible providers exhausted for product {product_id}. Last error: {last_exception}"
        )
