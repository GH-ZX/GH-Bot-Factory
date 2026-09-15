import logging
import uuid

import pytest

from packages.core.auth import (
    AuthSource,
    AuthTokenService,
    TokenInvalidSignatureError,
)

JWT_SECRET_A = "jwt-secret-a-0123456789abcdef-0123456789abcdef"
JWT_SECRET_B = "jwt-secret-b-0123456789abcdef-0123456789abcdef"


def _issue_token(service: AuthTokenService) -> str:
    return service.issue_access_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        roles=["CUSTOMER"],
        source=AuthSource.TEST,
    )


def test_auth_token_service_requires_jwt_secret(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY must be explicitly configured"):
        AuthTokenService()


def test_legacy_secret_key_does_not_act_as_jwt_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY", JWT_SECRET_A)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY must be explicitly configured"):
        AuthTokenService()


def test_auth_token_service_uses_jwt_secret_key_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET_A)

    service = AuthTokenService()
    token = _issue_token(service)
    payload = service.verify_access_token(token)

    assert payload["source"] == AuthSource.TEST.value


def test_auth_token_service_rejects_short_jwt_secret() -> None:
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        AuthTokenService(secret_key="too-short")


def test_rotating_jwt_secret_invalidates_existing_tokens() -> None:
    old_service = AuthTokenService(secret_key=JWT_SECRET_A)
    new_service = AuthTokenService(secret_key=JWT_SECRET_B)
    token = _issue_token(old_service)

    with pytest.raises(TokenInvalidSignatureError):
        new_service.verify_access_token(token)


def test_jwt_secret_is_not_exposed_in_repr_errors_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    service = AuthTokenService(secret_key=JWT_SECRET_A)
    forged_service = AuthTokenService(secret_key=JWT_SECRET_B)
    forged_token = _issue_token(forged_service)

    caplog.set_level(logging.DEBUG)
    with pytest.raises(TokenInvalidSignatureError) as exc_info:
        service.verify_access_token(forged_token)

    assert JWT_SECRET_A not in repr(service)
    assert JWT_SECRET_A not in str(exc_info.value)
    assert JWT_SECRET_A not in caplog.text
