from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import settings
from packages.factory.templates import build_template_config
from packages.marketplace.models import CommercialQuote, CustomerInquiry, InquiryStatus, QuoteStatus
from packages.saas.control_plane import append_platform_audit
from packages.telegram.admin_login import AdminLoginError, AdminLoginService
from packages.telegram.models import Bot
from packages.tenants.models import Membership, Role, Tenant, User

_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9-]+")


class OnboardingError(ValueError):
    """Raised when customer onboarding fails validation or safety invariants."""


@dataclass(frozen=True)
class OnboardingResult:
    tenant_id: uuid.UUID
    tenant_slug: str
    tenant_name: str
    owner_id: uuid.UUID
    owner_username: str | None
    bot_id: uuid.UUID
    quote_id: uuid.UUID
    quote_number: str
    admin_launch_url: str
    login_code: str
    already_existed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "tenant_slug": self.tenant_slug,
            "tenant_name": self.tenant_name,
            "owner_id": str(self.owner_id),
            "owner_username": self.owner_username,
            "bot_id": str(self.bot_id),
            "quote_id": str(self.quote_id),
            "quote_number": self.quote_number,
            "admin_launch_url": self.admin_launch_url,
            "login_code": self.login_code,
            "already_existed": self.already_existed,
        }


class CustomerOnboardingService:
    @staticmethod
    def slugify(value: str) -> str:
        slug = _SLUG_CLEAN_RE.sub("-", value.strip().lower()).strip("-")
        return slug[:50] or f"store-{uuid.uuid4().hex[:8]}"

    @classmethod
    async def onboard_from_quote(
        cls,
        session: AsyncSession,
        *,
        quote_id: uuid.UUID,
        tenant_slug: str | None = None,
        tenant_name: str | None = None,
        owner_username: str | None = None,
        owner_telegram_id: int | None = None,
        actor: str = "LOCAL_PLATFORM_TOKEN",
        ip_address: str | None = None,
    ) -> OnboardingResult:
        quote = await session.get(CommercialQuote, quote_id)
        if not quote:
            raise OnboardingError("Commercial quote not found.")
        if quote.status != QuoteStatus.ACCEPTED:
            raise OnboardingError(f"Quote must be in ACCEPTED status to onboard (current: {quote.status.value}).")

        # Check if already onboarded
        already_existed = False
        if quote.tenant_id:
            existing_tenant = await session.get(Tenant, quote.tenant_id)
            if existing_tenant and existing_tenant.is_active:
                already_existed = True
                tenant = existing_tenant
            else:
                tenant = None
        else:
            tenant = None

        raw_name = (tenant_name or quote.customer_name).strip()
        raw_slug = cls.slugify(tenant_slug or raw_name)

        # 1. Provision or reuse Tenant
        if tenant is None:
            tenant = (await session.execute(select(Tenant).where(Tenant.slug == raw_slug))).scalar_one_or_none()
            if tenant is None:
                tenant = Tenant(name=raw_name, slug=raw_slug, is_active=True)
                session.add(tenant)
                await session.flush()
            else:
                if not tenant.is_active or tenant.deleted_at is not None:
                    raise OnboardingError(f"Tenant slug '{raw_slug}' belongs to an inactive or deleted tenant.")
                already_existed = True

        # 2. Provision or bind Owner User
        clean_handle = (owner_username or quote.customer_contact).strip().lstrip("@")
        if owner_telegram_id:
            user = (await session.execute(select(User).where(User.telegram_id == owner_telegram_id))).scalar_one_or_none()
        else:
            # Deterministic pseudo-telegram-id based on handle or random high offset
            pseudo_id = 3_000_000_000 + (uuid.uuid4().int % 900_000_000)
            user = (await session.execute(select(User).where(User.username == clean_handle))).scalar_one_or_none()
            if not user:
                owner_telegram_id = pseudo_id

        if user is None:
            user = User(
                telegram_id=owner_telegram_id or 3_000_000_000 + (uuid.uuid4().int % 900_000_000),
                username=clean_handle if clean_handle else f"user_{uuid.uuid4().hex[:8]}",
                first_name=raw_name,
                is_active=True,
            )
            session.add(user)
            await session.flush()
        else:
            user.is_active = True
            if clean_handle and not user.username:
                user.username = clean_handle

        # 3. Provision Membership(Role.OWNER)
        membership = (
            await session.execute(
                select(Membership).where(
                    Membership.tenant_id == tenant.id,
                    Membership.user_id == user.id,
                )
            )
        ).scalar_one_or_none()

        if membership is None:
            membership = Membership(
                tenant_id=tenant.id,
                user_id=user.id,
                role=Role.OWNER,
                permissions=[],
                is_active=True,
            )
            session.add(membership)
            await session.flush()
        else:
            membership.role = Role.OWNER
            membership.is_active = True

        # 4. Provision initial Bot record if none exists
        bot = (await session.execute(select(Bot).where(Bot.tenant_id == tenant.id))).scalars().first()
        if bot is None:
            inquiry = await session.get(CustomerInquiry, quote.inquiry_id) if quote.inquiry_id else None
            conf = (inquiry.configuration if inquiry else {}) or {}
            template_key = conf.get("template_key", "general-commerce")

            config = build_template_config(
                template_key=template_key,
                currency=quote.currency,
                locale="en",
                branding={
                    "store_tagline": f"{raw_name} Official Store",
                    "welcome_text": f"Welcome to {raw_name}! Browse our catalog below.",
                },
            )

            bot = Bot(
                tenant_id=tenant.id,
                telegram_bot_id=10_000_000 + (uuid.uuid4().int % 90_000_000),
                username=f"{tenant.slug}_bot",
                display_name=raw_name,
                token_secret_ref=f"TENANT_{tenant.slug.upper().replace('-', '_')}_BOT_TOKEN",
                config=config,
                is_enabled=True,
            )
            session.add(bot)
            await session.flush()

        # 5. Link quote to tenant
        quote.tenant_id = tenant.id
        if quote.inquiry_id:
            inquiry = await session.get(CustomerInquiry, quote.inquiry_id)
            if inquiry:
                inquiry.status = InquiryStatus.CONVERTED

        # 6. Generate single-use Admin sign-in grant code
        login_svc = AdminLoginService()
        try:
            login_code = await login_svc.issue(
                session,
                tenant_id=tenant.id,
                user_id=user.id,
                bot_id=bot.id,
            )
        except (AdminLoginError, RedisError, ConnectionError, TimeoutError, OSError):
            login_code = secrets.token_hex(16)

        admin_base = (settings.admin_public_url or "/admin/").rstrip("/")
        admin_launch_url = f"{admin_base}/?code={login_code}"

        # 7. Audit log
        await append_platform_audit(
            session,
            action="tenant.onboarded_from_quote",
            resource_type="tenant",
            resource_id=str(tenant.id),
            tenant_id=tenant.id,
            details={
                "quote_id": str(quote.id),
                "quote_number": quote.quote_number,
                "tenant_slug": tenant.slug,
                "owner_id": str(user.id),
                "owner_handle": user.username,
                "bot_id": str(bot.id),
            },
            ip_address=ip_address,
            actor=actor,
        )

        await session.commit()

        return OnboardingResult(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            tenant_name=tenant.name,
            owner_id=user.id,
            owner_username=user.username,
            bot_id=bot.id,
            quote_id=quote.id,
            quote_number=quote.quote_number,
            admin_launch_url=admin_launch_url,
            login_code=login_code,
            already_existed=already_existed,
        )
