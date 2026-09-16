from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from packages.core.config import settings
from packages.core.database import async_session_factory
from packages.providers.catalog import ProviderCapability
from packages.providers.clients.registry import ProviderClientRegistry, provider_registry
from packages.providers.models import Provider, ProviderBalanceSnapshot
from packages.providers.router import ProviderRouter

logger = logging.getLogger("providers.balance-monitor")


class ProviderBalanceMonitor:
    """Poll supplier balances and persist read-only low-balance evidence.

    The monitor never disables a provider and never changes routing. It surfaces evidence
    only, leaving commercial/routing policy to the tenant/operator.
    """

    def __init__(
        self,
        *,
        registry: ProviderClientRegistry | None = None,
        router: ProviderRouter | None = None,
    ) -> None:
        self.registry = registry or provider_registry
        self.router = router or ProviderRouter(registry=self.registry)

    @staticmethod
    def _threshold(provider: Provider, currency: str) -> Decimal | None:
        raw = (provider.metadata_json or {}).get("low_balance_threshold")
        if raw is None or raw == "":
            return None
        configured_currency = str(
            (provider.metadata_json or {}).get("low_balance_currency") or currency
        ).strip().upper()
        if configured_currency != currency:
            return None
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError):
            return None
        return value if value.is_finite() and value >= 0 else None

    async def _snapshot(self, session: AsyncSession, provider: Provider) -> ProviderBalanceSnapshot:
        snapshot = await session.scalar(
            select(ProviderBalanceSnapshot).where(
                ProviderBalanceSnapshot.provider_id == provider.id,
                ProviderBalanceSnapshot.tenant_id == provider.tenant_id,
            )
        )
        if snapshot is None:
            snapshot = ProviderBalanceSnapshot(
                tenant_id=provider.tenant_id,
                provider_id=provider.id,
                is_low_balance=False,
            )
            session.add(snapshot)
            await session.flush()
        return snapshot

    async def refresh_provider(
        self, session: AsyncSession, *, provider: Provider
    ) -> ProviderBalanceSnapshot | None:
        # Callers are not required to preload credentials. Reload the provider with the
        # relationship eagerly populated so async ORM never attempts an implicit lazy-load.
        loaded_provider = await session.scalar(
            select(Provider)
            .where(Provider.id == provider.id, Provider.tenant_id == provider.tenant_id)
            .options(selectinload(Provider.credentials))
        )
        if loaded_provider is None:
            return None
        provider = loaded_provider
        capabilities = self.registry.capabilities_for(
            provider.provider_type, provider.category, provider.metadata_json or {}
        )
        if ProviderCapability.BALANCE not in capabilities:
            return None
        snapshot = await self._snapshot(session, provider)
        try:
            client = self.registry.get_client(
                provider_type=provider.provider_type,
                provider_name=provider.name,
                config=await self.router.build_provider_config(provider),
                provider_id=str(provider.id),
            )
            timeout_raw = (provider.metadata_json or {}).get("balance_timeout_seconds", 15)
            try:
                timeout_seconds = max(2.0, min(float(timeout_raw), 60.0))
            except (TypeError, ValueError):
                timeout_seconds = 15.0
            async with asyncio.timeout(timeout_seconds):
                result = await client.get_balance()
            balance = Decimal(result.balance)
            if not balance.is_finite():
                raise ValueError("Provider balance is not finite.")
            currency = str(result.currency or "").strip().upper()
            if not currency or len(currency) > 12:
                raise ValueError("Provider balance currency is invalid.")
            threshold = self._threshold(provider, currency)
            snapshot.balance = balance
            snapshot.currency = currency
            snapshot.low_balance_threshold = threshold
            snapshot.is_low_balance = threshold is not None and balance <= threshold
            snapshot.observed_at = datetime.now(UTC)
            snapshot.last_error_at = None
            snapshot.last_error = None
        except Exception as exc:  # noqa: BLE001
            snapshot.last_error_at = datetime.now(UTC)
            snapshot.last_error = f"{type(exc).__name__}: {exc}"[:500]
            logger.warning("Provider balance refresh failed for %s: %s", provider.id, exc)
        await session.flush()
        return snapshot

    async def refresh_all(self, session: AsyncSession, *, limit: int = 100) -> list[ProviderBalanceSnapshot]:
        providers = list(
            (
                await session.execute(
                    select(Provider)
                    .where(Provider.is_enabled.is_(True))
                    .options(selectinload(Provider.credentials))
                    .order_by(Provider.tenant_id, Provider.priority, Provider.id)
                    .limit(max(1, min(int(limit), 1000)))
                )
            ).scalars().all()
        )
        snapshots: list[ProviderBalanceSnapshot] = []
        for provider in providers:
            snapshot = await self.refresh_provider(session, provider=provider)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots


class ProviderBalanceMonitorWorker:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        monitor: ProviderBalanceMonitor | None = None,
        enabled: bool | None = None,
        interval_seconds: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        self.session_factory = session_factory or async_session_factory
        self.monitor = monitor or ProviderBalanceMonitor()
        self.enabled = settings.provider_balance_poll_enabled if enabled is None else bool(enabled)
        self.interval_seconds = int(
            settings.provider_balance_poll_interval_seconds
            if interval_seconds is None
            else interval_seconds
        )
        self.batch_size = int(
            settings.provider_balance_poll_batch_size if batch_size is None else batch_size
        )
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="provider-balance-monitor")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with self.session_factory() as session:
                    snapshots = await self.monitor.refresh_all(session, limit=self.batch_size)
                    await session.commit()
                if snapshots:
                    low = sum(1 for item in snapshots if item.is_low_balance)
                    logger.info("Provider balance monitor refreshed=%d low=%d", len(snapshots), low)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Provider balance monitor cycle failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
