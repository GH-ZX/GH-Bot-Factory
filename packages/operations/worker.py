"""Durable announcement delivery; ambiguous sends are never replayed automatically."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot as TelegramBot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import func, select, update

from packages.core.database import async_session_factory
from packages.operations.models import Announcement, AnnouncementDelivery
from packages.telegram.models import Bot, TenantTelegramUser
from packages.telegram.secrets import get_default_secret_storage
from packages.tenants.models import Membership, Tenant

logger = logging.getLogger("operations.announcements")


class AnnouncementWorker:
    def __init__(self, session_factory=async_session_factory, sender=None):
        self.sessions = session_factory
        self.sender = sender or self._send
        self.task = None

    async def start(self):
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def run(self):
        while True:
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 - isolate transport failures without logging secrets
                # Exception details from transports may contain token-bearing URLs.
                logger.warning("Announcement worker cycle failed; durable state retained.")
            await asyncio.sleep(1)

    async def _send(self, bot, binding, announcement):
        token = await get_default_secret_storage().get_secret(bot.token_secret_ref)
        client = TelegramBot(token=token)
        try:
            return (
                await client.send_message(
                    binding.telegram_user_id, announcement.body, parse_mode=None, request_timeout=20
                )
            ).message_id
        finally:
            await client.session.close()

    async def run_once(self):
        now = datetime.now(UTC)
        async with self.sessions() as session:
            # Installation-wide worker discovery. Every mutation and relationship below binds the discovered tenant.
            # Sweep completed campaigns as well: concurrent final sends may each observe
            # the other's uncommitted RUNNING row before either commits its result.
            campaigns = (
                await session.execute(
                    select(Announcement.tenant_id, Announcement.id)
                    .where(
                        Announcement.status == "QUEUED",
                        ~select(AnnouncementDelivery.id)
                        .where(
                            AnnouncementDelivery.tenant_id == Announcement.tenant_id,
                            AnnouncementDelivery.announcement_id == Announcement.id,
                            AnnouncementDelivery.status.in_(["QUEUED", "RUNNING"]),
                        )
                        .exists(),
                    )
                    .limit(100)
                )
            ).all()
            for campaign_tenant, campaign_id in campaigns:
                failed = await session.scalar(
                    select(func.count())
                    .select_from(AnnouncementDelivery)
                    .where(
                        AnnouncementDelivery.tenant_id == campaign_tenant,
                        AnnouncementDelivery.announcement_id == campaign_id,
                        AnnouncementDelivery.status.in_(["UNKNOWN", "FAILED"]),
                    )
                )
                await session.execute(
                    update(Announcement)
                    .where(
                        Announcement.tenant_id == campaign_tenant,
                        Announcement.id == campaign_id,
                        Announcement.status == "QUEUED",
                    )
                    .values(status="NEEDS_REVIEW" if failed else "COMPLETED")
                )
            stale = (
                await session.execute(
                    select(
                        AnnouncementDelivery.tenant_id,
                        AnnouncementDelivery.id,
                        AnnouncementDelivery.announcement_id,
                    )
                    .where(
                        AnnouncementDelivery.status == "RUNNING",
                        AnnouncementDelivery.updated_at < now - timedelta(minutes=5),
                    )
                    .limit(100)
                )
            ).all()
            for tenant_id, delivery_id, announcement_id in stale:
                await session.execute(
                    update(AnnouncementDelivery)
                    .where(
                        AnnouncementDelivery.tenant_id == tenant_id,
                        AnnouncementDelivery.id == delivery_id,
                        AnnouncementDelivery.status == "RUNNING",
                        AnnouncementDelivery.updated_at < now - timedelta(minutes=5),
                    )
                    .values(status="UNKNOWN", error_code="INTERRUPTED_SEND")
                )
                pending = await session.scalar(
                    select(func.count())
                    .select_from(AnnouncementDelivery)
                    .where(
                        AnnouncementDelivery.tenant_id == tenant_id,
                        AnnouncementDelivery.announcement_id == announcement_id,
                        AnnouncementDelivery.status.in_(["QUEUED", "RUNNING"]),
                    )
                )
                if not pending:
                    await session.execute(
                        update(Announcement)
                        .where(
                            Announcement.tenant_id == tenant_id,
                            Announcement.id == announcement_id,
                            Announcement.status == "QUEUED",
                        )
                        .values(status="NEEDS_REVIEW")
                    )
            candidates = (
                await session.execute(
                    select(AnnouncementDelivery.tenant_id, AnnouncementDelivery.id)
                    .where(
                        AnnouncementDelivery.status == "QUEUED",
                        AnnouncementDelivery.next_attempt_at <= now,
                    )
                    .order_by(AnnouncementDelivery.next_attempt_at, AnnouncementDelivery.id)
                    .limit(10)
                )
            ).all()
            claimed = None
            for tenant_id, delivery_id in candidates:
                result = await session.execute(
                    update(AnnouncementDelivery)
                    .where(
                        AnnouncementDelivery.tenant_id == tenant_id,
                        AnnouncementDelivery.id == delivery_id,
                        AnnouncementDelivery.status == "QUEUED",
                        AnnouncementDelivery.next_attempt_at <= now,
                    )
                    .values(status="RUNNING", updated_at=now)
                )
                if result.rowcount == 1:
                    claimed = (tenant_id, delivery_id)
                    break
            await session.commit()
            if not claimed:
                return False
        tenant_id, delivery_id = claimed
        async with self.sessions() as session:
            delivery = await session.scalar(
                select(AnnouncementDelivery).where(
                    AnnouncementDelivery.tenant_id == tenant_id,
                    AnnouncementDelivery.id == delivery_id,
                )
            )
            announcement = await session.scalar(
                select(Announcement).where(
                    Announcement.tenant_id == tenant_id, Announcement.id == delivery.announcement_id
                )
            )
            bot = await session.scalar(
                select(Bot).where(
                    Bot.tenant_id == tenant_id,
                    Bot.id == announcement.bot_id,
                    Bot.is_enabled.is_(True),
                    Bot.deleted_at.is_(None),
                )
            )
            binding = await session.scalar(
                select(TenantTelegramUser).where(
                    TenantTelegramUser.tenant_id == tenant_id,
                    TenantTelegramUser.id == delivery.binding_id,
                    TenantTelegramUser.bot_id == announcement.bot_id,
                    TenantTelegramUser.is_blocked.is_(False),
                )
            )
            tenant = await session.scalar(
                select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
            )
            active_member = binding and await session.scalar(
                select(Membership.id).where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == binding.user_id,
                    Membership.is_active.is_(True),
                )
            )
            status, error, message_id = "SENT", None, None
            if (
                not tenant
                or not bot
                or not binding
                or not active_member
                or announcement.status == "CANCELLED"
            ):
                status, error = "CANCELLED", "RECIPIENT_UNAVAILABLE"
            else:
                try:
                    message_id = await self.sender(bot, binding, announcement)
                except TelegramRetryAfter as exc:
                    status, error = "QUEUED", "RATE_LIMITED"
                    delivery.next_attempt_at = now + timedelta(seconds=max(1, exc.retry_after))
                    await session.execute(
                        update(AnnouncementDelivery)
                        .where(
                            AnnouncementDelivery.tenant_id == tenant_id,
                            AnnouncementDelivery.announcement_id == announcement.id,
                            AnnouncementDelivery.status == "QUEUED",
                        )
                        .values(next_attempt_at=delivery.next_attempt_at)
                    )
                except (TelegramForbiddenError, TelegramBadRequest):
                    status, error = "FAILED", "TELEGRAM_REJECTED"
                except Exception:  # noqa: BLE001 - isolate transport failures without logging secrets
                    status, error = "UNKNOWN", "SEND_UNCONFIRMED"
            delivery.status, delivery.error_code, delivery.telegram_message_id = (
                status,
                error,
                message_id,
            )
            await session.flush()
            pending = await session.scalar(
                select(func.count())
                .select_from(AnnouncementDelivery)
                .where(
                    AnnouncementDelivery.tenant_id == tenant_id,
                    AnnouncementDelivery.announcement_id == announcement.id,
                    AnnouncementDelivery.status.in_(["QUEUED", "RUNNING"]),
                )
            )
            if not pending:
                failed = await session.scalar(
                    select(func.count())
                    .select_from(AnnouncementDelivery)
                    .where(
                        AnnouncementDelivery.tenant_id == tenant_id,
                        AnnouncementDelivery.announcement_id == announcement.id,
                        AnnouncementDelivery.status.in_(["UNKNOWN", "FAILED"]),
                    )
                )
                await session.execute(
                    update(Announcement)
                    .where(
                        Announcement.tenant_id == tenant_id,
                        Announcement.id == announcement.id,
                        Announcement.status == "QUEUED",
                    )
                    .values(status="NEEDS_REVIEW" if failed else "COMPLETED")
                )
            await session.commit()
            return True
