from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.providers.catalog import ProviderCapability
from packages.providers.clients.registry import ProviderClientRegistry, provider_registry
from packages.providers.exceptions import ProviderConfigurationError, ProviderTimeoutError
from packages.providers.models import Provider, ProviderCredential, ProviderHealthStatus
from packages.providers.router import ProviderRouter
from packages.telegram.secrets import SecretStorage, get_default_secret_storage


@dataclass(frozen=True, slots=True)
class ProviderConnectionTestResult:
    provider_id: uuid.UUID
    status: ProviderHealthStatus
    latency_ms: float | None
    message: str | None
    balance: Decimal | None
    balance_currency: str | None
    checked_at: datetime


def managed_provider_secret_ref(
    *,
    tenant_id: uuid.UUID,
    provider_id: uuid.UUID,
    credential_type: str,
) -> str:
    safe_type = re.sub(r"[^A-Za-z0-9_]", "_", credential_type.strip().upper())
    if not safe_type:
        raise ProviderConfigurationError("Credential type cannot be empty.")
    return f"GHBF_PROVIDER_{tenant_id.hex}_{provider_id.hex}_{safe_type}"


async def upsert_provider_credential_value(
    session: AsyncSession,
    *,
    provider: Provider,
    credential_type: str,
    secret_value: str,
    secret_storage: SecretStorage | None = None,
    registry: ProviderClientRegistry | None = None,
) -> ProviderCredential:
    """Store plaintext only at the secret boundary and persist a reference in SQL."""
    if not secret_value:
        raise ProviderConfigurationError("Credential value cannot be empty.")
    registry = registry or provider_registry
    definition = registry.get_definition(provider.provider_type)
    normalized_type = credential_type.strip().upper()
    declared = {spec.normalized_key() for spec in definition.credentials}
    if declared and normalized_type not in declared:
        raise ProviderConfigurationError(
            f"Credential type '{normalized_type}' is not declared by adapter '{definition.normalized_key}'."
        )

    secret_ref = managed_provider_secret_ref(
        tenant_id=provider.tenant_id,
        provider_id=provider.id,
        credential_type=normalized_type,
    )
    storage = secret_storage or get_default_secret_storage()
    await storage.set_secret(secret_ref, secret_value)

    credential = await session.scalar(
        select(ProviderCredential).where(
            ProviderCredential.tenant_id == provider.tenant_id,
            ProviderCredential.provider_id == provider.id,
            ProviderCredential.credential_type == normalized_type,
        )
    )
    if credential is None:
        credential = ProviderCredential(
            tenant_id=provider.tenant_id,
            provider_id=provider.id,
            credential_type=normalized_type,
            secret_ref=secret_ref,
        )
        session.add(credential)
    else:
        credential.secret_ref = secret_ref
    await session.flush()
    return credential


async def delete_provider_credential(
    session: AsyncSession,
    *,
    credential: ProviderCredential,
    secret_storage: SecretStorage | None = None,
) -> None:
    storage = secret_storage or get_default_secret_storage()
    managed_prefix = f"GHBF_PROVIDER_{credential.tenant_id.hex}_{credential.provider_id.hex}_"
    if credential.secret_ref.startswith(managed_prefix):
        await storage.delete_secret(credential.secret_ref)
    await session.delete(credential)
    await session.flush()


async def test_provider_connection(
    session: AsyncSession,
    *,
    provider: Provider,
    registry: ProviderClientRegistry | None = None,
    secret_storage: SecretStorage | None = None,
) -> ProviderConnectionTestResult:
    registry = registry or provider_registry
    registry.get_definition(provider.provider_type)
    registry.validate_config(
        provider.provider_type,
        provider.metadata_json or {},
        provider.category,
    )

    configured = {credential.credential_type.upper() for credential in provider.credentials}
    required = registry.required_credentials_for(provider.provider_type, provider.metadata_json or {})
    missing = [key for key in required if key not in configured]
    if missing:
        raise ProviderConfigurationError(
            f"Missing required provider credentials: {', '.join(sorted(missing))}."
        )

    router = ProviderRouter(registry=registry, secret_storage=secret_storage)
    config = await router.build_provider_config(provider)
    credential_values = [str(value) for value in config.get("credentials", {}).values() if value]
    client = registry.get_client(
        provider_type=provider.provider_type,
        provider_name=provider.name,
        config=config,
        provider_id=str(provider.id),
    )
    timeout_seconds = _provider_timeout(provider)
    checked_at = datetime.now(UTC)
    try:
        async with asyncio.timeout(timeout_seconds):
            health = await client.health_check()
            balance = None
            if ProviderCapability.BALANCE in registry.capabilities_for(
                provider.provider_type, provider.category, provider.metadata_json or {}
            ):
                balance = await client.get_balance()
    except TimeoutError as exc:
        provider.health_status = ProviderHealthStatus.UNAVAILABLE
        provider.consecutive_failures += 1
        provider.last_health_check_at = checked_at
        provider.last_health_latency_ms = None
        provider.last_health_message = "Provider connection timed out."
        await session.flush()
        raise ProviderTimeoutError("Provider connection test timed out.") from exc
    except Exception:
        provider.health_status = ProviderHealthStatus.UNAVAILABLE
        provider.consecutive_failures += 1
        provider.last_health_check_at = checked_at
        provider.last_health_latency_ms = None
        provider.last_health_message = "Provider connection failed."
        await session.flush()
        raise

    provider.health_status = health.status
    provider.last_health_check_at = checked_at
    provider.last_health_latency_ms = health.latency_ms
    safe_message = _redact_secrets(health.message or "", credential_values)
    provider.last_health_message = safe_message[:500] or None
    if health.status == ProviderHealthStatus.HEALTHY:
        provider.consecutive_failures = 0
    elif health.status == ProviderHealthStatus.UNAVAILABLE:
        provider.consecutive_failures += 1
    await session.flush()
    return ProviderConnectionTestResult(
        provider_id=provider.id,
        status=health.status,
        latency_ms=health.latency_ms,
        message=safe_message or None,
        balance=balance.balance if balance else None,
        balance_currency=balance.currency if balance else None,
        checked_at=checked_at,
    )


def _provider_timeout(provider: Provider) -> float:
    raw = (provider.metadata_json or {}).get("timeout_seconds", 15)
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        timeout = 15.0
    return max(1.0, min(timeout, 60.0))


def _redact_secrets(value: str, secrets: list[str]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted
