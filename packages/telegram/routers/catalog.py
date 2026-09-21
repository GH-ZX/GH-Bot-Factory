from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.commerce.models import Product
from packages.telegram.context import TenantContext

router = Router(name="catalog_router")


async def render_catalog_text_and_keyboard(
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> tuple[str, InlineKeyboardMarkup]:
    stmt = (
        select(Product)
        .where(
            Product.tenant_id == tenant_context.tenant_id,
            Product.is_active.is_(True),
            Product.deleted_at.is_(None),
        )
        .options(selectinload(Product.variants))
        .order_by(Product.sort_order.asc(), Product.created_at.desc())
    )
    result = await db_session.execute(stmt)
    products = result.scalars().all()

    currency = tenant_context.currency
    if not products:
        text = "🛍️ <b>Store Catalog</b>\n\nNo products are currently available in this store."
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="nav:menu")]]
        )
        return text, keyboard

    lines = ["🛍️ <b>Store Catalog</b>\n"]
    buttons = []
    for prod in products:
        variants_summary = []
        for v in prod.variants:
            if v.is_active:
                variants_summary.append(f"• {v.title}: <b>{v.price} {currency}</b>")
        
        lines.append(f"<b>{prod.title}</b>")
        if prod.description:
            lines.append(f"<i>{prod.description}</i>")
        if variants_summary:
            lines.extend(variants_summary)
        lines.append("")

        buttons.append([InlineKeyboardButton(text=f"Select {prod.title}", callback_data=f"prod:{prod.id}")])

    buttons.append([InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="nav:menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return "\n".join(lines), keyboard


@router.message(Command("catalog"))
async def handle_catalog_command(
    message: Message,
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> None:
    text, keyboard = await render_catalog_text_and_keyboard(db_session, tenant_context)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "nav:catalog")
async def handle_catalog_callback(
    callback: CallbackQuery,
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> None:
    text, keyboard = await render_catalog_text_and_keyboard(db_session, tenant_context)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()
