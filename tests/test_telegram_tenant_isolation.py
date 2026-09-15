import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.telegram.models import Bot, TenantTelegramUser
from packages.tenants.models import Membership, Role, Tenant, User


@pytest.mark.asyncio
async def test_tenant_bot_isolation(db_session: AsyncSession):
    tenant_a = Tenant(name="Tenant Alpha", slug="tenant-alpha")
    tenant_b = Tenant(name="Tenant Beta", slug="tenant-beta")
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()

    bot_a = Bot(
        tenant_id=tenant_a.id,
        telegram_bot_id=11111,
        username="alpha_bot",
        display_name="Alpha Bot",
        token_secret_ref="TOKEN_A",
    )
    bot_b = Bot(
        tenant_id=tenant_b.id,
        telegram_bot_id=22222,
        username="beta_bot",
        display_name="Beta Bot",
        token_secret_ref="TOKEN_B",
    )
    db_session.add_all([bot_a, bot_b])
    await db_session.commit()

    # Querying Tenant A's bots must never return Tenant B's bot
    stmt = select(Bot).where(Bot.tenant_id == tenant_a.id)
    res = await db_session.execute(stmt)
    bots_a = res.scalars().all()

    assert len(bots_a) == 1
    assert bots_a[0].username == "alpha_bot"
    assert bots_a[0].telegram_bot_id == 11111


@pytest.mark.asyncio
async def test_same_telegram_user_id_isolated_per_tenant(db_session: AsyncSession):
    """Verifies that the same Telegram user (e.g. ID 7777777) talking to Bot A and Bot B
    is provisioned as two distinct application identities isolated per tenant."""
    tenant_a = Tenant(name="Alpha Store", slug="alpha-store")
    tenant_b = Tenant(name="Beta Store", slug="beta-store")
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()

    bot_a = Bot(
        tenant_id=tenant_a.id,
        telegram_bot_id=1001,
        username="bot_a",
        display_name="Bot A",
        token_secret_ref="SEC_A",
    )
    bot_b = Bot(
        tenant_id=tenant_b.id,
        telegram_bot_id=1002,
        username="bot_b",
        display_name="Bot B",
        token_secret_ref="SEC_B",
    )
    db_session.add_all([bot_a, bot_b])
    await db_session.flush()

    telegram_user_id = 7777777

    # Provision user under Tenant A
    user_a = User(username="telegram_user_7777", is_active=True)
    db_session.add(user_a)
    await db_session.flush()
    mem_a = Membership(tenant_id=tenant_a.id, user_id=user_a.id, role=Role.CUSTOMER)
    binding_a = TenantTelegramUser(
        tenant_id=tenant_a.id,
        bot_id=bot_a.id,
        telegram_user_id=telegram_user_id,
        user_id=user_a.id,
    )
    db_session.add_all([mem_a, binding_a])

    # Provision user under Tenant B
    user_b = User(username="telegram_user_7777", is_active=True)
    db_session.add(user_b)
    await db_session.flush()
    mem_b = Membership(tenant_id=tenant_b.id, user_id=user_b.id, role=Role.CUSTOMER)
    binding_b = TenantTelegramUser(
        tenant_id=tenant_b.id,
        bot_id=bot_b.id,
        telegram_user_id=telegram_user_id,
        user_id=user_b.id,
    )
    db_session.add_all([mem_b, binding_b])
    await db_session.commit()

    # Assert completely separate application user records
    assert user_a.id != user_b.id
    assert binding_a.tenant_id == tenant_a.id
    assert binding_b.tenant_id == tenant_b.id

    # Querying Tenant A telegram users returns only A
    stmt_a = select(TenantTelegramUser).where(TenantTelegramUser.tenant_id == tenant_a.id)
    res_a = await db_session.execute(stmt_a)
    users_a = res_a.scalars().all()
    assert len(users_a) == 1
    assert users_a[0].user_id == user_a.id
    assert users_a[0].user_id != user_b.id
