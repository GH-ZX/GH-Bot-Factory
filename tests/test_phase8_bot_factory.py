import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from apps.api.v1.admin_bots import _secret_storage
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.factory.models import BotProvisioningJob, BotProvisioningStatus
from packages.factory.provisioning import (
    BotProvisioningService,
    ProvisioningError,
    VerifiedTelegramBot,
    provisioning_fingerprint,
)
from packages.telegram.models import Bot
from packages.telegram.secrets import EncryptedFileSecretStorage, EnvSecretStorage
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase8-bot-factory-jwt-secret-0123456789abcdef-0123456789abcdef"


class FakeVerifier:
    def __init__(self, result: VerifiedTelegramBot, failures: list[ProvisioningError] | None = None) -> None:
        self.result = result
        self.failures = list(failures or [])
        self.calls = 0

    async def verify(self, token: str) -> VerifiedTelegramBot:
        assert token == "resolved-test-token"
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.result


async def create_identity(
    session: AsyncSession,
    tenant: Tenant,
    *,
    role: Role,
) -> tuple[User, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"{role.value.lower()}_{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    session.add(
        Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            permissions=[],
            is_active=True,
        )
    )
    await session.flush()
    token = AuthTokenService(secret_key=TEST_JWT_SECRET).issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[role],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )
    return user, token


@pytest_asyncio.fixture
async def admin_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "session": db_session}
    app.dependency_overrides.clear()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_job(
    tenant: Tenant,
    user: User,
    *,
    key: str,
    expected_username: str | None = "shop_bot",
    status: BotProvisioningStatus = BotProvisioningStatus.PENDING,
) -> BotProvisioningJob:
    config = {"currency": "USD", "enabled_modules": ["catalog"]}
    return BotProvisioningJob(
        tenant_id=tenant.id,
        requested_by_user_id=user.id,
        idempotency_key=key,
        request_fingerprint=provisioning_fingerprint(
            token_secret_ref="TEST_BOT_TOKEN_REF",
            expected_username=expected_username,
            requested_display_name=None,
            desired_enabled=True,
            desired_config=config,
        ),
        token_secret_ref="TEST_BOT_TOKEN_REF",
        expected_username=expected_username,
        desired_enabled=True,
        desired_config=config,
        status=status,
    )


async def test_admin_provisioning_is_idempotent_rbac_scoped_and_secret_safe(
    admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Factory Tenant", slug="factory-tenant", is_active=True)
    session.add(tenant)
    await session.flush()
    _, staff_token = await create_identity(session, tenant, role=Role.STAFF)
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)

    payload = {
        "token_secret_ref": "FACTORY_TELEGRAM_TOKEN",
        "expected_username": "@Factory_Bot",
        "display_name": "Factory",
        "config": {"locale": "en"},
    }
    denied = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(staff_token), "Idempotency-Key": "factory-request-001"},
        json=payload,
    )
    assert denied.status_code == 403

    first = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "factory-request-001"},
        json=payload,
    )
    assert first.status_code == 202, first.text
    replay = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "factory-request-001"},
        json=payload,
    )
    assert replay.status_code == 202
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["expected_username"] == "factory_bot"
    assert "token_secret_ref" not in first.json()
    assert first.json()["token_configured"] is True

    conflict = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "factory-request-001"},
        json={**payload, "display_name": "Different"},
    )
    assert conflict.status_code == 409

    rejected_secret = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "factory-request-002"},
        json={**payload, "token_secret_ref": "123456789:" + "A" * 30},
    )
    assert rejected_secret.status_code == 422


async def test_admin_can_submit_bot_token_directly_into_encrypted_vault(
    admin_env: dict[str, Any],
    tmp_path,
) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Easy Factory", slug="easy-factory", is_active=True)
    session.add(tenant)
    await session.flush()
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)

    vault = EncryptedFileSecretStorage(tmp_path / "vault")
    app.dependency_overrides[_secret_storage] = lambda: vault
    token = "123456789:" + "A" * 30
    payload = {
        "bot_token": token,
        "expected_username": "easy_factory_bot",
        "display_name": "Easy Factory",
        "template_key": "general-commerce",
    }
    first = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "easy-direct-token-001"},
        json=payload,
    )
    assert first.status_code == 202, first.text
    body = first.json()
    assert "token_secret_ref" not in body
    assert "bot_token" not in body

    job = await session.get(BotProvisioningJob, uuid.UUID(body["id"]))
    assert job is not None
    assert job.token_secret_ref.startswith("GHBF_VAULT_BOT_")
    assert await vault.get_secret(job.token_secret_ref) == token
    assert token not in (tmp_path / "vault" / "secrets.json").read_text(encoding="utf-8")

    changed_token = "123456789:" + "B" * 30
    conflict = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "easy-direct-token-001"},
        json={**payload, "bot_token": changed_token},
    )
    assert conflict.status_code == 409
    assert await vault.get_secret(job.token_secret_ref) == token



