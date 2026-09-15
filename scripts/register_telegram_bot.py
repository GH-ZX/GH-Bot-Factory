"""Register or update one Telegram bot without storing its token in the database."""

import argparse
import asyncio
import os

from sqlalchemy import select

from packages.core.database import async_session_factory
from packages.telegram.models import Bot
from packages.tenants.models import Tenant


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument("--tenant-name", help="Required only when creating a new tenant")
    parser.add_argument("--telegram-bot-id", type=int)
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--token-secret-ref", default="TELEGRAM_BOT_TOKEN")
    return parser.parse_args()


def resolve_telegram_bot_id(explicit_id: int | None, token_secret_ref: str) -> int:
    if explicit_id is not None:
        return explicit_id

    token = os.getenv(token_secret_ref, "")
    prefix, separator, _ = token.partition(":")
    if not separator or not prefix.isdigit():
        raise SystemExit(
            "Unable to derive Telegram bot id. Provide --telegram-bot-id or configure "
            f"a valid token in {token_secret_ref}."
        )
    return int(prefix)


async def register(args: argparse.Namespace) -> None:
    telegram_bot_id = resolve_telegram_bot_id(args.telegram_bot_id, args.token_secret_ref)

    async with async_session_factory() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == args.tenant_slug))
        ).scalar_one_or_none()
        if tenant is None:
            if not args.tenant_name:
                raise SystemExit(
                    f"Tenant '{args.tenant_slug}' does not exist; pass --tenant-name to create it."
                )
            tenant = Tenant(name=args.tenant_name, slug=args.tenant_slug, is_active=True)
            session.add(tenant)
            await session.flush()

        bot = (
            await session.execute(
                select(Bot).where(Bot.telegram_bot_id == telegram_bot_id)
            )
        ).scalar_one_or_none()

        if bot is not None and bot.tenant_id != tenant.id:
            raise SystemExit(
                "Telegram bot id is already assigned to a different tenant; refusing reassignment."
            )

        if bot is None:
            bot = Bot(
                tenant_id=tenant.id,
                telegram_bot_id=telegram_bot_id,
                username=args.username.lstrip("@"),
                display_name=args.display_name,
                token_secret_ref=args.token_secret_ref,
                is_enabled=True,
            )
            session.add(bot)
        else:
            bot.username = args.username.lstrip("@")
            bot.display_name = args.display_name
            bot.token_secret_ref = args.token_secret_ref
            bot.is_enabled = True
            bot.deleted_at = None

        await session.commit()
        print(f"tenant_id={tenant.id}")
        print(f"bot_id={bot.id}")
        print(f"telegram_bot_id={bot.telegram_bot_id}")
        print(f"token_secret_ref={bot.token_secret_ref}")


def main() -> None:
    asyncio.run(register(parse_args()))


if __name__ == "__main__":
    main()
