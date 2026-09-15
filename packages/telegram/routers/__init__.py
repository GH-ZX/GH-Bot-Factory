from aiogram import Router

from packages.telegram.routers.account import router as account_router
from packages.telegram.routers.catalog import router as catalog_router
from packages.telegram.routers.menu import router as menu_router
from packages.telegram.routers.orders import router as orders_router
from packages.telegram.routers.start import router as start_router


def get_root_router() -> Router:
    """Combines all modular feature routers under a root router."""
    root_router = Router(name="root_telegram_router")
    root_router.include_router(start_router)
    root_router.include_router(menu_router)
    root_router.include_router(catalog_router)
    root_router.include_router(orders_router)
    root_router.include_router(account_router)
    return root_router