async def test_provisioning_worker_verifies_identity_creates_bot_and_audits(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        tenant = Tenant(name="Worker Tenant", slug="worker-tenant", is_active=True)
        session.add(tenant)
        await session.flush()
        user, _ = await create_identity(session, tenant, role=Role.ADMIN)
        job = make_job(tenant, user, key="worker-success")
        session.add(job)
        await session.commit()
        job_id = job.id
        tenant_id = tenant.id

    verifier = FakeVerifier(VerifiedTelegramBot(telegram_bot_id=900001, username="shop_bot", display_name="Shop Bot"))
    service = BotProvisioningService(
        session_factory=db_session_factory,
        secret_storage=EnvSecretStorage({"TEST_BOT_TOKEN_REF": "resolved-test-token"}),
        verifier=verifier,
    )
    assert await service.process_one() is True

    async with db_session_factory() as session:
        stored = await session.get(BotProvisioningJob, job_id)
        assert stored is not None and stored.status == BotProvisioningStatus.READY
        assert stored.verified_telegram_bot_id == 900001
        bot = (await session.execute(select(Bot).where(Bot.tenant_id == tenant_id))).scalar_one()
        assert bot.telegram_bot_id == 900001
        assert bot.username == "shop_bot"
        assert bot.token_secret_ref == "TEST_BOT_TOKEN_REF"
        logs = list(
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == tenant_id,
                        AuditLog.action == "BOT_PROVISIONING_READY",
                    )
                )
            ).scalars().all()
        )
        assert len(logs) == 1
        assert "resolved-test-token" not in str(logs[0].details)


