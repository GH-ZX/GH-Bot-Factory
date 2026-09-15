import html

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from packages.core.config import settings
from packages.telegram.context import TenantContext
from packages.telegram.launch import build_miniapp_url, resolve_tenant_public_url

router = Router(name="start_router")


def _store_button(tenant_context: TenantContext) -> InlineKeyboardButton | None:
    public_url = resolve_tenant_public_url(
        tenant_context.tenant_settings,
        kind="miniapp",
        fallback=settings.miniapp_public_url,
    )
    if not public_url:
        return None
    return InlineKeyboardButton(
        text=tenant_context.get_branding("store_button_text", "🚀 Open Store"),
        web_app=WebAppInfo(url=build_miniapp_url(public_url, tenant_context.bot_id)),
    )


@router.message(CommandStart())
async def handle_start(message: Message, tenant_context: TenantContext) -> None:
    welcome_text = tenant_context.get_branding(
        "welcome_text",
        f"👋 Welcome to {tenant_context.display_name}!",
    )
    safe_welcome_text = html.escape(welcome_text)

    buttons = []
    store_button = (
        _store_button(tenant_context)
        if getattr(message.chat, "type", None) == "private"
        else None
    )
    if store_button is not None:
        buttons.append([store_button])
    elif tenant_context.is_module_enabled("catalog"):
        buttons.append([InlineKeyboardButton(text="🛍️ Catalog", callback_data="nav:catalog")])

    account_row = []
    if tenant_context.is_module_enabled("orders"):
        account_row.append(InlineKeyboardButton(text="📦 My Orders", callback_data="nav:orders"))
    if tenant_context.is_module_enabled("account"):
        account_row.append(
            InlineKeyboardButton(text="💳 Wallet & Account", callback_data="nav:account")
        )

    if account_row:
        buttons.append(account_row)

    support_handle = tenant_context.get_branding("support_contact", "")
    if support_handle:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="💬 Support",
                    url=f"https://t.me/{support_handle.lstrip('@')}",
                )
            ]
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"{safe_welcome_text}\n\nSelect an option below to get started:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
