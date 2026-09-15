
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.telegram.models import Bot
from packages.tenants.models import Tenant


@pytest.mark.asyncio
async def test_valid_bot_registration(db_session: AsyncSession):
    tenant = Tenant(name="Bot Store Alpha", slug="bot-store-alpha")
    db_session.add(tenant)
    await db_session.flush()

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=111222333,
        username="alpha_store_bot",
        display_name="Alpha Store Bot",
        token_secret_ref="ENV_BOT_ALPHA_TOKEN",
        is_enabled=True,
    )
    db_session.add(bot)
    await db_session.commit()

    stmt = select(Bot).where(Bot.telegram_bot_id == 111222333)
    res = await db_session.execute(stmt)
    retrieved = res.scalar_one_or_none()

    assert retrieved is not None
    assert retrieved.tenant_id == tenant.id
    assert retrieved.display_name == "Alpha Store Bot"
    assert retrieved.is_enabled is True


@pytest.mark.asyncio
async def test_duplicate_telegram_bot_identity_prevented(db_session: AsyncSession):
    tenant1 = Tenant(name="Tenant 1", slug="t1")
    tenant2 = Tenant(name="Tenant 2", slug="t2")
    db_session.add_all([tenant1, tenant2])
    await db_session.flush()

    bot1 = Bot(
        tenant_id=tenant1.id,
        telegram_bot_id=999888777,
        username="unique_bot",
        display_name="Unique Bot",
        token_secret_ref="SECRET_1",
    )
    db_session.add(bot1)
    await db_session.commit()

    # Attempt to assign the SAME telegram_bot_id to tenant2
    bot2 = Bot(
        tenant_id=tenant2.id,
        telegram_bot_id=999888777,  # Duplicate!
        username="imposter_bot",
        display_name="Imposter Bot",
        token_secret_ref="SECRET_2",
    )
    db_session.add(bot2)

    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_multiple_bots_per_tenant_supported(db_session: AsyncSession):
    tenant = Tenant(name="Multi Bot Tenant", slug="multi-bot-tenant")
    db_session.add(tenant)
    await db_session.flush()

    bot_main = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=10101,
        username="main_bot",
        display_name="Main Sales Bot",
        token_secret_ref="SECRET_MAIN",
    )
    bot_backup = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=20202,
        username="backup_bot",
        display_name="Backup Support Bot",
        token_secret_ref="SECRET_BACKUP",
    )
    db_session.add_all([bot_main, bot_backup])
    await db_session.commit()

    stmt = select(Bot).where(Bot.tenant_id == tenant.id)
    res = await db_session.execute(stmt)
    bots = res.scalars().all()

    assert len(bots) == 2
    bot_ids = {b.telegram_bot_id for b in bots}
    assert bot_ids == {10101, 20202}


@pytest.mark.asyncio
async def test_disabled_bot_state(db_session: AsyncSession):
    tenant = Tenant(name="Disabled Test Tenant", slug="dis-tenant")
    db_session.add(tenant)
    await db_session.flush()

    bot = Bot(
        tenant_id=tenant.id,
        telegram_bot_id=555444,
        username="inactive_bot",
        display_name="Inactive Bot",
        token_secret_ref="SECRET_INACTIVE",
        is_enabled=False,
    )
    db_session.add(bot)
    await db_session.commit()

    stmt = select(Bot).where(Bot.tenant_id == tenant.id, Bot.is_enabled.is_(True))
    res = await db_session.execute(stmt)
    assert res.scalar_one_or_none() is None
