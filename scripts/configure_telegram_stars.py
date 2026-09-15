"""Configure Telegram Stars wallet top-ups for one tenant using an existing bot secret reference."""

import argparse
import asyncio

from sqlalchemy import select

from packages.core.database import async_session_factory
from packages.payments.models import PaymentProviderConfig
from packages.telegram.models import Bot
from packages.tenants.models import Tenant


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument("--bot-id", help="Internal Bot UUID. Defaults to the first enabled tenant bot.")
    parser.add_argument("--display-name", default="Telegram Stars")
    parser.add_argument("--min-stars", type=int, default=10)
    parser.add_argument("--max-stars", type=int, default=10000)
    parser.add_argument("--terms-url", required=True)
    parser.add_argument("--terms-version", default="current")
    parser.add_argument("--transaction-scan-pages", type=int, default=10)
    return parser.parse_args()


async def configure(args: argparse.Namespace) -> None:
    if not args.terms_url.startswith("https://"):
        raise SystemExit("--terms-url must be an HTTPS URL.")
    if args.min_stars <= 0 or args.max_stars < args.min_stars:
        raise SystemExit("Invalid Stars top-up limits.")
    if args.transaction_scan_pages < 1 or args.transaction_scan_pages > 50:
        raise SystemExit("--transaction-scan-pages must be between 1 and 50.")

    async with async_session_factory() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == args.tenant_slug))
        ).scalar_one_or_none()
        if tenant is None:
            raise SystemExit(f"Tenant '{args.tenant_slug}' does not exist.")

        bot_stmt = select(Bot).where(
            Bot.tenant_id == tenant.id,
            Bot.is_enabled.is_(True),
            Bot.deleted_at.is_(None),
        )
        if args.bot_id:
            import uuid

            try:
                internal_bot_id = uuid.UUID(args.bot_id)
            except ValueError as exc:
                raise SystemExit("--bot-id must be a valid UUID.") from exc
            bot_stmt = bot_stmt.where(Bot.id == internal_bot_id)
        bot = (await session.execute(bot_stmt.order_by(Bot.created_at.asc()))).scalars().first()
        if bot is None:
            raise SystemExit("No enabled Telegram bot is available for this tenant.")

        config = (
            await session.execute(
                select(PaymentProviderConfig).where(
                    PaymentProviderConfig.tenant_id == tenant.id,
                    PaymentProviderConfig.provider_name == "telegram_stars",
                )
            )
        ).scalar_one_or_none()
        settings = {
            "display_name": args.display_name,
            "topup_enabled": True,
            "topup_min_amount": str(args.min_stars),
            "topup_max_amount": str(args.max_stars),
            "topup_currencies": ["XTR"],
            "topup_whole_units_only": True,
            "checkout_mode": "telegram_invoice",
            "terms_required": True,
            "terms_url": args.terms_url,
            "terms_version": args.terms_version,
            "invoice_title": "Wallet top-up",
            "invoice_description": f"Add Stars to your {tenant.name} wallet.",
            "price_label": "Wallet credit",
            "transaction_scan_pages": args.transaction_scan_pages,
            "chargeback_reconciliation_enabled": True,
        }
        if config is None:
            config = PaymentProviderConfig(
                tenant_id=tenant.id,
                provider_name="telegram_stars",
                is_enabled=True,
                credentials_ref=bot.token_secret_ref,
                webhook_secret_ref=None,
                settings_json=settings,
            )
            session.add(config)
        else:
            config.is_enabled = True
            config.credentials_ref = bot.token_secret_ref
            config.webhook_secret_ref = None
            config.settings_json = settings

        await session.commit()
        print(f"tenant_id={tenant.id}")
        print(f"bot_id={bot.id}")
        print("provider=telegram_stars")
        print(f"credentials_ref={bot.token_secret_ref}")
        print(f"terms_url={args.terms_url}")


def main() -> None:
    asyncio.run(configure(parse_args()))


if __name__ == "__main__":
    main()
