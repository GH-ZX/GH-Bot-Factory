"""Encrypted, schema-bound, single-tenant snapshots for offline destination restore."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import Date, DateTime, Numeric, Uuid, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models import Base
from packages.telegram.secrets import SecretNotFoundError, SecretStorage

FORMAT = "ghbf-tenant-encrypted-v2"
MAX_BYTES = 64 * 1024 * 1024
EXCLUDED = {
    "customer_inquiries", "commercial_quotes", "commercial_quote_lines", "deployment_handoffs",
    "platform_audit_logs", "integration_offerings", "tenant_integration_entitlements",
    "saas_plans", "saas_plan_prices", "tenant_subscriptions", "billing_events", "system_install_state",
}
# Adding a new table requires an explicit portability review.
INCLUDED = {"support_cases", "support_messages", "coupons", "coupon_redemptions", "announcements", "announcement_deliveries", "tenants", "users", "memberships", "audit_logs", "bots", "categories", "products", "product_variants", "orders", "order_items", "wallets", "ledger_transactions", "payment_intents", "payment_transactions", "wallet_topup_reversals", "payment_reconciliation_events", "financial_resolution_cases", "payment_method_configs", "payment_quotes", "payment_observations", "payment_provider_configs", "payment_webhook_events", "asset_wallets", "asset_ledger_transactions", "fx_policies", "flexible_deposit_sessions", "wallet_holds", "pricing_tiers", "pricing_rules", "user_pricing_tiers", "commerce_price_quotes", "order_item_economics", "providers", "provider_credentials", "provider_product_mappings", "provider_routing_policies", "provider_offer_snapshots", "provider_balance_snapshots", "tenant_telegram_users", "bot_provisioning_jobs", "fulfillment_jobs", "fulfillment_attempts"}
SECRET_COLUMNS = {
    "bots": ("token_secret_ref",), "provider_credentials": ("secret_ref",),
    "payment_provider_configs": ("credentials_ref", "webhook_secret_ref"),
    "bot_provisioning_jobs": ("token_secret_ref",),
}


class BundleError(ValueError):
    pass


def tables():
    if set(Base.metadata.tables) != INCLUDED | EXCLUDED:
        raise BundleError("Database schema needs a portability review before export or import.")
    return [t for t in Base.metadata.sorted_tables if t.name in INCLUDED]


def schema_fingerprint():
    description = [(t.name, [(c.name, str(c.type), c.nullable) for c in t.columns]) for t in tables()]
    return hashlib.sha256(json.dumps(description, sort_keys=True).encode()).hexdigest()


def json_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError("Unsupported snapshot value")


def _cipher(passphrase: str, salt: bytes):
    if not 16 <= len(passphrase) <= 1024:
        raise BundleError("Use an export passphrase of 16 to 1024 characters.")
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(payload, passphrase):
    raw = json.dumps(payload, default=json_value, sort_keys=True).encode()
    if len(raw) > MAX_BYTES // 2:
        raise BundleError("Tenant snapshot exceeds the supported offline bundle size.")
    salt = os.urandom(16)
    return json.dumps({"format": FORMAT, "salt": base64.b64encode(salt).decode(),
                       "ciphertext": _cipher(passphrase, salt).encrypt(raw).decode()}).encode()


def decrypt(raw: bytes, passphrase: str):
    if len(raw) > MAX_BYTES:
        raise BundleError("Bundle exceeds the supported size.")
    try:
        envelope = json.loads(raw)
        if envelope["format"] != FORMAT:
            raise BundleError("Unsupported bundle format; plaintext legacy exports cannot be imported.")
        salt = base64.b64decode(envelope["salt"], validate=True)
        if len(salt) != 16:
            raise ValueError("salt")
        payload = json.loads(_cipher(passphrase, salt).decrypt(envelope["ciphertext"].encode()))
    except (ValueError, KeyError, TypeError, AttributeError, InvalidToken) as exc:
        raise BundleError("Cannot decrypt bundle: incorrect passphrase, damaged file, or unsupported format.") from exc
    validate(payload)
    return payload


def validate(payload):
    if not isinstance(payload, dict) or payload.get("format") != FORMAT or payload.get("schema") != schema_fingerprint():
        raise BundleError("Bundle format/schema does not match this application version.")
    rows = payload.get("tables", {})
    if set(rows) != INCLUDED or len(rows["tenants"]) != 1:
        raise BundleError("Bundle must contain exactly one tenant and the complete tenant table set.")
    tenant_id = rows["tenants"][0]["id"]
    if tenant_id != payload.get("tenant_id"):
        raise BundleError("Tenant identity mismatch.")
    indexes = {}
    required_secrets = set()
    for table in tables():
        seen = set()
        for row in rows[table.name]:
            if set(row) != set(table.c.keys()):
                raise BundleError(f"Incomplete or unknown columns in {table.name}.")
            if "tenant_id" in row and row["tenant_id"] != tenant_id:
                raise BundleError("Cross-tenant row in bundle.")
            if row["id"] in seen:
                raise BundleError("Duplicate identity in bundle.")
            seen.add(row["id"])
            for column in SECRET_COLUMNS.get(table.name, ()):
                if row[column]:
                    required_secrets.add(row[column])
        indexes[table.name] = seen
    for table in tables():
        for row in rows[table.name]:
            for fk in table.foreign_keys:
                value = row[fk.parent.name]
                if value is not None and value not in indexes.get(fk.column.table.name, set()):
                    raise BundleError(f"Unresolved relationship in {table.name}; export cannot cross tenant boundaries.")
    secrets = payload.get("secrets", {})
    if set(secrets) != required_secrets or any(not isinstance(v, str) or not v for v in secrets.values()):
        raise BundleError("Bundle secret set is incomplete or contains unrelated entries.")


async def snapshot(session: AsyncSession, tenant_id: uuid.UUID, storage: SecretStorage):
    data = {}
    for table in tables():
        if table.name == "users":
            continue
        if table.name == "tenants":
            condition = table.c.id == tenant_id
        elif table.name == "product_variants":
            parent = Base.metadata.tables["products"]
            condition = table.c.product_id.in_(select(parent.c.id).where(parent.c.tenant_id == tenant_id))
        elif table.name == "order_items":
            parent = Base.metadata.tables["orders"]
            condition = table.c.order_id.in_(select(parent.c.id).where(parent.c.tenant_id == tenant_id))
        else:
            condition = table.c.tenant_id == tenant_id
        data[table.name] = [dict(row) for row in (await session.execute(select(table).where(condition))).mappings()]
    if len(data["tenants"]) != 1:
        raise BundleError("Tenant not found.")
    user_ids = set()
    for table in tables():
        for fk in table.foreign_keys:
            if fk.column.table.name == "users":
                user_ids.update(row[fk.parent.name] for row in data.get(table.name, []) if row[fk.parent.name])
    user_table = Base.metadata.tables["users"]
    data["users"] = [dict(row) for row in (await session.execute(select(user_table).where(user_table.c.id.in_(user_ids)))).mappings()]
    for row in data["users"]:
        # Global login credentials and profile details are not tenant-owned.
        for key in ("hashed_password", "email", "first_name", "last_name"):
            row[key] = None
        row["token_version"] += 1
    secret_values = {}
    for name, columns in SECRET_COLUMNS.items():
        for row in data[name]:
            for column in columns:
                ref = row[column]
                if ref and ref not in secret_values:
                    try:
                        secret_values[ref] = await storage.get_secret(ref)
                    except SecretNotFoundError as exc:
                        raise BundleError(f"A required {name} credential is missing; repair it before export.") from exc
    payload = json.loads(json.dumps({"format": FORMAT, "schema": schema_fingerprint(),
        "tenant_id": str(tenant_id), "tables": data, "secrets": secret_values}, default=json_value))
    validate(payload)
    return payload


def _typed(table, row):
    result = dict(row)
    for col in table.columns:
        value = result[col.name]
        if value is None:
            continue
        if isinstance(col.type, Uuid):
            result[col.name] = uuid.UUID(value)
        elif isinstance(col.type, DateTime):
            result[col.name] = datetime.fromisoformat(value)
        elif isinstance(col.type, Date):
            result[col.name] = date.fromisoformat(value)
        elif isinstance(col.type, Numeric):
            result[col.name] = Decimal(value)
    return result


async def restore(session: AsyncSession, payload, storage: SecretStorage, *, activate: bool = False):
    """Restore only to an empty, offline destination. Caller controls commit/startup."""
    validate(payload)
    for table in Base.metadata.sorted_tables:
        if await session.scalar(select(func.count()).select_from(table)):
            raise BundleError("Restore requires an empty migrated database; existing installations are never overwritten.")
    data = json.loads(json.dumps(payload["tables"]))
    created_refs = []
    namespace = uuid.uuid4().hex
    try:
        for name, columns in SECRET_COLUMNS.items():
            for row in data[name]:
                for column in columns:
                    old = row[column]
                    if old:
                        ref = f"GHBF_IMPORT_{namespace}_{row['id'].replace('-', '')}_{column}"
                        created_refs.append(ref)
                        await storage.set_secret(ref, payload["secrets"][old])
                        row[column] = ref
        # Public URLs, source sessions, and polling ownership do not carry over implicitly.
        for row in data["tenants"]:
            row["is_active"] = activate and row["is_active"]
            for key in ("admin_public_url", "miniapp_public_url"):
                row["settings"].pop(key, None)
        for row in data["bots"]:
            row["is_enabled"] = activate and row["is_enabled"]
            row["runtime_revision"] += 1
        # A destination must never resume broadcasts implicitly after a restore.
        for row in data["announcements"]:
            if row["status"] == "QUEUED":
                row["status"] = "CANCELLED"
        for row in data["announcement_deliveries"]:
            if row["status"] in {"QUEUED", "RUNNING"}:
                row["status"] = "UNKNOWN" if row["status"] == "RUNNING" else "CANCELLED"
                row["error_code"] = "DESTINATION_RESTORE"
        for table in tables():
            pending = list(data[table.name])
            inserted = set()
            self_keys = [fk.parent.name for fk in table.foreign_keys if fk.column.table is table]
            while pending:
                ready = [r for r in pending if all(r[k] is None or r[k] in inserted for k in self_keys)]
                if not ready:
                    raise BundleError("Cyclic self-reference in tenant snapshot.")
                await session.execute(insert(table), [_typed(table, row) for row in ready])
                inserted.update(r["id"] for r in ready)
                pending = [r for r in pending if r["id"] not in inserted]
        install = Base.metadata.tables["system_install_state"]
        await session.execute(insert(install).values(id=1, is_initialized=True,
            initialized_at=datetime.now(UTC), tenant_id=uuid.UUID(payload["tenant_id"])))
        await session.commit()
    except Exception:
        await session.rollback()
        for ref in created_refs:
            await storage.delete_secret(ref)
        raise
    return {"tenant_id": payload["tenant_id"], "active": activate,
            "row_counts": {name: len(rows) for name, rows in data.items()}}


def write_private(path: Path, content: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
