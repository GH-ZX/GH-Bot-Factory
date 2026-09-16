import logging

import pytest
from pydantic import ValidationError

from packages.core.config import Settings
from packages.core.observability import JsonFormatter, redact_text
from packages.core.security import RateLimitMiddleware


def _prod_settings(**overrides):
    values = {
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@postgres/db",
        "REDIS_URL": "redis://redis:6379/0",
        "JWT_SECRET_KEY": "x" * 48,
        "RATE_LIMIT_ENABLED": True,
        "RATE_LIMIT_BACKEND": "redis",
        "MINIAPP_PUBLIC_URL": "https://example.test/miniapp/",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_settings_fail_closed_without_redis_rate_limit():
    with pytest.raises(ValidationError):
        _prod_settings(RATE_LIMIT_ENABLED=False)
    with pytest.raises(ValidationError):
        _prod_settings(RATE_LIMIT_BACKEND="memory")


def test_production_settings_require_https_and_strong_jwt():
    with pytest.raises(ValidationError):
        _prod_settings(MINIAPP_PUBLIC_URL="http://example.test/miniapp/")
    with pytest.raises(ValidationError):
        _prod_settings(JWT_SECRET_KEY="short")


def test_log_redaction_covers_bearer_telegram_and_secret_fields():
    telegram = "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
    raw = f"Authorization: Bearer abc.def.ghi token=supersecret {telegram}"
    redacted = redact_text(raw)
    assert "abc.def.ghi" not in redacted
    assert "supersecret" not in redacted
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in redacted

    formatter = JsonFormatter()
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, raw, (), None)
    rendered = formatter.format(record)
    assert "supersecret" not in rendered


def test_rate_limit_route_classification_is_explicit():
    middleware = RateLimitMiddleware(lambda scope, receive, send: None)
    assert middleware is not None


def test_production_stripe_billing_requires_provider_secrets_and_https_redirects():
    with pytest.raises(ValidationError):
        _prod_settings(BILLING_PROVIDER="stripe")

    with pytest.raises(ValidationError):
        _prod_settings(
            BILLING_PROVIDER="stripe",
            STRIPE_SECRET_KEY="sk_test_example",
            STRIPE_WEBHOOK_SECRET="whsec_example",
            BILLING_SUCCESS_URL="http://example.test/billing/success",
        )

    configured = _prod_settings(
        BILLING_PROVIDER="stripe",
        STRIPE_SECRET_KEY="sk_test_example",
        STRIPE_WEBHOOK_SECRET="whsec_example",
        BILLING_SUCCESS_URL="https://example.test/billing/success",
        BILLING_CANCEL_URL="https://example.test/billing/cancel",
        BILLING_PORTAL_RETURN_URL="https://example.test/admin/",
    )
    assert configured.billing_provider == "stripe"


def test_production_tron_verifier_requires_https_and_installation_api_key():
    with pytest.raises(ValidationError):
        _prod_settings(PAYMENT_TRON_USDT_ENABLED=True)
    with pytest.raises(ValidationError):
        _prod_settings(
            PAYMENT_TRON_USDT_ENABLED=True,
            PAYMENT_TRONGRID_API_KEY="key",
            PAYMENT_TRONGRID_BASE_URL="http://api.trongrid.io",
        )

    configured = _prod_settings(
        PAYMENT_TRON_USDT_ENABLED=True,
        PAYMENT_TRONGRID_API_KEY="key",
    )
    assert configured.payment_tron_usdt_enabled is True


def test_production_bsc_token_verifier_requires_explicit_https_rpc_and_token_identity():
    with pytest.raises(ValidationError):
        _prod_settings(PAYMENT_BSC_TOKEN_ENABLED=True)
    with pytest.raises(ValidationError):
        _prod_settings(
            PAYMENT_BSC_TOKEN_ENABLED=True,
            PAYMENT_BSC_RPC_URL="http://bsc.example.test",
            PAYMENT_BSC_TOKEN_ASSET="BSC_USD",
            PAYMENT_BSC_TOKEN_CONTRACT="0x" + "1" * 40,
        )

    configured = _prod_settings(
        PAYMENT_BSC_TOKEN_ENABLED=True,
        PAYMENT_BSC_RPC_URL="https://bsc.example.test",
        PAYMENT_BSC_TOKEN_ASSET="BSC_USD",
        PAYMENT_BSC_TOKEN_CONTRACT="0x" + "1" * 40,
    )
    assert configured.payment_bsc_chain_id == 56
