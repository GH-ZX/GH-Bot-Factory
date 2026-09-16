from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.core.config import settings
from packages.providers.catalog import ProviderCapability
from packages.providers.clients.registry import ProviderClientRegistry, provider_registry
from packages.providers.exceptions import ProviderError, ProviderProductUnavailableError
from packages.providers.interface import ProviderProductDTO
from packages.providers.models import (
    Provider,
    ProviderOfferSnapshot,
    ProviderProductMapping,
)
from packages.providers.router import ProviderRouter
from packages.telegram.secrets import SecretStorage


@dataclass(frozen=True, slots=True)
class OfferRefreshFailure:
    mapping_id: uuid.UUID
    provider_id: uuid.UUID
    error_code: str


@dataclass(frozen=True, slots=True)
class OfferRefreshResult:
    refreshed: int
    unavailable: int
    failed: int
    failures: tuple[OfferRefreshFailure, ...] = ()


class ProviderOfferAggregator:
    """Maintains the latest normalized provider offer observation per product mapping.

    Provider observations are advisory read-side data. They never create upstream orders and they
    never convert currencies. A stale observation must not be treated as current availability.
    """

    def __init__(
        self,
        *,
        registry: ProviderClientRegistry | None = None,
        secret_storage: SecretStorage | None = None,
        freshness_seconds: int | None = None,
    ) -> None:
        self.registry = registry or provider_registry
        self.router = ProviderRouter(registry=self.registry, secret_storage=secret_storage)
        configured_freshness = (
            settings.provider_offer_freshness_seconds
            if freshness_seconds is None
            else freshness_seconds
        )
        self.freshness_seconds = max(30, min(int(configured_freshness), 86_400))

    async def list_offers(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        variant_id: uuid.UUID | None = None,
    ) -> list[ProviderOfferSnapshot]:
        stmt = select(ProviderOfferSnapshot).where(
            ProviderOfferSnapshot.tenant_id == tenant_id,
            ProviderOfferSnapshot.product_id == product_id,
        )
        if variant_id is not None:
            stmt = stmt.where(
                (ProviderOfferSnapshot.product_variant_id == variant_id)
                | (ProviderOfferSnapshot.product_variant_id.is_(None))
            )
        return list(
            (
                await session.execute(
                    stmt.order_by(
                        ProviderOfferSnapshot.cost_currency.asc(),
                        ProviderOfferSnapshot.cost_amount.asc(),
                        ProviderOfferSnapshot.provider_id.asc(),
                    )
                )
            ).scalars().all()
        )

    async def refresh_product(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        variant_id: uuid.UUID | None = None,
    ) -> OfferRefreshResult:
        mappings = await self._load_mappings(
            session,
            tenant_id=tenant_id,
            product_id=product_id,
            variant_id=variant_id,
        )
        grouped: dict[uuid.UUID, list[ProviderProductMapping]] = {}
        for mapping in mappings:
            grouped.setdefault(mapping.provider_id, []).append(mapping)

        refreshed = 0
        unavailable = 0
        failures: list[OfferRefreshFailure] = []
        now = datetime.now(UTC)

        for provider_mappings in grouped.values():
            provider = provider_mappings[0].provider
            capabilities = set(
                self.registry.capabilities_for(
                    provider.provider_type,
                    provider.category,
                    provider.metadata_json or {},
                )
            )
            if not capabilities.intersection(
                {ProviderCapability.PRODUCT_DETAIL, ProviderCapability.CATALOG}
            ):
                for mapping in provider_mappings:
                    failures.append(
                        OfferRefreshFailure(
                            mapping_id=mapping.id,
                            provider_id=provider.id,
                            error_code="CATALOG_UNSUPPORTED",
                        )
                    )
                continue

            try:
                client = self.registry.get_client(
                    provider_type=provider.provider_type,
                    provider_name=provider.name,
                    config=await self.router.build_provider_config(provider),
                    provider_id=str(provider.id),
                )
            except ProviderError as exc:
                code = type(exc).__name__
                for mapping in provider_mappings:
                    await self._record_error(session, mapping=mapping, now=now, error_code=code)
                    failures.append(
                        OfferRefreshFailure(mapping.id, provider.id, code)
                    )
                continue

            catalog: dict[str, ProviderProductDTO] | None = None
            if ProviderCapability.PRODUCT_DETAIL not in capabilities:
                try:
                    async with asyncio.timeout(self.router._provider_timeout(provider)):
                        products = await client.list_products()
                    catalog = {item.external_id: item for item in products}
                except (ProviderError, TimeoutError) as exc:
                    code = type(exc).__name__
                    for mapping in provider_mappings:
                        await self._record_error(session, mapping=mapping, now=now, error_code=code)
                        failures.append(
                            OfferRefreshFailure(mapping.id, provider.id, code)
                        )
                    continue

            for mapping in provider_mappings:
                try:
                    if catalog is not None:
                        dto = catalog.get(mapping.external_product_id)
                        if dto is None:
                            raise ProviderProductUnavailableError(
                                "Mapped product is absent from the provider catalog."
                            )
                    else:
                        async with asyncio.timeout(self.router._provider_timeout(provider)):
                            dto = await client.get_product(mapping.external_product_id)
                    await self._upsert_snapshot(session, mapping=mapping, dto=dto, now=now)
                    refreshed += 1
                    if not dto.is_available or (dto.stock is not None and dto.stock <= 0):
                        unavailable += 1
                except ProviderProductUnavailableError:
                    await self._upsert_unavailable_snapshot(session, mapping=mapping, now=now)
                    refreshed += 1
                    unavailable += 1
                except (ProviderError, TimeoutError) as exc:
                    code = type(exc).__name__
                    await self._record_error(session, mapping=mapping, now=now, error_code=code)
                    failures.append(OfferRefreshFailure(mapping.id, provider.id, code))

        await session.flush()
        return OfferRefreshResult(
            refreshed=refreshed,
            unavailable=unavailable,
            failed=len(failures),
            failures=tuple(failures),
        )

    async def _load_mappings(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        variant_id: uuid.UUID | None,
    ) -> list[ProviderProductMapping]:
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
        if variant_id is not None:
            stmt = stmt.where(
                (ProviderProductMapping.product_variant_id == variant_id)
                | (ProviderProductMapping.product_variant_id.is_(None))
            )
        return list((await session.execute(stmt)).scalars().all())

    async def _snapshot_for_mapping(
        self,
        session: AsyncSession,
        mapping_id: uuid.UUID,
    ) -> ProviderOfferSnapshot | None:
        return await session.scalar(
            select(ProviderOfferSnapshot).where(ProviderOfferSnapshot.mapping_id == mapping_id)
        )

    async def _upsert_snapshot(
        self,
        session: AsyncSession,
        *,
        mapping: ProviderProductMapping,
        dto: ProviderProductDTO,
        now: datetime,
    ) -> None:
        snapshot = await self._snapshot_for_mapping(session, mapping.id)
        if snapshot is None:
            snapshot = ProviderOfferSnapshot(
                tenant_id=mapping.tenant_id,
                provider_id=mapping.provider_id,
                mapping_id=mapping.id,
                product_id=mapping.product_id,
                product_variant_id=mapping.product_variant_id,
                external_product_id=mapping.external_product_id,
                cost_amount=dto.cost,
                cost_currency=(dto.currency or mapping.cost_currency).upper(),
                observed_at=now,
                expires_at=now + timedelta(seconds=self.freshness_seconds),
            )
            session.add(snapshot)
        snapshot.external_product_id = dto.external_id or mapping.external_product_id
        snapshot.display_name = (dto.name or "")[:255] or None
        snapshot.cost_amount = dto.cost
        snapshot.cost_currency = (dto.currency or mapping.cost_currency).upper()[:12]
        snapshot.is_available = bool(dto.is_available) and not (
            dto.stock is not None and dto.stock <= 0
        )
        snapshot.stock_quantity = dto.stock
        snapshot.min_quantity = max(1, int(dto.min_quantity))
        snapshot.max_quantity = max(snapshot.min_quantity, int(dto.max_quantity))
        snapshot.observed_at = now
        snapshot.expires_at = now + timedelta(seconds=self.freshness_seconds)
        snapshot.last_error_at = None
        snapshot.last_error_code = None

    async def _upsert_unavailable_snapshot(
        self,
        session: AsyncSession,
        *,
        mapping: ProviderProductMapping,
        now: datetime,
    ) -> None:
        snapshot = await self._snapshot_for_mapping(session, mapping.id)
        if snapshot is None:
            snapshot = ProviderOfferSnapshot(
                tenant_id=mapping.tenant_id,
                provider_id=mapping.provider_id,
                mapping_id=mapping.id,
                product_id=mapping.product_id,
                product_variant_id=mapping.product_variant_id,
                external_product_id=mapping.external_product_id,
                cost_amount=mapping.cost_price,
                cost_currency=mapping.cost_currency.upper(),
                observed_at=now,
                expires_at=now + timedelta(seconds=self.freshness_seconds),
            )
            session.add(snapshot)
        snapshot.is_available = False
        snapshot.stock_quantity = 0
        snapshot.observed_at = now
        snapshot.expires_at = now + timedelta(seconds=self.freshness_seconds)
        snapshot.last_error_at = None
        snapshot.last_error_code = None

    async def _record_error(
        self,
        session: AsyncSession,
        *,
        mapping: ProviderProductMapping,
        now: datetime,
        error_code: str,
    ) -> None:
        snapshot = await self._snapshot_for_mapping(session, mapping.id)
        if snapshot is None:
            return
        snapshot.last_error_at = now
        snapshot.last_error_code = error_code[:100]
