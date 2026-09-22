import uuid

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from packages.delivery.package import fresh_database_sql
from packages.marketplace.tenant_bundle import restore
from packages.telegram.secrets import EnvSecretStorage
from tests.test_tenant_bundle import assert_restored, seed_bundle

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_generated_schema_accepts_encrypted_tenant_state(postgres_test_database_url, postgres_session_factory):
    async with postgres_session_factory() as source:
        payload, tenant_id, wallet_id = await seed_bundle(source)
    database = 'ghbf_package_test_' + uuid.uuid4().hex
    url = postgres_test_database_url.rsplit('/', 1)[0] + '/' + database
    admin = await asyncpg.connect(postgres_test_database_url.replace('postgresql+asyncpg://', 'postgresql://'))
    await admin.execute(f'CREATE DATABASE "{database}"')
    engine = create_async_engine(url)
    try:
        connection = await asyncpg.connect(url.replace('postgresql+asyncpg://', 'postgresql://'))
        try:
            sql, revision = fresh_database_sql()
            await connection.execute(sql)
            assert await connection.fetchval('SELECT version_num FROM alembic_version') == revision
            assert await connection.fetchval("SELECT relrowsecurity FROM pg_class WHERE relname='wallets'") is True
        finally:
            await connection.close()
        async with AsyncSession(engine, expire_on_commit=False) as destination:
            vault = EnvSecretStorage({})
            result = await restore(destination, payload, vault)
            await destination.commit()
            assert result['tenant_id'] == str(tenant_id)
            await assert_restored(destination, vault, tenant_id, wallet_id)
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.close()
