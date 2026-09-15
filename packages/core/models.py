"""Centralized models registry for Alembic and metadata reflection."""

from packages.commerce.models import Category, Order, OrderItem, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.core.database import Base
from packages.payments.models import LedgerTransaction, TransactionType, Wallet
from packages.providers.models import ProviderModel, TenantProviderConfig
from packages.telegram.models import Bot, TenantTelegramUser
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

__all__ = [
    "AuditLog",
    "Base",
    "Bot",
    "Category",
    "LedgerTransaction",
    "Membership",
    "Order",
    "OrderItem",
    "OrderStatus",
    "Product",
    "ProductVariant",
    "ProviderModel",
    "Role",
    "Tenant",
    "TenantProviderConfig",
    "TenantTelegramUser",
    "TransactionType",
    "User",
    "Wallet",
]

