from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from packages.payments.service import LedgerService
from packages.telegram.context import TenantContext

router = Router(name="account_router")


async def render_account_text_and_keyboard(
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> tuple[str, InlineKeyboardMarkup]:
    if not tenant_context.user_id:
        text = "💳 <b>Account & Wallet</b>\n\nNo user account linked."
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="nav:menu")]]
        )
        return text, keyboard

    currency = tenant_context.currency
    wallet = await LedgerService.get_or_create_wallet(
        session=db_session,
        tenant_id=tenant_context.tenant_id,
        user_id=tenant_context.user_id,
        currency=currency,
    )

    text = (
        f"💳 <b>Account Profile & Wallet</b>\n\n"
        f"👤 <b>Telegram ID:</b> <code>{tenant_context.telegram_user_id}</code>\n"
        f"🏪 <b>Store:</b> {tenant_context.tenant_name}\n"
        f"💰 <b>Wallet Balance:</b> <b>{wallet.balance} {wallet.currency}</b>\n\n"
        f"<i>All balance transactions are auditable via the multi-tenant ledger.</i>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Deposit / Top-up", callback_data="wallet:topup_info")],
            [InlineKeyboardButton(text="📦 My Orders", callback_data="nav:orders")],
            [InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="nav:menu")],
        ]
    )
    return text, keyboard


@router.message(Command("account"))
@router.message(Command("wallet"))
async def handle_account_command(
    message: Message,
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> None:
    text, keyboard = await render_account_text_and_keyboard(db_session, tenant_context)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "nav:account")
async def handle_account_callback(
    callback: CallbackQuery,
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> None:
    text, keyboard = await render_account_text_and_keyboard(db_session, tenant_context)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "wallet:topup_info")
async def handle_topup_info_callback(
    callback: CallbackQuery,
    tenant_context: TenantContext,
) -> None:
    support_contact = tenant_context.get_branding("support_contact", "@admin")
    text = (
        f"💳 <b>Wallet Top-Up Instructions</b>\n\n"
        f"To top up your balance in <b>{tenant_context.tenant_name}</b>, please contact administration:\n"
        f"👉 <b>{support_contact}</b>\n\n"
        f"Include your Telegram ID: <code>{tenant_context.telegram_user_id}</code>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back to Account", callback_data="nav:account")]]
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()
