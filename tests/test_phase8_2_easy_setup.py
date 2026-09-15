from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from packages.core.system_models import SystemInstallState
from packages.factory.provisioning import VerifiedTelegramBot
from packages.setup.service import SetupError, install_first_tenant, is_initialized
from packages.telegram.models import Bot
from packages.telegram.secrets import EncryptedFileSecretStorage
from packages.tenants.models import Membership, Role, Tenant, User


class FakeVerifier:
    def __init__(self, *, bot_id: int = 8867803658, username: str = "mrandroidrobot") -> None:
        self.bot_id = bot_id
        self.username = username
        self.seen_token: str | None = None

    async def verify(self, token: str) -> VerifiedTelegramBot:
        self.seen_token = token
        return VerifiedTelegramBot(
            telegram_bot_id=self.bot_id,
            username=self.username,
            display_name="Android Robot",
        )


@pytest.mark.asyncio
async def test_encrypted_local_vault_round_trip_without_plaintext(tmp_path: Path) -> None:
    vault = EncryptedFileSecretStorage(tmp_path / "vault")
    token = "8867803658:" + "AAExampleTokenMaterialThatMustNotLeak"

    await vault.set_secret("GHBF_VAULT_TELEGRAM_8867803658", token)

    ciphertext = (tmp_path / "vault" / "secrets.json").read_text(encoding="utf-8")
    assert token not in ciphertext
    assert "AAExampleTokenMaterial" not in ciphertext
    assert (tmp_path / "vault" / "master.key").stat().st_mode & 0o077 == 0

    second_process_view = EncryptedFileSecretStorage(tmp_path / "vault")
    assert await second_process_view.get_secret("GHBF_VAULT_TELEGRAM_8867803658") == token


@pytest.mark.asyncio
async def test_first_run_setup_creates_owner_bot_and_locks_installer(db_session, tmp_path: Path) -> None:
    vault = EncryptedFileSecretStorage(tmp_path / "vault")
    verifier = FakeVerifier()
    token = "8867803658:" + "AAExampleTokenMaterialThatMustNotLeak"

    result = await install_first_tenant(
        session=db_session,
        secret_storage=vault,
        bot_token=token,
        tenant_slug="android-store",
        tenant_name="Android Store",
        owner_telegram_id=8358194002,
        owner_username="ahmedghxx",
        bot_display_name="Android Store Control",
        expected_bot_username="@mrandroidrobot",
        template_key="general-commerce",
        public_base_url="https://factory.gh-store.me",
        verifier=verifier,
    )

    assert verifier.seen_token == token
    assert await is_initialized(db_session) is True

    tenant = await db_session.get(Tenant, result.tenant_id)
    owner = await db_session.get(User, result.owner_user_id)
    bot = await db_session.get(Bot, result.bot_id)
    membership = (
        await db_session.execute(
            select(Membership).where(
                Membership.tenant_id == result.tenant_id,
                Membership.user_id == result.owner_user_id,
            )
        )
    ).scalar_one()
    state = await db_session.get(SystemInstallState, 1)

    assert tenant is not None
    assert tenant.settings["miniapp_public_url"] == "https://factory.gh-store.me/miniapp/"
    assert tenant.settings["admin_public_url"] == "https://factory.gh-store.me/admin/"
    assert owner is not None and owner.telegram_id == 8358194002
    assert membership.role == Role.OWNER
    assert bot is not None and bot.telegram_bot_id == 8867803658
    assert bot.token_secret_ref == "GHBF_VAULT_TELEGRAM_8867803658"
    assert await vault.get_secret(bot.token_secret_ref) == token
    assert state is not None and state.is_initialized is True

    with pytest.raises(SetupError) as exc_info:
        await install_first_tenant(
            session=db_session,
            secret_storage=vault,
            bot_token=token,
            tenant_slug="second-store",
            tenant_name="Second Store",
            owner_telegram_id=8358194002,
            owner_username="ahmedghxx",
            bot_display_name="Second Control",
            expected_bot_username="mrandroidrobot",
            template_key="general-commerce",
            public_base_url=None,
            verifier=verifier,
        )
    assert exc_info.value.code == "ALREADY_INITIALIZED"


@pytest.mark.asyncio
async def test_setup_rejects_non_https_public_url_before_persisting(db_session, tmp_path: Path) -> None:
    vault = EncryptedFileSecretStorage(tmp_path / "vault")

    with pytest.raises(SetupError) as exc_info:
        await install_first_tenant(
            session=db_session,
            secret_storage=vault,
            bot_token="8867803658:" + "AAExampleTokenMaterialThatMustNotLeak",
            tenant_slug="android-store",
            tenant_name="Android Store",
            owner_telegram_id=8358194002,
            owner_username="ahmedghxx",
            bot_display_name="Android Store Control",
            expected_bot_username="mrandroidrobot",
            template_key="general-commerce",
            public_base_url="http://factory.gh-store.me",
            verifier=FakeVerifier(),
        )

    assert exc_info.value.code == "PUBLIC_URL_INVALID"
    assert await is_initialized(db_session) is False


def test_easy_start_repairs_database_url_with_url_encoded_password(tmp_path: Path) -> None:
    import importlib.util

    module_path = Path(__file__).resolve().parents[1] / "scripts" / "easy_start.py"
    spec = importlib.util.spec_from_file_location("ghbf_easy_start_test", module_path)
    assert spec is not None and spec.loader is not None
    easy_start = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(easy_start)

    env_path = tmp_path / ".env"
    example_path = tmp_path / ".env.example"
    example_path.write_text(
        "POSTGRES_DB=gh_bot_factory\n"
        "POSTGRES_USER=gh_bot_factory\n"
        "POSTGRES_PASSWORD=local@password\n"
        "JWT_SECRET_KEY=\n"
        "SETUP_CODE=\n",
        encoding="utf-8",
    )
    easy_start.ENV_PATH = env_path
    easy_start.EXAMPLE_PATH = example_path

    setup_code = easy_start.prepare_env()
    rendered = env_path.read_text(encoding="utf-8")

    assert setup_code
    assert "local%40password@postgres:5432/gh_bot_factory" in rendered
    assert "JWT_SECRET_KEY=" in rendered
    assert "SETUP_CODE=" in rendered


def test_easy_start_wait_for_api_tolerates_transient_connection_reset(monkeypatch) -> None:
    import importlib.util

    module_path = Path(__file__).resolve().parents[1] / "scripts" / "easy_start.py"
    spec = importlib.util.spec_from_file_location("ghbf_easy_start_wait_test", module_path)
    assert spec is not None and spec.loader is not None
    easy_start = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(easy_start)

    class ReadyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    attempts = iter([ConnectionResetError(104, "Connection reset by peer"), ReadyResponse()])

    def fake_urlopen(*args, **kwargs):
        result = next(attempts)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(easy_start.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(easy_start.time, "sleep", lambda _seconds: None)

    easy_start.wait_for_api("8010", timeout_seconds=5)
