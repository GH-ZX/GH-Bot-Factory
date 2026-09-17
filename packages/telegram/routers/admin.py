from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import settings
from packages.telegram.admin_login import AdminLoginError, AdminLoginService
from packages.telegram.context import TenantContext
from packages.telegram.launch import build_admin_url, resolve_tenant_public_url
from packages.tenants.models import Membership, Role

router = Router(name="admin_router")
_PRIVILEGED_ROLES = {Role.STAFF, Role.MANAGER, Role.ADMIN, Role.OWNER}


async def _membership(db_session: AsyncSession, tenant_context: TenantContext) -> Membership | None:
    if tenant_context.user_id is None:
        return None
    return (
        await db_session.execute(
            select(Membership).where(
                Membership.tenant_id == tenant_context.tenant_id,
                Membership.user_id == tenant_context.user_id,
                Membership.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()


@router.message(Command("whoami"))
async def handle_whoami(
    message: Message,
    tenant_context: TenantContext,
    db_session: AsyncSession,
) -> None:
    membership = await _membership(db_session, tenant_context)
    role = membership.role.value if membership is not None else "UNASSIGNED"
    await message.answer(
        "\n".join(
            [
                f"Telegram user ID: <code>{tenant_context.telegram_user_id}</code>",
                f"Tenant role: <b>{role}</b>",
                f"Bot: <b>{html.escape(tenant_context.display_name)}</b>",
            ]
        ),
        parse_mode="HTML",
    )


@router.message(Command("admin"))
async def handle_admin_command(
    message: Message,
    tenant_context: TenantContext,
    db_session: AsyncSession,
) -> None:
    if message.chat.type != "private":
        await message.answer("Send /admin in a private chat with this bot to sign in.")
        return
    membership = await _membership(db_session, tenant_context)
    if membership is None or membership.role not in _PRIVILEGED_ROLES:
        await message.answer("Admin access is not enabled for your tenant membership.")
        return
    public_url = resolve_tenant_public_url(
        tenant_context.tenant_settings,
        kind="admin",
        fallback=settings.admin_public_url,
    )
    keyboard = None
    buttons = []
    if public_url:
        try:
            launch_url = build_admin_url(public_url, tenant_context.bot_id)
        except ValueError:
            launch_url = None
        if launch_url:
            buttons.append([
                InlineKeyboardButton(text="Open Admin", web_app=WebAppInfo(url=launch_url))
            ])
    try:
        code = await AdminLoginService().issue(
            db_session, tenant_id=tenant_context.tenant_id,
            user_id=membership.user_id, bot_id=tenant_context.bot_id,
        )
        base_admin = (public_url or "http://127.0.0.1:8010/admin/").rstrip("/")
        if not base_admin.endswith("/admin"):
            direct_url = f"{base_admin}/admin/?code={code}"
        else:
            direct_url = f"{base_admin}/?code={code}"

        if direct_url.startswith("https://"):
            buttons.append([
                InlineKeyboardButton(text="🔑 Sign in (Browser)", url=direct_url)
            ])

        instructions = (
            "\n\n<b>🔑 Browser Admin Access</b>\n"
            f"Click the link to sign in directly:\n"
            f'👉 <a href="{direct_url}">{direct_url}</a>\n\n'
            f"Or enter this code manually:\n"
            f"<code>{code}</code>\n\n"
            "<i>Valid for 5 minutes. Your session remains active in this browser until you click Exit.</i>"
        )
    except (RedisError, AdminLoginError):
        instructions = "\n\nBrowser sign-in is temporarily unavailable. Try /admin again shortly."
    if buttons:
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"<b>{html.escape(tenant_context.display_name)}</b> administration{instructions}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
