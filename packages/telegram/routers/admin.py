from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import settings
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
    membership = await _membership(db_session, tenant_context)
    if membership is None or membership.role not in _PRIVILEGED_ROLES:
        await message.answer("Admin access is not enabled for your tenant membership.")
        return
    public_url = resolve_tenant_public_url(
        tenant_context.tenant_settings,
        kind="admin",
        fallback=settings.admin_public_url,
    )
    if not public_url:
        await message.answer("Admin Web App is not configured. Add a public HTTPS URL in setup or platform settings.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Open Admin",
                    web_app=WebAppInfo(url=build_admin_url(public_url, tenant_context.bot_id)),
                )
            ]
        ]
    )
    await message.answer(
        f"⚙️ <b>{html.escape(tenant_context.display_name)}</b> administration",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
