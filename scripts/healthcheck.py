#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys

from packages.core.config import settings
from packages.core.health import check_database, check_redis
from packages.core.heartbeat import heartbeat_key


async def _heartbeat_ok(service: str) -> tuple[bool, str]:
    from redis.asyncio import Redis

    client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    try:
        value = await client.get(heartbeat_key(service))
        return value == "alive", "ok" if value == "alive" else "missing"
    except Exception as exc:
        return False, type(exc).__name__
    finally:
        await client.aclose()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=["api-deps", "worker", "bot-runtime"])
    args = parser.parse_args()
    database, redis = await asyncio.gather(check_database(), check_redis())
    if not database[0] or not redis[0]:
        print(f"unhealthy database={database[1]} redis={redis[1]}", file=sys.stderr)
        return 1
    if args.target != "api-deps":
        heartbeat = await _heartbeat_ok(args.target)
        if not heartbeat[0]:
            print(f"unhealthy heartbeat={heartbeat[1]}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
