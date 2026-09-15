from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from packages.telegram.context import TenantContext

router = Router(name="menu_router")


def build_main_menu(tenant_context: TenantContext) -> InlineKeyboardMarkup:
    buttons = []
    if tenant_context.is_module_enabled("catalog"):
        buttons.append([InlineKeyboardButton(text="🛍️ Catalog", callback_data="nav:catalog")])

    row = []
    if tenant_context.is_module_enabled("orders"):
        row.append(InlineKeyboardButton(text="📦 My Orders", callback_data="nav:orders"))
    if tenant_context.is_module_enabled("account"):
        row.append(InlineKeyboardButton(text="💳 Wallet & Account", callback_data="nav:account"))
    if row:
        buttons.append(row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("menu"))
async def handle_menu_command(message: Message, tenant_context: TenantContext) -> None:
    keyboard = build_main_menu(tenant_context)
    await message.answer(
        f"🏠 <b>{tenant_context.display_name}</b> — Main Menu",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "nav:menu")
async def handle_menu_callback(callback: CallbackQuery, tenant_context: TenantContext) -> None:
    keyboard = build_main_menu(tenant_context)
    if callback.message:
        await callback.message.edit_text(
            f"🏠 <b>{tenant_context.display_name}</b> — Main Menu",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await callback.answer()
