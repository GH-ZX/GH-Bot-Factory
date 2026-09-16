import asyncio
import hashlib
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.providers.clients.base import BaseProviderClient
from packages.providers.clients.registry import ProviderClientRegistry, provider_registry
from packages.providers.exceptions import (
    ProviderConfigurationError,
    ProviderError,
    ProviderProductUnavailableError,
    ProviderTimeoutError,
)
from packages.providers.interface import (
    ProviderOrderRequest,
    ProviderOrderResponse,
)
from packages.providers.models import (
    Provider,
    ProviderCategory,
    ProviderHealthStatus,
    ProviderOfferSnapshot,
    ProviderProductMapping,
    ProviderRoutingPolicy,
    ProviderRoutingStrategy,
)
from packages.telegram.secrets import SecretNotFoundError, SecretStorage, get_default_secret_storage

logger = logging.getLogger("providers.router")


@dataclass(frozen=True, slots=True)
class RoutingDirective:
    strategy: ProviderRoutingStrategy
    preferred_provider_id: uuid.UUID | None = None
    failover_enabled: bool = True
    weights_json: dict[str, int] | None = None


class ProviderRouter:
    """Selects eligible providers, evaluates health, and executes resilient failover routing."""

    def __init__(
        self,
        registry: ProviderClientRegistry | None = None,
        secret_storage: SecretStorage | None = None,
        max_consecutive_failures: int = 3,
    ) -> None:
        self.registry = registry or provider_registry
        self.secret_storage = secret_storage or get_default_secret_storage()
        self.max_consecutive_failures = max_consecutive_failures

    async def get_eligible_mappings(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        variant_id: uuid.UUID | None = None,
        routing_key: str | None = None,
        allowed_provider_ids: set[uuid.UUID] | None = None,
        allowed_categories: set[ProviderCategory] | None = None,
        strategy_override: ProviderRoutingStrategy | None = None,
        preferred_provider_id: uuid.UUID | None = None,
        failover_enabled: bool | None = None,
    ) -> list[ProviderProductMapping]:
        """Return eligible mappings in deterministic policy order.

        Variant-scoped policies override product-level policies. Without a policy this method
        preserves the legacy priority-then-cost order.
        """
        stmt = (
            select(ProviderProductMapping)
            .join(Provider, ProviderProductMapping.provider_id == Provider.id)
            .where(
                ProviderProductMapping.tenant_id == tenant_id,
                ProviderProductMapping.product_id == product_id,
                ProviderProductMapping.is_enabled.is_(True),
                Provider.is_enabled.is_(True),
            )
            .options(
                selectinload(ProviderProductMapping.provider).selectinload(Provider.credentials)
            )
        )

        if allowed_provider_ids:
            stmt = stmt.where(Provider.id.in_(allowed_provider_ids))
        if allowed_categories:
            stmt = stmt.where(Provider.category.in_(allowed_categories))

        if variant_id:
            stmt = stmt.where(
                (ProviderProductMapping.product_variant_id == variant_id)
                | (ProviderProductMapping.product_variant_id.is_(None))
            )

        result = await session.execute(stmt)
        mappings = list(result.scalars().all())

        eligible: list[ProviderProductMapping] = []
        for mapping in mappings:
            provider = mapping.provider
            if provider.health_status == ProviderHealthStatus.UNAVAILABLE:
                logger.warning(
                    "Skipping provider '%s' (id=%s): health status is UNAVAILABLE",
                    provider.name,
                    provider.id,
                )
                continue
            if provider.consecutive_failures >= self.max_consecutive_failures:
                logger.warning(
                    "Skipping provider '%s' (id=%s): consecutive failures (%d) >= threshold (%d)",
                    provider.name,
                    provider.id,
                    provider.consecutive_failures,
                    self.max_consecutive_failures,
                )
                continue
            eligible.append(mapping)

        offer_snapshots: dict[uuid.UUID, ProviderOfferSnapshot] = {}
        if eligible:
            rows = list(
                (
                    await session.execute(
                        select(ProviderOfferSnapshot).where(
                            ProviderOfferSnapshot.tenant_id == tenant_id,
                            ProviderOfferSnapshot.mapping_id.in_([item.id for item in eligible]),
                        )
                    )
                ).scalars().all()
            )
            offer_snapshots = {row.mapping_id: row for row in rows}
            eligible = [
                mapping
                for mapping in eligible
                if not (
                    self._snapshot_is_fresh(offer_snapshots.get(mapping.id))
                    and offer_snapshots[mapping.id].is_available is False
                )
            ]

        policy: ProviderRoutingPolicy | RoutingDirective | None = await self._routing_policy(
            session, tenant_id=tenant_id, product_id=product_id, variant_id=variant_id
        )
        if strategy_override is not None:
            policy = RoutingDirective(
                strategy=strategy_override,
                preferred_provider_id=preferred_provider_id,
                failover_enabled=True if failover_enabled is None else failover_enabled,
                weights_json={},
            )
        ordered = self._order_mappings(
            eligible,
            policy=policy,
            routing_key=routing_key
            or f"preview:{tenant_id}:{product_id}:{variant_id or 'default'}",
            offer_snapshots=offer_snapshots,
        )
        if policy is not None and not policy.failover_enabled and ordered:
            return ordered[:1]
        return ordered

    async def _routing_policy(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        variant_id: uuid.UUID | None,
    ) -> ProviderRoutingPolicy | None:
        if variant_id is not None:
            variant_policy = await session.scalar(
                select(ProviderRoutingPolicy).where(
                    ProviderRoutingPolicy.tenant_id == tenant_id,
                    ProviderRoutingPolicy.product_id == product_id,
                    ProviderRoutingPolicy.product_variant_id == variant_id,
                )
            )
            if variant_policy is not None:
                return variant_policy
        return await session.scalar(
            select(ProviderRoutingPolicy).where(
                ProviderRoutingPolicy.tenant_id == tenant_id,
                ProviderRoutingPolicy.product_id == product_id,
                ProviderRoutingPolicy.product_variant_id.is_(None),
            )
        )

    @staticmethod
    def _snapshot_is_fresh(snapshot: ProviderOfferSnapshot | None) -> bool:
        if snapshot is None:
            return False
        expires_at = snapshot.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at > datetime.now(UTC)

    def _effective_cost(
        self,
        mapping: ProviderProductMapping,
        snapshots: dict[uuid.UUID, ProviderOfferSnapshot],
    ) -> tuple[Any, str]:
        snapshot = snapshots.get(mapping.id)
        if self._snapshot_is_fresh(snapshot):
            assert snapshot is not None
            return snapshot.cost_amount, snapshot.cost_currency.upper()
        return mapping.cost_price, mapping.cost_currency.upper()

    @staticmethod
    def _health_rank(provider: Provider) -> int:
        return {
            ProviderHealthStatus.HEALTHY: 0,
            ProviderHealthStatus.UNKNOWN: 1,
            ProviderHealthStatus.DEGRADED: 2,
            ProviderHealthStatus.UNAVAILABLE: 3,
        }[provider.health_status]

    @staticmethod
    def _weighted_score(
        mapping: ProviderProductMapping, *, routing_key: str, weight: int
    ) -> float:
        seed = (
            f"{routing_key}:{mapping.provider_id}:{mapping.external_product_id}"
        ).encode()
        digest = hashlib.sha256(seed).digest()
        integer = int.from_bytes(digest[:8], "big")
        unit = (integer + 1) / ((1 << 64) + 1)
        return -math.log(unit) / max(weight, 1)

    def _order_mappings(
        self,
        mappings: list[ProviderProductMapping],
        *,
        policy: ProviderRoutingPolicy | RoutingDirective | None,
        routing_key: str,
        offer_snapshots: dict[uuid.UUID, ProviderOfferSnapshot] | None = None,
    ) -> list[ProviderProductMapping]:
        ordered = list(mappings)
        snapshots = offer_snapshots or {}
        currencies = {self._effective_cost(item, snapshots)[1] for item in ordered}
        comparable_costs = len(currencies) <= 1
        if policy is None or policy.strategy == ProviderRoutingStrategy.PRIORITY:
            ordered.sort(
                key=lambda mapping: (
                    mapping.priority_override
                    if mapping.priority_override is not None
                    else mapping.provider.priority,
                    self._effective_cost(mapping, snapshots)[0] if comparable_costs else 0,
                    str(mapping.provider_id),
                )
            )
            return ordered

        if policy.strategy == ProviderRoutingStrategy.LOWEST_COST:
            if not comparable_costs:
                raise ProviderConfigurationError(
                    "LOWEST_COST routing requires one provider cost currency until FX normalization is configured."
                )
            ordered.sort(
                key=lambda mapping: (
                    self._effective_cost(mapping, snapshots)[0],
                    mapping.priority_override
                    if mapping.priority_override is not None
                    else mapping.provider.priority,
                    str(mapping.provider_id),
                )
            )
            return ordered

        if policy.strategy == ProviderRoutingStrategy.AVAILABILITY:
            ordered.sort(
                key=lambda mapping: (
                    self._health_rank(mapping.provider),
                    mapping.provider.consecutive_failures,
                    mapping.priority_override
                    if mapping.priority_override is not None
                    else mapping.provider.priority,
                    str(mapping.provider_id),
                )
            )
            return ordered

        if policy.strategy == ProviderRoutingStrategy.HEALTHIEST:
            ordered.sort(
                key=lambda mapping: (
                    self._health_rank(mapping.provider),
                    mapping.provider.consecutive_failures,
                    mapping.provider.last_health_latency_ms
                    if mapping.provider.last_health_latency_ms is not None
                    else float("inf"),
                    self._effective_cost(mapping, snapshots)[0] if comparable_costs else 0,
                    str(mapping.provider_id),
                )
            )
            return ordered

        if policy.strategy == ProviderRoutingStrategy.MANUAL:
            preferred = policy.preferred_provider_id
            if preferred is None:
                return []
            preferred_rows = [mapping for mapping in ordered if mapping.provider_id == preferred]
            others = [mapping for mapping in ordered if mapping.provider_id != preferred]
            others.sort(
                key=lambda mapping: (
                    mapping.priority_override
                    if mapping.priority_override is not None
                    else mapping.provider.priority,
                    self._effective_cost(mapping, snapshots)[0] if comparable_costs else 0,
                    str(mapping.provider_id),
                )
            )
            return preferred_rows + others if preferred_rows else []

        if policy.strategy == ProviderRoutingStrategy.WEIGHTED:
            raw_weights = policy.weights_json or {}

            def weight_for(mapping: ProviderProductMapping) -> int:
                raw = raw_weights.get(str(mapping.provider_id), 1)
                try:
                    value = int(raw)
                except (TypeError, ValueError):
                    value = 1
                return max(1, min(value, 1000))

            ordered.sort(
                key=lambda mapping: (
                    self._weighted_score(
                        mapping,
                        routing_key=routing_key,
                        weight=weight_for(mapping),
                    ),
                    str(mapping.provider_id),
                )
            )
            return ordered

        return ordered

    async def build_provider_config(self, provider_record: Provider) -> dict[str, Any]:
        """Resolve credential references into an ephemeral runtime config.

        Secret values are never written back to database models or logs. Adapters can
        read them from config["credentials"] keyed by credential_type.
        """
        definition = self.registry.get_definition(provider_record.provider_type)
        self.registry.validate_config(
            provider_record.provider_type,
            provider_record.metadata_json or {},
            provider_record.category,
        )
        configured_types = {credential.credential_type.upper() for credential in provider_record.credentials}
        required = self.registry.required_credentials_for(
            provider_record.provider_type, provider_record.metadata_json or {}
        )
        missing = [key for key in required if key not in configured_types]
        if missing:
            raise ProviderConfigurationError(
                f"Missing required provider credentials: {', '.join(sorted(missing))}."
            )

        config = dict(provider_record.metadata_json or {})
        credentials: dict[str, str] = {}
        for credential in provider_record.credentials:
            try:
                credentials[credential.credential_type] = await self.secret_storage.get_secret(
                    credential.secret_ref
                )
            except SecretNotFoundError as exc:
                from packages.providers.exceptions import ProviderAuthenticationError

                raise ProviderAuthenticationError(
                    f"Credential reference for provider '{provider_record.name}' is not available."
                ) from exc
        config["credentials"] = credentials
        config["provider_category"] = provider_record.category.value
        config["adapter_key"] = definition.normalized_key
        config["capabilities"] = [
            capability.value
            for capability in self.registry.capabilities_for(
                provider_record.provider_type,
                provider_record.category,
                provider_record.metadata_json or {},
            )
        ]
        return config

    @staticmethod
    def _provider_timeout(provider_record: Provider) -> float:
        raw = (provider_record.metadata_json or {}).get("timeout_seconds", 15)
        try:
            timeout = float(raw)
        except (TypeError, ValueError):
            timeout = 15.0
        return max(1.0, min(timeout, 60.0))

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
        allowed_provider_ids: set[uuid.UUID] | None = None,
        allowed_categories: set[ProviderCategory] | None = None,
        strategy_override: ProviderRoutingStrategy | None = None,
        preferred_provider_id: uuid.UUID | None = None,
        failover_enabled: bool | None = None,
    ) -> tuple[Provider, ProviderProductMapping, ProviderOrderResponse]:
        """Attempts order creation across eligible providers with automatic retryable fallback."""
        mappings = await self.get_eligible_mappings(
            session=session,
            tenant_id=tenant_id,
            product_id=product_id,
            variant_id=variant_id,
            routing_key=idempotency_key,
            allowed_provider_ids=allowed_provider_ids,
            allowed_categories=allowed_categories,
            strategy_override=strategy_override,
            preferred_provider_id=preferred_provider_id,
            failover_enabled=failover_enabled,
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
                config=await self.build_provider_config(provider_record),
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
                timeout_seconds = self._provider_timeout(provider_record)
                try:
                    async with asyncio.timeout(timeout_seconds):
                        response = await client.create_order(request)
                except TimeoutError as exc:
                    raise ProviderTimeoutError(
                        f"Provider '{provider_record.name}' timed out during order creation."
                    ) from exc

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

                # Fail over only when the adapter can prove the failure happened before work
                # could have been accepted upstream. Timeouts/network ambiguity must converge
                # through reconciliation instead of risking a duplicate purchase at another provider.
                if not p_err.is_retryable or not p_err.safe_to_failover:
                    logger.error(
                        "Halting failover on provider '%s' (retryable=%s, safe_to_failover=%s)",
                        provider_record.name,
                        p_err.is_retryable,
                        p_err.safe_to_failover,
                    )
                    raise

                # Safe pre-order failure: continue to the next deterministic eligible provider.

            except Exception as unhandled_err:
                logger.exception("Unexpected error executing provider '%s'", provider_record.name)
                raise ProviderError(f"Unexpected provider execution failure: {unhandled_err}") from unhandled_err

        # If all providers were exhausted
        is_retry = getattr(last_exception, "is_retryable", False) if last_exception else False
        err = ProviderError(
            f"All eligible providers exhausted for product {product_id}. Last error: {last_exception}"
        )
        err.is_retryable = is_retry
        raise err from last_exception
