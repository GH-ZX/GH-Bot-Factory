from __future__ import annotations

import copy
import re
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


class OnboardingUnavailableError(OnboardingError):
    """Temporary grant storage failure; onboarding was rolled back."""


@dataclass(frozen=True)
class OnboardingResult:
    tenant_id: uuid.UUID
    tenant_slug: str
    tenant_name: str
    owner_id: uuid.UUID
    owner_username: str | None
    bot_id: uuid.UUID | None
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
            "bot_id": str(self.bot_id) if self.bot_id else None,
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
        if not owner_telegram_id or owner_telegram_id <= 0:
            raise OnboardingError("A confirmed owner Telegram user ID is required; usernames are display-only.")
        quote = (await session.execute(
            select(CommercialQuote).where(CommercialQuote.id == quote_id).with_for_update()
        )).scalar_one_or_none()
        if not quote:
            raise OnboardingError("Commercial quote not found.")
        if quote.status != QuoteStatus.ACCEPTED:
            raise OnboardingError(f"Quote must be in ACCEPTED status to onboard (current: {quote.status.value}).")

        # Check if already onboarded
        already_existed = False
        if quote.tenant_id:
            existing_tenant = await session.get(Tenant, quote.tenant_id)
            if existing_tenant and existing_tenant.is_active and existing_tenant.deleted_at is None:
                already_existed = True
                tenant = existing_tenant
            else:
                raise OnboardingError("The linked tenant is inactive or deleted; restore it explicitly before onboarding.")
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
                raise OnboardingError("This tenant slug is already in use. Choose a different slug.")

        # Telegram user IDs are supplied by the authenticated platform operator.
        # Usernames never select an identity or authorize an existing tenant.
        clean_handle = (owner_username or "").strip().lstrip("@") or None
        user = (await session.execute(
            select(User).where(User.telegram_id == owner_telegram_id)
        )).scalar_one_or_none()
        if user is None:
            if already_existed:
                raise OnboardingError("The supplied user is not the existing tenant owner.")
            user = User(telegram_id=owner_telegram_id, username=clean_handle, first_name=raw_name, is_active=True)
            session.add(user)
            await session.flush()
        elif not user.is_active or user.deleted_at is not None:
            raise OnboardingError("The owner account is inactive or deleted.")

        # 3. Provision Membership(Role.OWNER)
        membership = (
            await session.execute(
                select(Membership).where(
                    Membership.tenant_id == tenant.id,
                    Membership.user_id == user.id,
                )
            )
        ).scalar_one_or_none()

        if already_existed and (membership is None or membership.role != Role.OWNER or not membership.is_active):
            raise OnboardingError("Onboarding retries require an existing active owner; ownership changes use member management.")
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
        # Keep template intent without inventing a Telegram bot identity.
        # The owner connects a real bot through the verified provisioning workflow.
        if not already_existed:
            inquiry = await session.get(CustomerInquiry, quote.inquiry_id) if quote.inquiry_id else None
            scope = quote.scope_snapshot or {}
            conf = scope.get("configuration") or (inquiry.configuration if inquiry else {}) or {}
            brief = conf.get("brief") or {}
            branding = {"store_tagline": f"{raw_name} Official Store"}
            if brief.get("accent"):
                branding["brand_accent"] = brief["accent"]
            config = build_template_config(
                template_key=conf.get("template_key", "general-commerce"),
                currency=quote.currency, locale="ar" if brief.get("store_language") == "Arabic" else "en",
                branding=branding,
            )
            tenant.settings = {
                **(tenant.settings or {}), "onboarding_template": config,
                # Requested features remain scope, never runtime permissions/entitlements.
                "delivery_scope": copy.deepcopy(scope or {"configuration": conf}),
            }
        bot = (await session.execute(select(Bot).where(
            Bot.tenant_id == tenant.id, Bot.deleted_at.is_(None),
            Bot.credential_status == "VERIFIED",
        ).order_by(Bot.created_at))).scalars().first()

        # 5. Link quote to tenant
        quote.tenant_id = tenant.id
        if quote.inquiry_id:
            inquiry = await session.get(CustomerInquiry, quote.inquiry_id)
            if inquiry:
                inquiry.status = InquiryStatus.CONVERTED

        # 6. Generate single-use Admin sign-in grant code
        login_svc = AdminLoginService()
        try:
            login_code = await login_svc.issue_owner_setup(
                session,
                tenant_id=tenant.id,
                user_id=user.id,
                quote_id=quote.id,
            )
        except (AdminLoginError, RedisError, ConnectionError, TimeoutError, OSError) as exc:
            await session.rollback()
            raise OnboardingUnavailableError("Owner sign-in is temporarily unavailable. Retry onboarding shortly.") from exc

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
                "bot_id": str(bot.id) if bot else None,
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
            bot_id=bot.id if bot else None,
            quote_id=quote.id,
            quote_number=quote.quote_number,
            admin_launch_url=admin_launch_url,
            login_code=login_code,
            already_existed=already_existed,
        )
