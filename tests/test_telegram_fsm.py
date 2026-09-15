import uuid

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from packages.telegram.context import TenantContext
from packages.telegram.fsm import OrderCreationFSM, TenantFSMHelper, WalletTopupFSM


@pytest.mark.asyncio
async def test_fsm_isolation_between_tenants_and_bots():
    storage = MemoryStorage()

    tenant_1_id = uuid.uuid4()
    tenant_2_id = uuid.uuid4()
    bot_1_id = uuid.uuid4()
    bot_2_id = uuid.uuid4()
    user_id = 998877  # Same telegram user

    ctx_tenant_1 = TenantContext(
        bot_id=bot_1_id,
        tenant_id=tenant_1_id,
        telegram_bot_id=1001,
        telegram_user_id=user_id,
        user_id=uuid.uuid4(),
        correlation_id="cid-1",
        tenant_name="Tenant 1",
        display_name="Bot 1",
    )

    ctx_tenant_2 = TenantContext(
        bot_id=bot_2_id,
        tenant_id=tenant_2_id,
        telegram_bot_id=1002,
        telegram_user_id=user_id,
        user_id=uuid.uuid4(),
        correlation_id="cid-2",
        tenant_name="Tenant 2",
        display_name="Bot 2",
    )

    # Scoped keys for User 998877 in Tenant 1 vs Tenant 2
    key_t1 = TenantFSMHelper.get_scoped_storage_key(ctx_tenant_1, chat_id=user_id, user_id=user_id)
    key_t2 = TenantFSMHelper.get_scoped_storage_key(ctx_tenant_2, chat_id=user_id, user_id=user_id)

    fsm_t1 = FSMContext(storage=storage, key=key_t1)
    fsm_t2 = FSMContext(storage=storage, key=key_t2)

    # Set state in Tenant 1
    await fsm_t1.set_state(OrderCreationFSM.selecting_variant)
    await TenantFSMHelper.set_tenant_data(fsm_t1, tenant_1_id, "selected_sku", "SKU-T1-100")

    # Set different state in Tenant 2
    await fsm_t2.set_state(WalletTopupFSM.entering_amount)
    await TenantFSMHelper.set_tenant_data(fsm_t2, tenant_2_id, "topup_amount", "50.00")

    # Assert complete state isolation
    state_t1 = await fsm_t1.get_state()
    state_t2 = await fsm_t2.get_state()

    assert state_t1 == OrderCreationFSM.selecting_variant.state
    assert state_t2 == WalletTopupFSM.entering_amount.state

    # Data stored in Tenant 1 must not be visible in Tenant 2
    data_t1_val = await TenantFSMHelper.get_tenant_data(fsm_t1, tenant_1_id, "selected_sku")
    data_t2_val_leak = await TenantFSMHelper.get_tenant_data(fsm_t2, tenant_1_id, "selected_sku")

    assert data_t1_val == "SKU-T1-100"
    assert data_t2_val_leak is None


@pytest.mark.asyncio
async def test_fsm_isolation_between_distinct_users():
    storage = MemoryStorage()
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()

    ctx = TenantContext(
        bot_id=bot_id,
        tenant_id=tenant_id,
        telegram_bot_id=5000,
        telegram_user_id=1,
        user_id=uuid.uuid4(),
        correlation_id="cid",
        tenant_name="Store",
        display_name="StoreBot",
    )

    key_user_1 = TenantFSMHelper.get_scoped_storage_key(ctx, chat_id=111, user_id=111)
    key_user_2 = TenantFSMHelper.get_scoped_storage_key(ctx, chat_id=222, user_id=222)

    fsm_1 = FSMContext(storage=storage, key=key_user_1)
    fsm_2 = FSMContext(storage=storage, key=key_user_2)

    await fsm_1.set_state(OrderCreationFSM.confirming_checkout)

    assert await fsm_1.get_state() == OrderCreationFSM.confirming_checkout.state
    assert await fsm_2.get_state() is None
