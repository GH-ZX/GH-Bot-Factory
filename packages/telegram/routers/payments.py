from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, PreCheckoutQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.payments.models import PaymentProviderConfig
from packages.payments.payment_service import PaymentService
from packages.telegram.context import TenantContext

router = Router(name="payments_router")


@router.pre_checkout_query()
async def handle_pre_checkout_query(
    pre_checkout_query: PreCheckoutQuery,
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> None:
    """Approve only server-authoritative Telegram Stars wallet top-up invoices."""
    if tenant_context.user_id is None:
        await pre_checkout_query.answer(ok=False, error_message="Customer account is unavailable.")
        return
    service = PaymentService()
    try:
        await service.validate_telegram_stars_checkout(
            session=db_session,
            tenant_id=tenant_context.tenant_id,
            user_id=tenant_context.user_id,
            invoice_payload=pre_checkout_query.invoice_payload,
            amount=Decimal(pre_checkout_query.total_amount),
            currency=pre_checkout_query.currency,
        )
    except Exception:  # noqa: BLE001
        await pre_checkout_query.answer(
            ok=False,
            error_message="This payment can no longer be accepted. Please create a new top-up.",
        )
        return
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def handle_successful_payment(
    message: Message,
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> None:
    """Settle Telegram successful_payment exactly once into the tenant wallet."""
    payment = message.successful_payment
    if payment is None or tenant_context.user_id is None:
        return
    service = PaymentService()
    try:
        intent = await service.settle_telegram_stars_topup(
            session=db_session,
            tenant_id=tenant_context.tenant_id,
            user_id=tenant_context.user_id,
            invoice_payload=payment.invoice_payload,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            amount=Decimal(payment.total_amount),
            currency=payment.currency,
        )
        await db_session.commit()
    except Exception:  # noqa: BLE001
        await db_session.rollback()
        await message.answer(
            "Payment was received by Telegram but the wallet confirmation needs review. "
            "Please contact /paysupport and do not pay again."
        )
        return

    await message.answer(
        f"Payment confirmed. Your wallet was credited with {int(intent.amount)} Stars."
    )


@router.message(Command("paysupport"))
async def handle_pay_support(message: Message, tenant_context: TenantContext) -> None:
    support_contact = tenant_context.get_branding("support_contact", "@support")
    await message.answer(
        f"Payment support for {tenant_context.tenant_name}: {support_contact}\n"
        "Include your Telegram ID and approximate payment time. Never send your password or bot token."
    )


@router.message(Command("terms"))
async def handle_payment_terms(
    message: Message,
    db_session: AsyncSession,
    tenant_context: TenantContext,
) -> None:
    stmt = select(PaymentProviderConfig).where(
        PaymentProviderConfig.tenant_id == tenant_context.tenant_id,
        PaymentProviderConfig.provider_name == "telegram_stars",
        PaymentProviderConfig.is_enabled.is_(True),
    )
    config = (await db_session.execute(stmt)).scalar_one_or_none()
    terms_url = str((config.settings_json or {}).get("terms_url") or "") if config else ""
    if terms_url.startswith("https://"):
        await message.answer(f"Store payment terms: {terms_url}")
    else:
        await message.answer("Payment terms are not configured. Please contact /paysupport.")
