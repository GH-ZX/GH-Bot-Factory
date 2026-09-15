from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    app_name: str = Field(default="gh-bot-factory", alias="APP_NAME")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=True, alias="LOG_JSON")

    database_url: str = Field(
        default="sqlite+aiosqlite:///./test_factory.db",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    secret_key: str | None = Field(default=None, alias="SECRET_KEY")
    jwt_secret_key: str | None = Field(default=None, alias="JWT_SECRET_KEY")
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    miniapp_public_url: str | None = Field(default=None, alias="MINIAPP_PUBLIC_URL")
    admin_public_url: str | None = Field(default=None, alias="ADMIN_PUBLIC_URL")
    miniapp_menu_text: str = Field(default="Open Store", alias="MINIAPP_MENU_TEXT")
    bot_runtime_reconcile_seconds: float = Field(default=5.0, alias="BOT_RUNTIME_RECONCILE_SECONDS", ge=1.0, le=300.0)
    bot_runtime_release_channels: str = Field(default="STABLE,CANARY", alias="BOT_RUNTIME_RELEASE_CHANNELS")
    factory_max_bots_per_tenant: int = Field(default=50, alias="FACTORY_MAX_BOTS_PER_TENANT", ge=1, le=10000)
    factory_max_enabled_bots_per_tenant: int = Field(default=20, alias="FACTORY_MAX_ENABLED_BOTS_PER_TENANT", ge=1, le=10000)
    factory_max_open_provisioning_jobs_per_tenant: int = Field(default=10, alias="FACTORY_MAX_OPEN_PROVISIONING_JOBS_PER_TENANT", ge=1, le=1000)
    local_secret_vault_enabled: bool = Field(default=True, alias="LOCAL_SECRET_VAULT_ENABLED")
    local_secret_vault_dir: str = Field(default="/var/lib/ghbf/secret-store", alias="LOCAL_SECRET_VAULT_DIR")
    setup_code: str | None = Field(default=None, alias="SETUP_CODE")

    max_request_body_bytes: int = Field(default=1_048_576, alias="MAX_REQUEST_BODY_BYTES")
    health_dependency_timeout_seconds: float = Field(default=2.0, alias="HEALTH_DEPENDENCY_TIMEOUT_SECONDS")
    rate_limit_enabled: bool = Field(default=False, alias="RATE_LIMIT_ENABLED")
    rate_limit_backend: str = Field(default="memory", alias="RATE_LIMIT_BACKEND")
    rate_limit_auth_per_minute: int = Field(default=30, alias="RATE_LIMIT_AUTH_PER_MINUTE")
    rate_limit_money_per_minute: int = Field(default=20, alias="RATE_LIMIT_MONEY_PER_MINUTE")
    rate_limit_admin_write_per_minute: int = Field(default=60, alias="RATE_LIMIT_ADMIN_WRITE_PER_MINUTE")

    @field_validator("app_env")
    @classmethod
    def normalize_app_env(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("rate_limit_backend")
    @classmethod
    def validate_rate_limit_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"memory", "redis"}:
            raise ValueError("RATE_LIMIT_BACKEND must be 'memory' or 'redis'")
        return normalized

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        if self.app_env not in {"production", "staging"}:
            return self
        if not self.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("Production-like environments require PostgreSQL via asyncpg")
        if not self.redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("Production-like environments require Redis")
        if not self.jwt_secret_key or len(self.jwt_secret_key.encode("utf-8")) < 32:
            raise ValueError("Production-like environments require JWT_SECRET_KEY >= 32 bytes")
        if not self.rate_limit_enabled or self.rate_limit_backend != "redis":
            raise ValueError("Production-like environments require Redis-backed rate limiting")
        if self.miniapp_public_url and not self.miniapp_public_url.startswith("https://"):
            raise ValueError("MINIAPP_PUBLIC_URL must use HTTPS in production-like environments")
        if self.admin_public_url and not self.admin_public_url.startswith("https://"):
            raise ValueError("ADMIN_PUBLIC_URL must use HTTPS in production-like environments")
        return self

    @property
    def bot_runtime_release_channel_set(self) -> set[str]:
        values = {item.strip().upper() for item in self.bot_runtime_release_channels.split(",") if item.strip()}
        return values or {"STABLE"}

    @property
    def is_production_like(self) -> bool:
        return self.app_env in {"production", "staging"}


settings = Settings()
