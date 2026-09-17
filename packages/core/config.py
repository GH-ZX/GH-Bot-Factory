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
    owner_telegram_handle: str = Field(default="GH_Store", alias="OWNER_TELEGRAM_HANDLE")
    bot_runtime_reconcile_seconds: float = Field(default=5.0, alias="BOT_RUNTIME_RECONCILE_SECONDS", ge=1.0, le=300.0)
    bot_runtime_release_channels: str = Field(default="STABLE,CANARY", alias="BOT_RUNTIME_RELEASE_CHANNELS")
    factory_max_bots_per_tenant: int = Field(default=50, alias="FACTORY_MAX_BOTS_PER_TENANT", ge=1, le=10000)
    factory_max_enabled_bots_per_tenant: int = Field(default=20, alias="FACTORY_MAX_ENABLED_BOTS_PER_TENANT", ge=1, le=10000)
    factory_max_open_provisioning_jobs_per_tenant: int = Field(default=10, alias="FACTORY_MAX_OPEN_PROVISIONING_JOBS_PER_TENANT", ge=1, le=1000)
    local_secret_vault_enabled: bool = Field(default=True, alias="LOCAL_SECRET_VAULT_ENABLED")
    local_secret_vault_dir: str = Field(default="/var/lib/ghbf/secret-store", alias="LOCAL_SECRET_VAULT_DIR")
    setup_code: str | None = Field(default=None, alias="SETUP_CODE")
    platform_admin_token: str | None = Field(default=None, alias="PLATFORM_ADMIN_TOKEN")

    billing_provider: str = Field(default="disabled", alias="BILLING_PROVIDER")
    billing_success_url: str | None = Field(default=None, alias="BILLING_SUCCESS_URL")
    billing_cancel_url: str | None = Field(default=None, alias="BILLING_CANCEL_URL")
    billing_portal_return_url: str | None = Field(default=None, alias="BILLING_PORTAL_RETURN_URL")
    billing_past_due_grace_days: int = Field(default=7, alias="BILLING_PAST_DUE_GRACE_DAYS", ge=0, le=90)
    billing_webhook_tolerance_seconds: int = Field(default=300, alias="BILLING_WEBHOOK_TOLERANCE_SECONDS", ge=30, le=3600)
    billing_reconcile_enabled: bool = Field(default=True, alias="BILLING_RECONCILE_ENABLED")
    billing_reconcile_interval_seconds: int = Field(default=900, alias="BILLING_RECONCILE_INTERVAL_SECONDS", ge=60, le=86400)
    billing_reconcile_batch_size: int = Field(default=200, alias="BILLING_RECONCILE_BATCH_SIZE", ge=1, le=1000)
    billing_sync_stale_seconds: int = Field(default=86400, alias="BILLING_SYNC_STALE_SECONDS", ge=300, le=604800)
    stripe_secret_key: str | None = Field(default=None, alias="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str | None = Field(default=None, alias="STRIPE_WEBHOOK_SECRET")

    provider_http_allow_private_networks: bool = Field(
        default=False, alias="PROVIDER_HTTP_ALLOW_PRIVATE_NETWORKS"
    )
    provider_http_allowed_hosts: str = Field(default="", alias="PROVIDER_HTTP_ALLOWED_HOSTS")
    provider_offer_freshness_seconds: int = Field(
        default=300, alias="PROVIDER_OFFER_FRESHNESS_SECONDS", ge=30, le=86400
    )
    provider_reconcile_enabled: bool = Field(default=True, alias="PROVIDER_RECONCILE_ENABLED")
    provider_reconcile_interval_seconds: int = Field(
        default=30, alias="PROVIDER_RECONCILE_INTERVAL_SECONDS", ge=15, le=3600
    )
    provider_reconcile_batch_size: int = Field(
        default=200, alias="PROVIDER_RECONCILE_BATCH_SIZE", ge=1, le=1000
    )

    provider_balance_poll_enabled: bool = Field(default=True, alias="PROVIDER_BALANCE_POLL_ENABLED")
    provider_balance_poll_interval_seconds: int = Field(
        default=300, alias="PROVIDER_BALANCE_POLL_INTERVAL_SECONDS", ge=30, le=86400
    )
    provider_balance_poll_batch_size: int = Field(
        default=100, alias="PROVIDER_BALANCE_POLL_BATCH_SIZE", ge=1, le=1000
    )

    payment_provider_reconcile_enabled: bool = Field(
        default=True, alias="PAYMENT_PROVIDER_RECONCILE_ENABLED"
    )
    payment_provider_reconcile_interval_seconds: int = Field(
        default=30, alias="PAYMENT_PROVIDER_RECONCILE_INTERVAL_SECONDS", ge=15, le=3600
    )
    payment_provider_reconcile_batch_size: int = Field(
        default=100, alias="PAYMENT_PROVIDER_RECONCILE_BATCH_SIZE", ge=1, le=200
    )
    payment_operations_stale_seconds: int = Field(
        default=900, alias="PAYMENT_OPERATIONS_STALE_SECONDS", ge=60, le=86400
    )

    payment_onchain_reconcile_enabled: bool = Field(
        default=True, alias="PAYMENT_ONCHAIN_RECONCILE_ENABLED"
    )
    payment_onchain_reconcile_interval_seconds: int = Field(
        default=30, alias="PAYMENT_ONCHAIN_RECONCILE_INTERVAL_SECONDS", ge=15, le=3600
    )
    payment_onchain_reconcile_batch_size: int = Field(
        default=100, alias="PAYMENT_ONCHAIN_RECONCILE_BATCH_SIZE", ge=1, le=1000
    )

    # Installation-owned chain verifier configuration. These values are never tenant-controlled.
    payment_tron_usdt_enabled: bool = Field(default=False, alias="PAYMENT_TRON_USDT_ENABLED")
    payment_trongrid_base_url: str = Field(
        default="https://api.trongrid.io", alias="PAYMENT_TRONGRID_BASE_URL"
    )
    payment_trongrid_api_key: str | None = Field(default=None, alias="PAYMENT_TRONGRID_API_KEY")
    payment_tron_timeout_seconds: float = Field(
        default=12.0, alias="PAYMENT_TRON_TIMEOUT_SECONDS", ge=2.0, le=30.0
    )

    # Generic BSC/EVM token verification. No token symbol is trusted implicitly: the exact
    # contract, decimals and internal asset identity must be configured by the installation.
    payment_bsc_token_enabled: bool = Field(default=False, alias="PAYMENT_BSC_TOKEN_ENABLED")
    payment_bsc_rpc_url: str | None = Field(default=None, alias="PAYMENT_BSC_RPC_URL")
    payment_bsc_token_asset: str | None = Field(default=None, alias="PAYMENT_BSC_TOKEN_ASSET")
    payment_bsc_token_contract: str | None = Field(
        default=None, alias="PAYMENT_BSC_TOKEN_CONTRACT"
    )
    payment_bsc_token_decimals: int = Field(
        default=18, alias="PAYMENT_BSC_TOKEN_DECIMALS", ge=0, le=36
    )
    payment_bsc_chain_id: int = Field(default=56, alias="PAYMENT_BSC_CHAIN_ID", ge=1)
    payment_bsc_timeout_seconds: float = Field(
        default=12.0, alias="PAYMENT_BSC_TIMEOUT_SECONDS", ge=2.0, le=30.0
    )

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

    @field_validator("billing_provider")
    @classmethod
    def validate_billing_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"disabled", "stripe"}:
            raise ValueError("BILLING_PROVIDER must be 'disabled' or 'stripe'")
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
        if self.payment_tron_usdt_enabled:
            if not self.payment_trongrid_base_url.startswith("https://"):
                raise ValueError("PAYMENT_TRONGRID_BASE_URL must use HTTPS in production-like environments")
            if not self.payment_trongrid_api_key or not self.payment_trongrid_api_key.strip():
                raise ValueError("PAYMENT_TRONGRID_API_KEY is required when TRON payments are enabled in production-like environments")
        if self.payment_bsc_token_enabled:
            if not self.payment_bsc_rpc_url or not self.payment_bsc_rpc_url.startswith("https://"):
                raise ValueError("PAYMENT_BSC_RPC_URL must use HTTPS when BSC token payments are enabled in production-like environments")
            if not self.payment_bsc_token_asset or not self.payment_bsc_token_asset.strip():
                raise ValueError("PAYMENT_BSC_TOKEN_ASSET is required when BSC token payments are enabled")
            if not self.payment_bsc_token_contract or not self.payment_bsc_token_contract.strip():
                raise ValueError("PAYMENT_BSC_TOKEN_CONTRACT is required when BSC token payments are enabled")
        for field_name, value in (
            ("BILLING_SUCCESS_URL", self.billing_success_url),
            ("BILLING_CANCEL_URL", self.billing_cancel_url),
            ("BILLING_PORTAL_RETURN_URL", self.billing_portal_return_url),
        ):
            if value and not value.startswith("https://"):
                raise ValueError(f"{field_name} must use HTTPS in production-like environments")
        if self.billing_provider == "stripe":
            if not self.stripe_secret_key or not self.stripe_secret_key.strip():
                raise ValueError("Stripe billing requires STRIPE_SECRET_KEY")
            if not self.stripe_webhook_secret or not self.stripe_webhook_secret.strip():
                raise ValueError("Stripe billing requires STRIPE_WEBHOOK_SECRET")
        return self

    @property
    def provider_http_allowed_host_set(self) -> tuple[str, ...]:
        return tuple(
            item.strip().lower()
            for item in self.provider_http_allowed_hosts.split(",")
            if item.strip()
        )

    @property
    def bot_runtime_release_channel_set(self) -> set[str]:
        values = {item.strip().upper() for item in self.bot_runtime_release_channels.split(",") if item.strip()}
        return values or {"STABLE"}

    @property
    def is_production_like(self) -> bool:
        return self.app_env in {"production", "staging"}


settings = Settings()
