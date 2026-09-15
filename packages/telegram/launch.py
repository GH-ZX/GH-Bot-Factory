from __future__ import annotations

import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _build_webapp_url(public_url: str, bot_id: uuid.UUID, *, setting_name: str) -> str:
    raw_url = public_url.strip()
    if not raw_url:
        raise ValueError(f"{setting_name} cannot be empty when Web App launch is enabled.")

    parsed = urlsplit(raw_url)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"{setting_name} must use HTTPS for Telegram WebAppInfo.")
    if not parsed.netloc:
        raise ValueError(f"{setting_name} must include a public hostname.")
    if parsed.username or parsed.password:
        raise ValueError(f"{setting_name} must not contain URL credentials.")

    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "bot_id"
    ]
    query.append(("bot_id", str(bot_id)))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", urlencode(query), ""))



def resolve_tenant_public_url(tenant_settings: dict | None, *, kind: str, fallback: str | None) -> str | None:
    """Resolve per-tenant Mini App/Admin URL with a process-level fallback."""
    key = "miniapp_public_url" if kind == "miniapp" else "admin_public_url"
    value = (tenant_settings or {}).get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def build_miniapp_url(public_url: str, bot_id: uuid.UUID) -> str:
    """Build a tenant-safe Telegram Mini App URL for one bot instance."""
    return _build_webapp_url(public_url, bot_id, setting_name="MINIAPP_PUBLIC_URL")


def build_admin_url(public_url: str, bot_id: uuid.UUID) -> str:
    """Build the privileged Telegram Admin Web App URL for one bot instance."""
    return _build_webapp_url(public_url, bot_id, setting_name="ADMIN_PUBLIC_URL")


async def configure_bot_menu_button(
    bot_client: object,
    *,
    public_url: str | None,
    bot_id: uuid.UUID,
    menu_text: str = "Open Store",
) -> bool:
    """Configure Telegram's persistent private-chat menu button when enabled."""
    if not public_url:
        return False

    # Imported lazily so URL validation remains unit-testable without aiogram.
    from aiogram.types import MenuButtonWebApp, WebAppInfo

    url = build_miniapp_url(public_url, bot_id)
    await bot_client.set_chat_menu_button(  # type: ignore[attr-defined]
        menu_button=MenuButtonWebApp(
            text=menu_text,
            web_app=WebAppInfo(url=url),
        )
    )
    return True
