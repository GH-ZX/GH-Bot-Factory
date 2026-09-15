import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TenantContext:
    """Explicit update context propagated through all Telegram middlewares and handlers.
    
    Prevents relying on global mutable state and guarantees strict tenant scoping.
    """

    bot_id: uuid.UUID
    tenant_id: uuid.UUID
    telegram_bot_id: int
    telegram_user_id: int
    user_id: uuid.UUID | None
    correlation_id: str
    tenant_name: str
    display_name: str
    bot_username: str | None = None
    language_code: str | None = "en"
    config: dict[str, Any] = field(default_factory=dict)
    tenant_settings: dict[str, Any] = field(default_factory=dict)

    @property
    def currency(self) -> str:
        return self.config.get("currency", "USD")

    @property
    def locale(self) -> str:
        return self.config.get("locale", "en")

    def is_module_enabled(self, module_name: str) -> bool:
        enabled_modules = self.config.get("enabled_modules", ["catalog", "orders", "account"])
        return module_name in enabled_modules

    def get_branding(self, key: str, default: str = "") -> str:
        branding = self.config.get("branding", {})
        return branding.get(key, default)
