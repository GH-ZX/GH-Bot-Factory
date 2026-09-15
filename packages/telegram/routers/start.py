from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from packages.telegram.context import TenantContext

router = Router(name="start_router")


@router.message(CommandStart())
async def handle_start(message: Message, tenant_context: TenantContext) -> None:
    welcome_text = tenant_context.get_branding(
        "welcome_text",
        f"👋 Welcome to <b>{tenant_context.display_name}</b>!",
    )

    buttons = []
    if tenant_context.is_module_enabled("catalog"):
        buttons.append([InlineKeyboardButton(text="🛍️ Catalog", callback_data="nav:catalog")])

    account_row = []
    if tenant_context.is_module_enabled("orders"):
        account_row.append(InlineKeyboardButton(text="📦 My Orders", callback_data="nav:orders"))
    if tenant_context.is_module_enabled("account"):
        account_row.append(InlineKeyboardButton(text="💳 Wallet & Account", callback_data="nav:account"))

    if account_row:
        buttons.append(account_row)

    support_handle = tenant_context.get_branding("support_contact", "")
    if support_handle:
        buttons.append([InlineKeyboardButton(text="💬 Support", url=f"https://t.me/{support_handle.lstrip('@')}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"{welcome_text}\n\nSelect an option below to get started:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
