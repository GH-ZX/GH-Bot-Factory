"""Bootstrap the first tenant, OWNER membership, and control Telegram bot safely.

This is an operator-only bridge into the normal Bot Factory workflow. It verifies the
Telegram token with getMe, stores only the secret reference, and is idempotent for
the same tenant / Telegram identities.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from packages.core.database import async_session_factory
from packages.factory.provisioning import (
    AiogramTelegramIdentityVerifier,
    normalize_expected_username,
    validate_secret_ref,
)
from packages.factory.templates import build_template_config
from packages.telegram.models import Bot
from packages.telegram.secrets import EnvSecretStorage, SecretNotFoundError
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--owner-telegram-id", required=True, type=int)
    parser.add_argument("--owner-username")
    parser.add_argument("--token-secret-ref", required=True)
    parser.add_argument("--expected-bot-username")
    parser.add_argument("--bot-display-name", required=True)
    parser.add_argument("--template-key", default="general-commerce")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--locale", default="en")
    parser.add_argument("--support-contact", default="")
    return parser.parse_args()


async def bootstrap(args: argparse.Namespace) -> None:
    try:
        secret_ref = validate_secret_ref(args.token_secret_ref)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    secret_storage = EnvSecretStorage()
    try:
        token = await secret_storage.get_secret(secret_ref)
    except SecretNotFoundError as exc:
        raise SystemExit(f"Secret reference {secret_ref!r} is not configured in the process environment.") from exc

    identity = await AiogramTelegramIdentityVerifier().verify(token)
    expected_username = normalize_expected_username(args.expected_bot_username)
    actual_username = normalize_expected_username(identity.username)
    if expected_username and expected_username != actual_username:
        raise SystemExit(
            f"Verified Telegram bot username @{actual_username or 'unknown'} does not match "
            f"expected @{expected_username}."
        )

    config = build_template_config(
        template_key=args.template_key,
        currency=args.currency,
        locale=args.locale,
        branding={
            "support_contact": args.support_contact,
        },
    )

    async with async_session_factory() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == args.tenant_slug))
        ).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name=args.tenant_name, slug=args.tenant_slug, is_active=True)
            session.add(tenant)
            await session.flush()
        elif tenant.deleted_at is not None:
            raise SystemExit("Refusing to bootstrap into a deleted tenant.")
        else:
            tenant.name = args.tenant_name
            tenant.is_active = True

        owner = (
            await session.execute(select(User).where(User.telegram_id == args.owner_telegram_id))
        ).scalar_one_or_none()
        if owner is None:
            owner = User(
                telegram_id=args.owner_telegram_id,
                username=args.owner_username.lstrip("@") if args.owner_username else None,
                is_active=True,
            )
            session.add(owner)
            await session.flush()
        else:
            owner.is_active = True
            if args.owner_username:
                owner.username = args.owner_username.lstrip("@")

        membership = (
            await session.execute(
                select(Membership).where(
                    Membership.tenant_id == tenant.id,
                    Membership.user_id == owner.id,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            membership = Membership(
                tenant_id=tenant.id,
                user_id=owner.id,
                role=Role.OWNER,
                permissions=[],
                is_active=True,
            )
            session.add(membership)
        else:
            membership.role = Role.OWNER
            membership.is_active = True

        bot = (
            await session.execute(
                select(Bot).where(Bot.telegram_bot_id == identity.telegram_bot_id)
            )
        ).scalar_one_or_none()
        if bot is not None and bot.tenant_id != tenant.id:
            raise SystemExit("Verified Telegram bot is already owned by a different tenant.")
        if bot is None:
            bot = Bot(
                tenant_id=tenant.id,
                telegram_bot_id=identity.telegram_bot_id,
                username=identity.username,
                display_name=args.bot_display_name,
                token_secret_ref=secret_ref,
                is_enabled=True,
                config=config,
            )
            session.add(bot)
            await session.flush()
        else:
            bot.username = identity.username
            bot.display_name = args.bot_display_name
            bot.token_secret_ref = secret_ref
            bot.is_enabled = True
            bot.config = config
            bot.deleted_at = None

        session.add(
            AuditLog(
                tenant_id=tenant.id,
                user_id=owner.id,
                action="FIRST_TENANT_BOOTSTRAPPED",
                resource_type="BOT",
                resource_id=str(bot.id),
                details={
                    "telegram_bot_id": identity.telegram_bot_id,
                    "username": identity.username,
                    "template_key": args.template_key,
                },
            )
        )
        await session.commit()

        print("Bootstrap complete. No token material was persisted or printed.")
        print(f"tenant_id={tenant.id}")
        print(f"owner_user_id={owner.id}")
        print(f"bot_id={bot.id}")
        print(f"telegram_bot_id={identity.telegram_bot_id}")
        print(f"telegram_username=@{identity.username}" if identity.username else "telegram_username=<none>")


def main() -> None:
    asyncio.run(bootstrap(parse_args()))


if __name__ == "__main__":
    main()
