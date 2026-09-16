from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.providers.catalog import ProviderCapability
from packages.providers.clients.base import BaseProviderClient
from packages.providers.clients.registry import ProviderClientRegistry, provider_registry
from packages.providers.contracts import (
    NumberActivationSnapshot,
    NumberCountryDTO,
    NumberOfferDTO,
    NumberProviderOperations,
    NumberReservationRequest,
    NumberServiceDTO,
)
from packages.providers.exceptions import ProviderConfigurationError, ProviderTimeoutError
from packages.providers.models import Provider, ProviderCategory
from packages.providers.router import ProviderRouter
from packages.telegram.secrets import SecretStorage

T = TypeVar("T")


class ProviderOperationsService:
    """Tenant-scoped entry point for category-specific provider operations.

    The service deliberately returns canonical contracts and never exposes the adapter instance,
    provider credentials, or vendor-specific status strings to commerce/bot code.
    """

    def __init__(
        self,
        *,
        registry: ProviderClientRegistry | None = None,
        secret_storage: SecretStorage | None = None,
    ) -> None:
        self.registry = registry or provider_registry
        self.router = ProviderRouter(registry=self.registry, secret_storage=secret_storage)

    async def _load_provider(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        category: ProviderCategory,
    ) -> Provider:
        provider = await session.scalar(
            select(Provider)
            .where(
                Provider.id == provider_id,
                Provider.tenant_id == tenant_id,
                Provider.category == category,
                Provider.is_enabled.is_(True),
            )
            .options(selectinload(Provider.credentials))
        )
        if provider is None:
            raise ProviderConfigurationError(
                f"Enabled {category.value} provider connection was not found for this tenant."
            )
        return provider

    async def _number_client(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        capability: ProviderCapability,
    ) -> tuple[Provider, NumberProviderOperations]:
        provider = await self._load_provider(
            session,
            tenant_id=tenant_id,
            provider_id=provider_id,
            category=ProviderCategory.NUMBER,
        )
        definition = self.registry.get_definition(provider.provider_type)
        if capability not in self.registry.capabilities_for(
            provider.provider_type, provider.category, provider.metadata_json or {}
        ):
            raise ProviderConfigurationError(
                f"Adapter '{definition.normalized_key}' does not declare capability '{capability.value}'."
            )
        client: BaseProviderClient = self.registry.get_client(
            provider_type=provider.provider_type,
            provider_name=provider.name,
            config=await self.router.build_provider_config(provider),
            provider_id=str(provider.id),
        )
        if not isinstance(client, NumberProviderOperations):
            raise ProviderConfigurationError(
                f"Adapter '{definition.normalized_key}' declares number operations but does not implement them."
            )
        return provider, client

    async def _call(
        self,
        provider: Provider,
        operation_name: str,
        callback: Callable[[], Awaitable[T]],
    ) -> T:
        timeout_seconds = self.router._provider_timeout(provider)
        try:
            async with asyncio.timeout(timeout_seconds):
                return await callback()
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"Provider '{provider.name}' timed out during {operation_name}."
            ) from exc

    async def list_number_services(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
    ) -> list[NumberServiceDTO]:
        provider, client = await self._number_client(
            session,
            tenant_id=tenant_id,
            provider_id=provider_id,
            capability=ProviderCapability.NUMBER_SERVICES,
        )
        return await self._call(provider, "number service discovery", client.list_number_services)

    async def list_number_countries(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        service: str | None = None,
    ) -> list[NumberCountryDTO]:
        provider, client = await self._number_client(
            session,
            tenant_id=tenant_id,
            provider_id=provider_id,
            capability=ProviderCapability.NUMBER_COUNTRIES,
        )
        return await self._call(
            provider,
            "number country discovery",
            lambda: client.list_number_countries(service),
        )

    async def list_number_offers(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        service: str,
        country: str,
    ) -> list[NumberOfferDTO]:
        provider, client = await self._number_client(
            session,
            tenant_id=tenant_id,
            provider_id=provider_id,
            capability=ProviderCapability.NUMBER_OFFERS,
        )
        return await self._call(
            provider,
            "number offer discovery",
            lambda: client.list_number_offers(service=service, country=country),
        )

    async def reserve_number(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        request: NumberReservationRequest,
    ) -> NumberActivationSnapshot:
        provider, client = await self._number_client(
            session,
            tenant_id=tenant_id,
            provider_id=provider_id,
            capability=ProviderCapability.NUMBER_RESERVE,
        )
        return await self._call(provider, "number reservation", lambda: client.reserve_number(request))

    async def get_number_activation(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        external_order_id: str,
    ) -> NumberActivationSnapshot:
        provider, client = await self._number_client(
            session,
            tenant_id=tenant_id,
            provider_id=provider_id,
            capability=ProviderCapability.NUMBER_ACTIVATION,
        )
        return await self._call(
            provider,
            "number activation status",
            lambda: client.get_number_activation(external_order_id),
        )

    async def cancel_number_activation(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        external_order_id: str,
    ) -> NumberActivationSnapshot:
        provider, client = await self._number_client(
            session,
            tenant_id=tenant_id,
            provider_id=provider_id,
            capability=ProviderCapability.NUMBER_CANCEL,
        )
        return await self._call(
            provider,
            "number activation cancellation",
            lambda: client.cancel_number_activation(external_order_id),
        )

    async def finish_number_activation(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        external_order_id: str,
    ) -> NumberActivationSnapshot:
        provider, client = await self._number_client(
            session,
            tenant_id=tenant_id,
            provider_id=provider_id,
            capability=ProviderCapability.NUMBER_FINISH,
        )
        return await self._call(
            provider,
            "number activation completion",
            lambda: client.finish_number_activation(external_order_id),
        )
