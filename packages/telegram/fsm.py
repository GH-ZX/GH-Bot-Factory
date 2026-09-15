import uuid
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey

from packages.telegram.context import TenantContext


class OrderCreationFSM(StatesGroup):
    """FSM states for interactive order creation."""

    selecting_variant = State()
    entering_quantity = State()
    confirming_checkout = State()


class WalletTopupFSM(StatesGroup):
    """FSM states for entering deposit/topup amount."""

    entering_amount = State()
    confirming_payment = State()


class TenantFSMHelper:
    """Utilities ensuring FSM state never crosses tenant, bot, or user boundaries."""

    @staticmethod
    def get_scoped_storage_key(
        tenant_context: TenantContext,
        chat_id: int,
        user_id: int,
    ) -> StorageKey:
        """Constructs a strictly scoped StorageKey bound to bot, chat, and user.
        
        Guarantees that state cannot leak across different bots or tenants even if chat_id matches.
        """
        # We use a combined bot identifier that incorporates tenant_id
        return StorageKey(
            bot_id=tenant_context.telegram_bot_id,
            chat_id=chat_id,
            user_id=user_id,
            destiny=f"tenant:{tenant_context.tenant_id}:bot:{tenant_context.bot_id}",
        )

    @staticmethod
    async def set_tenant_data(
        fsm_context: FSMContext,
        tenant_id: uuid.UUID,
        key: str,
        value: Any,
    ) -> None:
        """Stores data in FSM with mandatory tenant prefixing."""
        data = await fsm_context.get_data()
        tenant_data = data.get(f"tenant_{tenant_id}", {})
        tenant_data[key] = value
        await fsm_context.update_data({f"tenant_{tenant_id}": tenant_data})

    @staticmethod
    async def get_tenant_data(
        fsm_context: FSMContext,
        tenant_id: uuid.UUID,
        key: str,
        default: Any = None,
    ) -> Any:
        """Retrieves tenant-isolated data from FSM."""
        data = await fsm_context.get_data()
        tenant_data = data.get(f"tenant_{tenant_id}", {})
        return tenant_data.get(key, default)
