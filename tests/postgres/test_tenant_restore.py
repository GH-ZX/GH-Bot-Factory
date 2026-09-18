import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from packages.core.models import Base
from packages.marketplace.tenant_bundle import decrypt, encrypt, restore
from packages.telegram.secrets import EncryptedFileSecretStorage
from tests.test_tenant_bundle import PASSPHRASE, assert_restored, seed_bundle

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_encrypted_restore_into_fresh_postgres_schema(postgres_session_factory, postgres_test_database_url, tmp_path):
    async with postgres_session_factory() as source:
        payload, tenant_id, wallet_id = await seed_bundle(source)
    artifact = tmp_path / "tenant.ghbf.enc"
    artifact.write_bytes(encrypt(payload, PASSPHRASE))
    engine = create_async_engine(postgres_test_database_url)
    schema = f"restore_{uuid.uuid4().hex}"
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET search_path TO "{schema}"'))
            await conn.run_sync(Base.metadata.create_all)
            await conn.commit()
            async with AsyncSession(bind=conn, expire_on_commit=False) as destination:
                vault = EncryptedFileSecretStorage(tmp_path / "vault")
                await restore(destination, decrypt(artifact.read_bytes(), PASSPHRASE), vault)
                await assert_restored(destination, vault, tenant_id, wallet_id)
            await conn.rollback()
            await conn.execute(text('SET search_path TO public'))
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await conn.commit()
    finally:
        await engine.dispose()