async def test_cross_tenant_telegram_identity_is_rejected(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        tenant = Tenant(name="A", slug="factory-a", is_active=True)
        other = Tenant(name="B", slug="factory-b", is_active=True)
        session.add_all([tenant, other])
        await session.flush()
        user, _ = await create_identity(session, tenant, role=Role.ADMIN)
        session.add(
            Bot(
                tenant_id=other.id,
                telegram_bot_id=900002,
                username="owned_bot",
                display_name="Owned",
                token_secret_ref="OTHER_TOKEN_REF",
                is_enabled=True,
                config={},
            )
        )
        job = make_job(tenant, user, key="cross-tenant", expected_username="owned_bot")
        session.add(job)
        await session.commit()
        job_id = job.id
        tenant_id = tenant.id

    service = BotProvisioningService(
        session_factory=db_session_factory,
        secret_storage=EnvSecretStorage({"TEST_BOT_TOKEN_REF": "resolved-test-token"}),
        verifier=FakeVerifier(VerifiedTelegramBot(900002, "owned_bot", "Owned")),
    )
    await service.process_one()
    async with db_session_factory() as session:
        stored = await session.get(BotProvisioningJob, job_id)
        assert stored is not None and stored.status == BotProvisioningStatus.FAILED
        assert stored.last_error_code == "TELEGRAM_BOT_OWNED_BY_ANOTHER_TENANT"
        assert await session.scalar(select(func.count()).select_from(Bot).where(Bot.tenant_id == tenant_id)) == 0


async def test_retryable_telegram_failure_is_retried_then_succeeds(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        tenant = Tenant(name="Retry", slug="factory-retry", is_active=True)
        session.add(tenant)
        await session.flush()
        user, _ = await create_identity(session, tenant, role=Role.ADMIN)
        job = make_job(tenant, user, key="retry-success")
        session.add(job)
        await session.commit()
        job_id = job.id

    verifier = FakeVerifier(
        VerifiedTelegramBot(900003, "shop_bot", "Shop"),
        failures=[ProvisioningError("TELEGRAM_TEMPORARY_FAILURE", retryable=True)],
    )
    service = BotProvisioningService(
        session_factory=db_session_factory,
        secret_storage=EnvSecretStorage({"TEST_BOT_TOKEN_REF": "resolved-test-token"}),
        verifier=verifier,
        base_backoff_seconds=0,
    )
    await service.process_one()
    async with db_session_factory() as session:
        stored = await session.get(BotProvisioningJob, job_id)
        assert stored is not None and stored.status == BotProvisioningStatus.RETRY
        assert stored.attempt_count == 1
    await service.process_one()
    async with db_session_factory() as session:
        stored = await session.get(BotProvisioningJob, job_id)
        assert stored is not None and stored.status == BotProvisioningStatus.READY
        assert stored.attempt_count == 2
    assert verifier.calls == 2


async def test_stale_running_job_recovers_after_worker_crash(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        tenant = Tenant(name="Recovery", slug="factory-recovery", is_active=True)
        session.add(tenant)
        await session.flush()
        user, _ = await create_identity(session, tenant, role=Role.ADMIN)
        job = make_job(tenant, user, key="stale", status=BotProvisioningStatus.RUNNING)
        job.attempt_count = 1
        job.locked_at = datetime.now(UTC) - timedelta(minutes=5)
        job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=4)
        session.add(job)
        await session.commit()
        job_id = job.id

    service = BotProvisioningService(session_factory=db_session_factory)
    async with db_session_factory() as session:
        assert await service.recover_stale_jobs(session) == 1
    async with db_session_factory() as session:
        stored = await session.get(BotProvisioningJob, job_id)
        assert stored is not None and stored.status == BotProvisioningStatus.RETRY
        assert stored.last_error_code == "WORKER_LEASE_EXPIRED"
        assert stored.lease_expires_at is None


async def test_username_mismatch_fails_closed_without_bot(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        tenant = Tenant(name="Mismatch", slug="factory-mismatch", is_active=True)
        session.add(tenant)
        await session.flush()
        user, _ = await create_identity(session, tenant, role=Role.ADMIN)
        job = make_job(tenant, user, key="username-mismatch", expected_username="expected_bot")
        session.add(job)
        await session.commit()
        job_id = job.id
        tenant_id = tenant.id

    service = BotProvisioningService(
        session_factory=db_session_factory,
        secret_storage=EnvSecretStorage({"TEST_BOT_TOKEN_REF": "resolved-test-token"}),
        verifier=FakeVerifier(VerifiedTelegramBot(900004, "different_bot", "Different")),
    )
    await service.process_one()
    async with db_session_factory() as session:
        stored = await session.get(BotProvisioningJob, job_id)
        assert stored is not None and stored.status == BotProvisioningStatus.FAILED
        assert stored.last_error_code == "TELEGRAM_USERNAME_MISMATCH"
        assert await session.scalar(select(func.count()).select_from(Bot).where(Bot.tenant_id == tenant_id)) == 0


async def test_admin_bot_inventory_redacts_legacy_secret_like_config_and_toggle_is_tenant_scoped(
    admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Fleet", slug="factory-fleet", is_active=True)
    other = Tenant(name="Other Fleet", slug="other-factory-fleet", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, staff_token = await create_identity(session, tenant, role=Role.STAFF)
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)
    foreign = Bot(
        tenant_id=other.id,
        telegram_bot_id=900006,
        username="foreign_bot",
        display_name="Foreign",
        token_secret_ref="FOREIGN_TOKEN_REF",
        is_enabled=True,
        config={},
    )
    visible = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=900005,
        username="visible_bot",
        display_name="Visible",
        token_secret_ref="VISIBLE_TOKEN_REF",
        is_enabled=True,
        config={"nested": {"api_token": "legacy-secret-value"}, "locale": "en"},
    )
    session.add_all([visible, foreign])
    await session.flush()

    listing = await client.get("/api/v1/admin/bots", headers=auth(staff_token))
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["bots"][0]["config"]["nested"]["api_token"] == "[REDACTED]"
    assert "token_secret_ref" not in body["bots"][0]

    denied = await client.patch(
        f"/api/v1/admin/bots/{visible.id}/state",
        headers=auth(staff_token),
        json={"is_enabled": False},
    )
    assert denied.status_code == 403
    forbidden_foreign = await client.patch(
        f"/api/v1/admin/bots/{foreign.id}/state",
        headers=auth(admin_token),
        json={"is_enabled": False},
    )
    assert forbidden_foreign.status_code == 404
    changed = await client.patch(
        f"/api/v1/admin/bots/{visible.id}/state",
        headers=auth(admin_token),
        json={"is_enabled": False},
    )
    assert changed.status_code == 200
    assert changed.json()["is_enabled"] is False

async def test_same_tenant_identity_converges_existing_bot_without_duplicate(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        tenant = Tenant(name="Converge", slug="factory-converge", is_active=True)
        session.add(tenant)
        await session.flush()
        user, _ = await create_identity(session, tenant, role=Role.ADMIN)
        existing = Bot(
            tenant_id=tenant.id,
            telegram_bot_id=900007,
            username="old_name",
            display_name="Old",
            token_secret_ref="OLD_TOKEN_REF",
            is_enabled=False,
            config={"locale": "de"},
        )
        session.add(existing)
        job = make_job(tenant, user, key="same-tenant", expected_username="new_name")
        job.requested_display_name = "New Display"
        job.desired_config = {"locale": "en"}
        session.add(job)
        await session.commit()
        bot_id = existing.id
        tenant_id = tenant.id

    service = BotProvisioningService(
        session_factory=db_session_factory,
        secret_storage=EnvSecretStorage({"TEST_BOT_TOKEN_REF": "resolved-test-token"}),
        verifier=FakeVerifier(VerifiedTelegramBot(900007, "new_name", "Telegram Name")),
    )
    await service.process_one()
    async with db_session_factory() as session:
        bots = list((await session.execute(select(Bot).where(Bot.tenant_id == tenant_id))).scalars().all())
        assert len(bots) == 1
        assert bots[0].id == bot_id
        assert bots[0].username == "new_name"
        assert bots[0].display_name == "New Display"
        assert bots[0].token_secret_ref == "TEST_BOT_TOKEN_REF"
        assert bots[0].is_enabled is True
        assert bots[0].config == {"locale": "en"}


async def test_manual_retry_and_cancel_enforce_job_state_machine(admin_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Job Ops", slug="factory-job-ops", is_active=True)
    session.add(tenant)
    await session.flush()
    admin, admin_token = await create_identity(session, tenant, role=Role.ADMIN)
    failed = make_job(tenant, admin, key="failed-job", status=BotProvisioningStatus.FAILED)
    failed.attempt_count = 5
    failed.completed_at = datetime.now(UTC)
    failed.last_error_code = "TELEGRAM_TOKEN_UNAUTHORIZED"
    pending = make_job(tenant, admin, key="pending-job")
    session.add_all([failed, pending])
    await session.flush()

    retried = await client.post(f"/api/v1/admin/bots/jobs/{failed.id}/retry", headers=auth(admin_token))
    assert retried.status_code == 200
    assert retried.json()["status"] == "PENDING"
    assert retried.json()["attempt_count"] == 0

    cancelled = await client.post(f"/api/v1/admin/bots/jobs/{pending.id}/cancel", headers=auth(admin_token))
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    cannot_cancel_running = make_job(
        tenant,
        admin,
        key="running-job",
        status=BotProvisioningStatus.RUNNING,
    )
    session.add(cannot_cancel_running)
    await session.flush()
    conflict = await client.post(
        f"/api/v1/admin/bots/jobs/{cannot_cancel_running.id}/cancel",
        headers=auth(admin_token),
    )
    assert conflict.status_code == 409


class FlexibleVerifier:
    def __init__(self, result: VerifiedTelegramBot) -> None:
        self.result = result
        self.tokens: list[str] = []

    async def verify(self, token: str) -> VerifiedTelegramBot:
        self.tokens.append(token)
        return self.result


class FakeFleetStore:
    def __init__(self, payloads: dict[uuid.UUID, dict[str, Any]]) -> None:
        self.payloads = payloads

    async def read_many(self, bot_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
        return {bot_id: self.payloads[bot_id] for bot_id in bot_ids if bot_id in self.payloads}


async def test_bot_credential_verify_and_rotation_are_identity_safe(
    admin_env: dict[str, Any],
) -> None:
    from apps.api.v1.admin_bots import _telegram_identity_verifier

    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Credential Tenant", slug="credential-tenant", is_active=True)
    session.add(tenant)
    await session.flush()
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=910001,
        username="credential_bot",
        display_name="Credential Bot",
        token_secret_ref="CREDENTIAL_BOT_TOKEN",
        is_enabled=True,
        config={},
    )
    session.add(bot)
    await session.flush()

    storage = EnvSecretStorage({"CREDENTIAL_BOT_TOKEN": "old-token"})
    verifier = FlexibleVerifier(VerifiedTelegramBot(910001, "credential_bot", "Credential Bot"))
    app.dependency_overrides[_secret_storage] = lambda: storage
    app.dependency_overrides[_telegram_identity_verifier] = lambda: verifier

    verified = await client.post(
        f"/api/v1/admin/bots/{bot.id}/credentials/verify",
        headers=auth(admin_token),
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["bot"]["credential_status"] == "VERIFIED"
    assert verifier.tokens == ["old-token"]

    rotated = await client.post(
        f"/api/v1/admin/bots/{bot.id}/credentials/rotate",
        headers=auth(admin_token),
        json={"bot_token": "new-token"},
    )
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()["bot"]
    assert body["credential_status"] == "VERIFIED"
    assert body["credential_version"] == 2
    assert await storage.get_secret("CREDENTIAL_BOT_TOKEN") == "new-token"
    assert verifier.tokens[-1] == "new-token"


async def test_bot_credential_rotation_rejects_different_telegram_identity(
    admin_env: dict[str, Any],
) -> None:
    from apps.api.v1.admin_bots import _telegram_identity_verifier

    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Rotate Guard", slug="rotate-guard", is_active=True)
    session.add(tenant)
    await session.flush()
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=910002,
        username="guard_bot",
        display_name="Guard Bot",
        token_secret_ref="GUARD_BOT_TOKEN",
        is_enabled=True,
        config={},
    )
    session.add(bot)
    await session.flush()

    storage = EnvSecretStorage({"GUARD_BOT_TOKEN": "original-token"})
    verifier = FlexibleVerifier(VerifiedTelegramBot(999999, "wrong_bot", "Wrong Bot"))
    app.dependency_overrides[_secret_storage] = lambda: storage
    app.dependency_overrides[_telegram_identity_verifier] = lambda: verifier

    response = await client.post(
        f"/api/v1/admin/bots/{bot.id}/credentials/rotate",
        headers=auth(admin_token),
        json={"bot_token": "wrong-token"},
    )
    assert response.status_code == 409
    assert await storage.get_secret("GUARD_BOT_TOKEN") == "original-token"
    await session.refresh(bot)
    assert bot.credential_version == 1


async def test_fleet_endpoint_reports_observed_runtime_and_restart_intent(
    admin_env: dict[str, Any],
) -> None:
    from apps.api.v1.admin_bots import _fleet_state_store

    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Fleet Tenant", slug="fleet-tenant", is_active=True)
    session.add(tenant)
    await session.flush()
    _, staff_token = await create_identity(session, tenant, role=Role.STAFF)
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=910003,
        username="fleet_bot",
        display_name="Fleet Bot",
        token_secret_ref="FLEET_BOT_TOKEN",
        credential_status="VERIFIED",
        is_enabled=True,
        config={},
    )
    session.add(bot)
    await session.flush()
    bot_id = bot.id
    observed_at = datetime.now(UTC).isoformat()
    app.dependency_overrides[_fleet_state_store] = lambda: FakeFleetStore(
        {bot_id: {"status": "RUNNING", "detail": None, "observed_at": observed_at}}
    )

    fleet = await client.get("/api/v1/admin/bots/fleet", headers=auth(staff_token))
    assert fleet.status_code == 200, fleet.text
    item = fleet.json()["bots"][0]
    assert item["desired_state"] == "ENABLED"
    assert item["runtime_status"] == "RUNNING"
    assert item["credential_status"] == "VERIFIED"

    restarted = await client.post(
        f"/api/v1/admin/bots/{bot_id}/runtime/restart",
        headers=auth(admin_token),
    )
    assert restarted.status_code == 200, restarted.text
    assert restarted.json()["runtime_revision"] == 2


async def test_factory_capacity_limits_block_provisioning_and_enablement(
    admin_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packages.core.config import settings

    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Capacity Tenant", slug="capacity-tenant", is_active=True)
    session.add(tenant)
    await session.flush()
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)
    existing = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=910004,
        username="capacity_bot",
        display_name="Capacity Bot",
        token_secret_ref="CAPACITY_BOT_TOKEN",
        is_enabled=True,
        config={},
    )
    disabled = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=910005,
        username="disabled_bot",
        display_name="Disabled Bot",
        token_secret_ref="DISABLED_BOT_TOKEN",
        is_enabled=False,
        config={},
    )
    session.add_all([existing, disabled])
    await session.flush()

    monkeypatch.setattr(settings, "factory_max_bots_per_tenant", 2)
    blocked = await client.post(
        "/api/v1/admin/bots/provision",
        headers={**auth(admin_token), "Idempotency-Key": "capacity-new-bot-001"},
        json={
            "token_secret_ref": "THIRD_BOT_TOKEN",
            "display_name": "Third Bot",
            "config": {},
            "is_enabled": False,
        },
    )
    assert blocked.status_code == 409
    assert "bot limit" in blocked.json()["detail"].lower()

    monkeypatch.setattr(settings, "factory_max_enabled_bots_per_tenant", 1)
    enable = await client.patch(
        f"/api/v1/admin/bots/{disabled.id}/state",
        headers=auth(admin_token),
        json={"is_enabled": True},
    )
    assert enable.status_code == 409
    assert "enabled-bot limit" in enable.json()["detail"].lower()


async def test_release_channel_change_is_audited_and_bumps_runtime_revision(
    admin_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(name="Rollout Tenant", slug="rollout-tenant", is_active=True)
    session.add(tenant)
    await session.flush()
    _, admin_token = await create_identity(session, tenant, role=Role.ADMIN)
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=910006,
        username="rollout_bot",
        display_name="Rollout Bot",
        token_secret_ref="ROLLOUT_BOT_TOKEN",
        is_enabled=True,
        config={},
    )
    session.add(bot)
    await session.flush()

    moved = await client.patch(
        f"/api/v1/admin/bots/{bot.id}/release-channel",
        headers=auth(admin_token),
        json={"release_channel": "CANARY"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["release_channel"] == "CANARY"
    assert moved.json()["runtime_revision"] == 2

    log = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant.id,
                AuditLog.action == "BOT_RELEASE_CHANNEL_CHANGED",
            )
        )
    ).scalar_one()
    assert log.details["previous"] == "STABLE"
    assert log.details["next"] == "CANARY"


async def test_launch_readiness_is_server_authoritative_and_blocks_missing_fulfillment(
    admin_env: dict[str, Any],
) -> None:
    from decimal import Decimal

    from apps.api.v1.admin_bots import _fleet_state_store
    from packages.commerce.models import Product, ProductVariant
    from packages.providers.models import Provider, ProviderProductMapping

    client: httpx.AsyncClient = admin_env["client"]
    session: AsyncSession = admin_env["session"]
    tenant = Tenant(
        name="Launch Tenant",
        slug="launch-tenant",
        is_active=True,
        settings={
            "miniapp_public_url": "https://factory.example/miniapp/",
            "admin_public_url": "https://factory.example/admin/",
        },
    )
    session.add(tenant)
    await session.flush()
    _, staff_token = await create_identity(session, tenant, role=Role.STAFF)
    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=910007,
        username="launch_bot",
        display_name="Launch Bot",
        token_secret_ref="LAUNCH_BOT_TOKEN",
        credential_status="VERIFIED",
        is_enabled=True,
        config={},
    )
    session.add(bot)
    await session.flush()
    app.dependency_overrides[_fleet_state_store] = lambda: FakeFleetStore(
        {bot.id: {"status": "RUNNING", "observed_at": datetime.now(UTC).isoformat()}}
    )

    blocked = await client.get(
        f"/api/v1/admin/bots/{bot.id}/launch-readiness",
        headers=auth(staff_token),
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["launchable"] is False
    statuses = {item["key"]: item["status"] for item in blocked.json()["checks"]}
    assert statuses["catalog"] == "BLOCK"
    assert statuses["fulfillment"] == "BLOCK"
    assert statuses["payments"] == "WARN"

    product = Product(tenant_id=tenant.id, title="Ready Product", is_active=True)
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku="READY-1",
        title="Ready",
        price=Decimal("10.00"),
        currency="USD",
        stock_quantity=10,
        is_active=True,
    )
    provider = Provider(
        tenant_id=tenant.id,
        name="Ready Provider",
        slug="ready-provider",
        provider_type="mock",
        is_enabled=True,
        priority=1,
    )
    session.add_all([variant, provider])
    await session.flush()
    session.add(
        ProviderProductMapping(
            tenant_id=tenant.id,
            provider_id=provider.id,
            product_id=product.id,
            product_variant_id=variant.id,
            external_product_id="ready-ext",
            is_enabled=True,
            cost_price=Decimal("5.00"),
            cost_currency="USD",
        )
    )
    await session.flush()

    ready = await client.get(
        f"/api/v1/admin/bots/{bot.id}/launch-readiness",
        headers=auth(staff_token),
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["launchable"] is True
