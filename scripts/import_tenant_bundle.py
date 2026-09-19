#!/usr/bin/env python3
"""Offline restore into an empty migrated destination, never an existing installation."""
import argparse
import asyncio
import getpass
import json
from pathlib import Path

from packages.core.database import async_session_factory
from packages.marketplace.tenant_bundle import MAX_BYTES, BundleError, decrypt, restore
from packages.telegram.secrets import EncryptedFileSecretStorage


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--confirm-empty-offline-destination", action="store_true", required=True)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--confirm-source-stopped", action="store_true")
    parser.add_argument("--database-url", help="Destination database URL (e.g. Supabase postgresql+asyncpg://...)")
    args = parser.parse_args()
    if args.activate and not args.confirm_source_stopped:
        parser.error("Activation requires --confirm-source-stopped after verifying source runtime shutdown.")
    if args.bundle.stat().st_size > MAX_BYTES:
        parser.error("Bundle exceeds the supported size.")
    payload = decrypt(args.bundle.read_bytes(), getpass.getpass("Bundle passphrase: "))
    if args.database_url:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from packages.core.database import get_async_engine_connect_args
        target_engine = create_async_engine(
            args.database_url,
            connect_args=get_async_engine_connect_args(args.database_url),
        )
        session_maker = async_sessionmaker(bind=target_engine, class_=AsyncSession, expire_on_commit=False)
    else:
        session_maker = async_session_factory

    async with session_maker() as session:
        result = await restore(session, payload, EncryptedFileSecretStorage(), activate=args.activate)
    print(json.dumps(result, indent=2))
    print("Restore complete. Destination environment remains locally owned. Start application services only after validation.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BundleError as exc:
        raise SystemExit(str(exc)) from None
