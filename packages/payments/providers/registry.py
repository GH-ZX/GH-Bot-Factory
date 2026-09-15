import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.payments.exceptions import (
    PaymentProviderError,
)
from packages.payments.models import PaymentProviderConfig
from packages.payments.providers.interface import PaymentProvider
from packages.payments.providers.mock import MockPaymentProvider
from packages.telegram.secrets import SecretStorage

logger = logging.getLogger("payments.registry")

ProviderFactory = Callable[[dict[str, Any], str, str | None], PaymentProvider]


class PaymentProviderRegistry:
    """Multi-tenant payment provider registry and factory.

    Resolves tenant-scoped provider configurations, decrypts/retrieves secret credentials
    via SecretStorage, and returns tenant-isolated PaymentProvider adapters.
    """

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}
        self._instance_overrides: dict[tuple[uuid.UUID, str], PaymentProvider] = {}
        self._cached_instances: dict[tuple[uuid.UUID, str], PaymentProvider] = {}
        # Register default mock provider factory
        self.register_factory("mock", self._create_mock_provider)

    def register_factory(self, provider_name: str, factory: ProviderFactory) -> None:
        self._factories[provider_name.lower()] = factory

    def register_instance(
        self,
        tenant_id: uuid.UUID,
        provider_name: str,
        provider: PaymentProvider,
    ) -> None:
        """Register an explicit provider instance override (primarily for test harnesses)."""
        self._instance_overrides[(tenant_id, provider_name.lower())] = provider

    def unregister_instance(self, tenant_id: uuid.UUID, provider_name: str) -> None:
        self._instance_overrides.pop((tenant_id, provider_name.lower()), None)
        self._cached_instances.pop((tenant_id, provider_name.lower()), None)

    async def get_provider(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        provider_name: str,
        secret_storage: SecretStorage,
    ) -> PaymentProvider:
        """Resolve and instantiate a tenant-scoped payment provider.

        Strictly enforces tenant isolation: Tenant A cannot access Tenant B's credentials.
        """
        normalized_name = provider_name.lower()

        # Check explicit test instance overrides first
        override_key = (tenant_id, normalized_name)
        if override_key in self._instance_overrides:
            return self._instance_overrides[override_key]

        # Query tenant-scoped configuration from DB
        stmt = select(PaymentProviderConfig).where(
            PaymentProviderConfig.tenant_id == tenant_id,
            PaymentProviderConfig.provider_name == normalized_name,
        )
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()

        if config is None:
            raise PaymentProviderError(
                f"Payment provider '{provider_name}' is not configured for tenant {tenant_id}."
            )

        if not config.is_enabled:
            raise PaymentProviderError(
                f"Payment provider '{provider_name}' is disabled for tenant {tenant_id}."
            )

        factory = self._factories.get(normalized_name)
        if factory is None:
            raise PaymentProviderError(
                f"No provider adapter registered for provider type '{provider_name}'."
            )

        # Check cached instances next
        if override_key in self._cached_instances:
            return self._cached_instances[override_key]

        # Resolve credentials securely from SecretStorage (never plaintext in DB)
        credentials = await secret_storage.get_secret(config.credentials_ref)
        webhook_secret = (
            await secret_storage.get_secret(config.webhook_secret_ref)
            if config.webhook_secret_ref
            else None
        )

        instance = factory(config.settings_json, credentials, webhook_secret)
        self._cached_instances[override_key] = instance
        return instance

    @staticmethod
    def _create_mock_provider(
        settings: dict[str, Any],
        credentials: str,
        webhook_secret: str | None,
    ) -> MockPaymentProvider:
        return MockPaymentProvider(
            provider_name=settings.get("provider_name", "mock"),
            supports_idempotency_keys=settings.get("supports_idempotency_keys", True),
            supports_payment_lookup=settings.get("supports_payment_lookup", True),
            supports_webhooks=settings.get("supports_webhooks", True),
            supports_refunds=settings.get("supports_refunds", True),
            supports_partial_refunds=settings.get("supports_partial_refunds", True),
        )


# Global registry singleton
default_payment_provider_registry = PaymentProviderRegistry()
