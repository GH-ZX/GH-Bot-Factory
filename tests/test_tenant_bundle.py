from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from packages.core.models import Base
from packages.marketplace.tenant_bundle import BundleError, decrypt, encrypt, restore, snapshot
from packages.payments.models import Wallet
from packages.payments.service import LedgerService
from packages.providers.models import Provider, ProviderCredential
from packages.telegram.models import Bot
from packages.telegram.secrets import EnvSecretStorage
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
PASSPHRASE = "test-only-long-export-passphrase"


async def seed_bundle(session):
    tenant = Tenant(name="Portable", slug="portable")
    other = Tenant(name="Private other tenant", slug="never-export")
    user = User(telegram_id=22334455, username="owner", hashed_password="never-export-password", email="private@example.invalid")
    session.add_all([tenant, other, user])
    await session.flush()
    session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER))
    bot = Bot(tenant_id=tenant.id, telegram_bot_id=992233, display_name="Portable bot", token_secret_ref="TEST_BOT_KEY", is_enabled=True)
    session.add(bot)
    source = EnvSecretStorage({"TEST_BOT_KEY": "test-bot-secret", "PROVIDER_A_KEY": "provider-a-secret", "PROVIDER_B_KEY": "provider-b-secret"})
    for slug, ref in (("a", "PROVIDER_A_KEY"), ("b", "PROVIDER_B_KEY")):
        provider = Provider(tenant_id=tenant.id, name=slug, slug=slug, provider_type="MOCK")
        session.add(provider)
        await session.flush()
        session.add(ProviderCredential(tenant_id=tenant.id, provider_id=provider.id, credential_type="API_KEY", secret_ref=ref))
    wallet = await LedgerService.get_or_create_wallet(session, tenant.id, user.id, "USD")
    await LedgerService.credit(session, wallet, Decimal("25.00"), reference_id="bundle-seed", reference_type="TEST")
    await session.commit()
    payload = await snapshot(session, tenant.id, source)
    return payload, tenant.id, wallet.id


async def assert_restored(session, vault, tenant_id, wallet_id):
    wallet = await session.get(Wallet, wallet_id)
    assert wallet.balance == Decimal("25.00")
    amount, valid = await LedgerService.reconstruct_and_verify_balance(session, wallet_id, tenant_id)
    assert valid and amount == Decimal("25.00")
    credentials = (await session.execute(select(ProviderCredential).where(ProviderCredential.tenant_id == tenant_id))).scalars().all()
    assert len({row.secret_ref for row in credentials}) == 2
    assert {await vault.get_secret(row.secret_ref) for row in credentials} == {"provider-a-secret", "provider-b-secret"}
    bot = (await session.execute(select(Bot).where(Bot.tenant_id == tenant_id))).scalar_one()
    assert not bot.is_enabled
    assert await vault.get_secret(bot.token_secret_ref) == "test-bot-secret"
    assert not (await session.get(Tenant, tenant_id)).is_active
    assert await session.scalar(select(Base.metadata.tables["system_install_state"].c.is_initialized))


async def test_encrypted_tenant_restore_preserves_credentials_and_ledger(db_session):
    payload, tenant_id, wallet_id = await seed_bundle(db_session)
    ciphertext = encrypt(payload, PASSPHRASE)
    for sensitive in (b"provider-a-secret", b"test-bot-secret", b"never-export", b"private@example.invalid"):
        assert sensitive not in ciphertext
    assert "never-export" not in str(payload)
    assert "private@example.invalid" not in str(payload)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as dest:
            vault = EnvSecretStorage({})
            result = await restore(dest, decrypt(ciphertext, PASSPHRASE), vault)
            assert result["tenant_id"] == str(tenant_id)
            await assert_restored(dest, vault, tenant_id, wallet_id)
            with pytest.raises(BundleError, match="empty"):
                await restore(dest, payload, vault)
    finally:
        await engine.dispose()


async def test_bundle_rejects_wrong_key_tampering_and_cross_tenant_rows(db_session):
    payload, _, _ = await seed_bundle(db_session)
    ciphertext = encrypt(payload, PASSPHRASE)
    with pytest.raises(BundleError):
        decrypt(ciphertext, "wrong-passphrase-long-enough")
    with pytest.raises(BundleError):
        decrypt(ciphertext[:-12] + b"bad payload", PASSPHRASE)
    changed = deepcopy(payload)
    changed["tables"]["wallets"][0]["tenant_id"] = "00000000-0000-0000-0000-000000000001"
    with pytest.raises(BundleError, match="Cross-tenant"):
        decrypt(encrypt(changed, PASSPHRASE), PASSPHRASE)
    changed = deepcopy(payload)
    changed["secrets"].clear()
    with pytest.raises(BundleError, match="secret set"):
        decrypt(encrypt(changed, PASSPHRASE), PASSPHRASE)


async def test_restore_vault_failure_rolls_back_and_stays_offline(db_session):
    payload, _, _ = await seed_bundle(db_session)
    class BrokenVault(EnvSecretStorage):
        async def set_secret(self, ref, value):
            await super().set_secret(ref, value)
            raise OSError("simulated vault failure")
    vault = BrokenVault({})
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine) as dest:
            with pytest.raises(OSError):
                await restore(dest, payload, vault)
            assert not vault._memory_store
            assert not (await dest.execute(select(Tenant))).scalars().all()
    finally:
        await engine.dispose()


async def test_import_script_requires_confirmations(tmp_path, monkeypatch):
    import sys

    from scripts import import_tenant_bundle

    bundle = tmp_path / "test.enc"
    bundle.write_bytes(b"dummy")

    # Missing --confirm-empty-offline-destination
    monkeypatch.setattr(sys, "argv", ["import_tenant_bundle.py", str(bundle)])
    with pytest.raises(SystemExit):
        await import_tenant_bundle.main()

    # Activation without confirm source stopped
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_tenant_bundle.py",
            str(bundle),
            "--confirm-empty-offline-destination",
            "--activate",
        ],
    )
    with pytest.raises(SystemExit):
        await import_tenant_bundle.main()
