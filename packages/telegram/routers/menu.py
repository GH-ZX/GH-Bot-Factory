import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from packages.core.config import settings
from packages.telegram.context import TenantContext
from packages.telegram.launch import build_miniapp_url, resolve_tenant_public_url

router = Router(name="menu_router")


def build_main_menu(
    tenant_context: TenantContext,
    *,
    allow_web_app: bool = True,
) -> InlineKeyboardMarkup:
    buttons = []
    arabic = str(tenant_context.locale).startswith("ar")
    public_url = resolve_tenant_public_url(tenant_context.tenant_settings, kind="miniapp", fallback=settings.miniapp_public_url)
    if allow_web_app and public_url:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=tenant_context.get_branding("store_button_text", "🚀 Open Store"),
                    web_app=WebAppInfo(
                        url=build_miniapp_url(
                            public_url,
                            tenant_context.bot_id,
                        )
                    ),
                )
            ]
        )
    elif tenant_context.is_module_enabled("catalog"):
        buttons.append([InlineKeyboardButton(text="🛍️ المنتجات" if arabic else "🛍️ Catalog", callback_data="nav:catalog")])

    row = []
    if tenant_context.is_module_enabled("orders"):
        row.append(InlineKeyboardButton(text="📦 طلباتي" if arabic else "📦 My Orders", callback_data="nav:orders"))
    if tenant_context.is_module_enabled("account"):
        row.append(InlineKeyboardButton(text="💳 المحفظة والحساب" if arabic else "💳 Wallet & Account", callback_data="nav:account"))
    if row:
        buttons.append(row)

    if allow_web_app and public_url:
        launch = build_miniapp_url(public_url, tenant_context.bot_id)
        buttons.append([
            InlineKeyboardButton(text="➕ شحن الرصيد" if arabic else "➕ Recharge", web_app=WebAppInfo(url=launch + "&view=recharge")),
            InlineKeyboardButton(text="💬 الدعم" if arabic else "💬 Support", web_app=WebAppInfo(url=launch + "&view=support")),
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("menu"))
async def handle_menu_command(message: Message, tenant_context: TenantContext) -> None:
    keyboard = build_main_menu(
        tenant_context,
        allow_web_app=getattr(message.chat, "type", None) == "private",
    )
    await message.answer(
        f"🏠 <b>{html.escape(tenant_context.display_name)}</b> — Main Menu",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "nav:menu")
async def handle_menu_callback(callback: CallbackQuery, tenant_context: TenantContext) -> None:
    keyboard = build_main_menu(
        tenant_context,
        allow_web_app=(
            callback.message is not None
            and getattr(callback.message.chat, "type", None) == "private"
        ),
    )
    if callback.message:
        await callback.message.edit_text(
            f"🏠 <b>{html.escape(tenant_context.display_name)}</b> — Main Menu",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await callback.answer()
