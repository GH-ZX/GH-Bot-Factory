from aiogram import Router

from packages.telegram.routers.account import router as account_router
from packages.telegram.routers.admin import router as admin_router
from packages.telegram.routers.catalog import router as catalog_router
from packages.telegram.routers.menu import router as menu_router
from packages.telegram.routers.orders import router as orders_router
from packages.telegram.routers.payments import router as payments_router
from packages.telegram.routers.start import router as start_router


def copy_router(source: Router) -> Router:
    """Clones a router's observers, handlers, and middlewares into a fresh unattached Router instance."""
    target = Router(name=source.name)
    for event_name, source_observer in source.observers.items():
        target_observer = target.observers[event_name]
        for handler in source_observer.handlers:
            target_observer.handlers.append(handler)
        for m in getattr(source_observer.middleware, "_middlewares", []):
            target_observer.middleware.register(m)
        for m in getattr(source_observer.outer_middleware, "_middlewares", []):
            target_observer.outer_middleware.register(m)
    for sub in source.sub_routers:
        target.include_router(copy_router(sub))
    return target


_FEATURE_ROUTERS = (
    start_router,
    admin_router,
    menu_router,
    catalog_router,
    orders_router,
    payments_router,
    account_router,
)


def get_root_router() -> Router:
    """Combines all modular feature routers under a fresh root router instance."""
    root_router = Router(name="root_telegram_router")
    for r in _FEATURE_ROUTERS:
        root_router.include_router(copy_router(r))
    return root_router
