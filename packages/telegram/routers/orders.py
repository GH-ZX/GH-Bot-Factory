from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Order
from packages.telegram.context import TenantContext

router = Router(name="orders_router")

STATUS_EMOJIS = {
    "PENDING": "⏳",
    "PAYMENT_PENDING": "💳",
    "PAID": "✅",
    "PROCESSING": "⚙️",
    "PARTIALLY_FULFILLED": "📦",
    "FULFILLED": "🎉",
    "CANCELLED": "❌",
    "FAILED": "⚠️",
    "REFUNDED": "↩️",
}


async def render_orders_text_and_keyboard(
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> tuple[str, InlineKeyboardMarkup]:
    if not tenant_context.user_id:
        text = "📦 <b>Your Orders</b>\n\nNo user account linked yet."
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="nav:menu")]]
        )
        return text, keyboard

    stmt = (
        select(Order)
        .where(
            Order.tenant_id == tenant_context.tenant_id,
            Order.user_id == tenant_context.user_id,
        )
        .order_by(Order.created_at.desc())
        .limit(10)
    )
    result = await db_session.execute(stmt)
    orders = result.scalars().all()

    if not orders:
        text = "📦 <b>Your Orders</b>\n\nYou haven't placed any orders yet."
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🛍️ Browse Catalog", callback_data="nav:catalog")],
                [InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="nav:menu")],
            ]
        )
        return text, keyboard

    lines = ["📦 <b>Your Recent Orders</b>\n"]
    for ord_item in orders:
        emoji = STATUS_EMOJIS.get(ord_item.status.value, "📋")
        lines.append(
            f"{emoji} Order <code>#{ord_item.order_number}</code>\n"
            f"   Status: <b>{ord_item.status.value}</b> | Amount: <b>{ord_item.total_amount} {ord_item.currency}</b>\n"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="nav:menu")]]
    )
    return "\n".join(lines), keyboard


@router.message(Command("orders"))
async def handle_orders_command(
    message: Message,
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> None:
    text, keyboard = await render_orders_text_and_keyboard(db_session, tenant_context)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "nav:orders")
async def handle_orders_callback(
    callback: CallbackQuery,
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> None:
    text, keyboard = await render_orders_text_and_keyboard(db_session, tenant_context)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()
