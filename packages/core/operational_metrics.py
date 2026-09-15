from __future__ import annotations

import asyncio
import time

from sqlalchemy import func, select

from packages.core.config import settings
from packages.core.database import async_session_factory
from packages.fulfillment.models import FulfillmentJobRecord, FulfillmentJobStatus
from packages.factory.models import BotProvisioningJob, BotProvisioningStatus
from packages.payments.models import FinancialResolutionCase, FinancialResolutionCaseStatus
from packages.providers.models import Provider, ProviderHealthStatus
from packages.telegram.models import Bot

_CACHE_TTL_SECONDS = 10.0
_cache_lock = asyncio.Lock()
_cache_at = 0.0
_cache_text = ""


async def render_operational_metrics() -> str:
    global _cache_at, _cache_text
    now = time.monotonic()
    if _cache_text and now - _cache_at < _CACHE_TTL_SECONDS:
        return _cache_text
    async with _cache_lock:
        now = time.monotonic()
        if _cache_text and now - _cache_at < _CACHE_TTL_SECONDS:
            return _cache_text
        lines: list[str] = []
        async with async_session_factory() as session:
            for status in (
                FulfillmentJobStatus.QUEUED,
                FulfillmentJobStatus.RUNNING,
                FulfillmentJobStatus.DEAD_LETTER,
                FulfillmentJobStatus.FAILED,
            ):
                count = await session.scalar(
                    select(func.count()).select_from(FulfillmentJobRecord).where(
                        FulfillmentJobRecord.status == status
                    )
                )
                lines.append(f'ghbf_fulfillment_jobs{{status="{status.value}"}} {int(count or 0)}')
            for status in (
                BotProvisioningStatus.PENDING,
                BotProvisioningStatus.RUNNING,
                BotProvisioningStatus.RETRY,
                BotProvisioningStatus.FAILED,
            ):
                count = await session.scalar(
                    select(func.count()).select_from(BotProvisioningJob).where(
                        BotProvisioningJob.status == status
                    )
                )
                lines.append(f'ghbf_bot_provisioning_jobs{{status="{status.value}"}} {int(count or 0)}')
            enabled_bots = await session.scalar(
                select(func.count()).select_from(Bot).where(
                    Bot.is_enabled.is_(True),
                    Bot.deleted_at.is_(None),
                )
            )
            lines.append(f"ghbf_bots_enabled {int(enabled_bots or 0)}")

            open_cases = await session.scalar(
                select(func.count()).select_from(FinancialResolutionCase).where(
                    FinancialResolutionCase.status != FinancialResolutionCaseStatus.RESOLVED
                )
            )
            lines.append(f"ghbf_financial_resolution_cases_open {int(open_cases or 0)}")
            unavailable = await session.scalar(
                select(func.count()).select_from(Provider).where(
                    Provider.is_enabled.is_(True),
                    Provider.health_status == ProviderHealthStatus.UNAVAILABLE,
                )
            )
            lines.append(f"ghbf_providers_unavailable {int(unavailable or 0)}")

        try:
            from redis.asyncio import Redis

            client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
            try:
                for service in ("api", "worker", "bot-runtime"):
                    keys = []
                    async for key in client.scan_iter(match=f"ghbf:heartbeat:{service}:*"):
                        keys.append(key)
                    lines.append(f'ghbf_service_heartbeat_instances{{service="{service}"}} {len(keys)}')
            finally:
                await client.aclose()
        except Exception:
            lines.append("ghbf_metrics_redis_collection_error 1")

        _cache_text = "\n".join(lines) + "\n"
        _cache_at = time.monotonic()
        return _cache_text
