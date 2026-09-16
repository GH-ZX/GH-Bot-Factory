#!/usr/bin/env python3
"""Local-first CLI for the installation-level GH Bot Factory control plane.

The CLI reads PLATFORM_ADMIN_TOKEN from .env and talks only to the configured
localhost API port by default. It is suitable for laptop hosting and later VPS/SSH
operation without exposing a platform administration UI publicly.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def request_json(
    method: str,
    path: str,
    *,
    token: str,
    port: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    url = f"http://127.0.0.1:{port}/api/v1/platform{path}"
    data = None
    headers = {"X-GHBF-Platform-Token": token, "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Platform API returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach local GHBF API: {exc.reason}") from exc


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="GH Bot Factory local platform control plane")
    sub = root.add_subparsers(dest="command", required=True)

    sub.add_parser("overview")
    sub.add_parser("plans")

    create = sub.add_parser("plan-create")
    create.add_argument("--key", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--description")
    create.add_argument("--max-bots", type=int, required=True)
    create.add_argument("--max-enabled-bots", type=int, required=True)
    create.add_argument("--max-open-jobs", type=int, required=True)
    create.add_argument("--public", action="store_true")

    update = sub.add_parser("plan-update")
    update.add_argument("--id", required=True)
    update.add_argument("--name")
    update.add_argument("--description")
    update.add_argument("--retire", action="store_true")
    update.add_argument("--private", action="store_true")

    prices = sub.add_parser("plan-prices")
    prices.add_argument("--plan-id", required=True)

    price_add = sub.add_parser("plan-price-add")
    price_add.add_argument("--plan-id", required=True)
    price_add.add_argument("--provider", default="stripe")
    price_add.add_argument("--external-price-id", required=True)
    price_add.add_argument("--currency", required=True)
    price_add.add_argument("--amount-minor", type=int, required=True)
    price_add.add_argument("--interval", choices=["MONTH", "YEAR"], required=True)
    price_add.add_argument("--interval-count", type=int, default=1)

    price_retire = sub.add_parser("plan-price-retire")
    price_retire.add_argument("--price-id", required=True)

    reconcile = sub.add_parser("billing-reconcile")
    reconcile.add_argument("--provider")
    reconcile.add_argument("--limit", type=int, default=200)

    tenants = sub.add_parser("tenants")
    tenants.add_argument("--q")

    sub.add_parser("saas-health")
    inspect = sub.add_parser("tenant-entitlements")
    inspect.add_argument("--tenant", required=True)

    set_sub = sub.add_parser("subscription-set")
    set_sub.add_argument("--tenant", required=True)
    set_sub.add_argument("--plan", required=True)
    set_sub.add_argument(
        "--status",
        default="ACTIVE",
        choices=["TRIALING", "ACTIVE", "PAST_DUE", "PAUSED", "CANCELED"],
    )

    clear = sub.add_parser("subscription-clear")
    clear.add_argument("--tenant", required=True)

    events = sub.add_parser("billing-events")
    events.add_argument("--tenant")

    audit = sub.add_parser("audit")
    audit.add_argument("--tenant")
    return root


def main() -> None:
    args = parser().parse_args()
    env = read_env()
    token = env.get("PLATFORM_ADMIN_TOKEN", "").strip()
    if len(token.encode("utf-8")) < 32:
        raise SystemExit(
            "PLATFORM_ADMIN_TOKEN is missing/weak in .env. Run scripts/easy_start.py or configure it explicitly."
        )
    port = env.get("API_HOST_PORT", "8010")

    if args.command == "overview":
        result = request_json("GET", "/overview", token=token, port=port)
    elif args.command == "plans":
        result = request_json("GET", "/plans", token=token, port=port)
    elif args.command == "plan-create":
        result = request_json(
            "POST",
            "/plans",
            token=token,
            port=port,
            payload={
                "key": args.key,
                "name": args.name,
                "description": args.description,
                "is_active": True,
                "is_public": args.public,
                "entitlements": {
                    "max_bots": args.max_bots,
                    "max_enabled_bots": args.max_enabled_bots,
                    "max_open_provisioning_jobs": args.max_open_jobs,
                    "features": {},
                },
                "metadata": {},
            },
        )
    elif args.command == "plan-update":
        payload: dict[str, Any] = {}
        if args.name is not None:
            payload["name"] = args.name
        if args.description is not None:
            payload["description"] = args.description
        if args.retire:
            payload["is_active"] = False
            payload["is_public"] = False
        if args.private:
            payload["is_public"] = False
        if not payload:
            raise SystemExit("plan-update requires at least one change flag.")
        result = request_json("PATCH", f"/plans/{args.id}", token=token, port=port, payload=payload)
    elif args.command == "plan-prices":
        result = request_json("GET", f"/plans/{args.plan_id}/prices", token=token, port=port)
    elif args.command == "plan-price-add":
        result = request_json(
            "POST",
            f"/plans/{args.plan_id}/prices",
            token=token,
            port=port,
            payload={
                "provider": args.provider,
                "external_price_id": args.external_price_id,
                "currency": args.currency,
                "unit_amount_minor": args.amount_minor,
                "interval": args.interval,
                "interval_count": args.interval_count,
                "is_active": True,
                "metadata": {},
            },
        )
    elif args.command == "plan-price-retire":
        result = request_json(
            "PATCH",
            f"/prices/{args.price_id}",
            token=token,
            port=port,
            payload={"is_active": False},
        )
    elif args.command == "billing-reconcile":
        params = {"limit": args.limit}
        if args.provider:
            params["provider"] = args.provider
        result = request_json(
            "POST",
            "/billing/reconcile?" + urllib.parse.urlencode(params),
            token=token,
            port=port,
        )
    elif args.command == "tenants":
        query = ""
        if args.q:
            query = "?" + urllib.parse.urlencode({"q": args.q})
        result = request_json("GET", f"/tenants{query}", token=token, port=port)
    elif args.command == "saas-health":
        result = request_json("GET", "/operations/saas-health", token=token, port=port)
    elif args.command == "tenant-entitlements":
        result = request_json("GET", f"/tenants/{args.tenant}/entitlements", token=token, port=port)
    elif args.command == "subscription-set":
        result = request_json(
            "PUT",
            f"/tenants/{args.tenant}/subscription",
            token=token,
            port=port,
            payload={"plan_key": args.plan, "status": args.status},
        )
    elif args.command == "subscription-clear":
        result = request_json("DELETE", f"/tenants/{args.tenant}/subscription", token=token, port=port)
    elif args.command == "billing-events":
        query = "?" + urllib.parse.urlencode({"tenant_id": args.tenant}) if args.tenant else ""
        result = request_json("GET", f"/billing-events{query}", token=token, port=port)
    elif args.command == "audit":
        query = "?" + urllib.parse.urlencode({"tenant_id": args.tenant}) if args.tenant else ""
        result = request_json("GET", f"/audit{query}", token=token, port=port)
    else:  # pragma: no cover
        raise SystemExit(f"Unsupported command: {args.command}")

    if result is not None:
        print_json(result)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
