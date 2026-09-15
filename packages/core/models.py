"""Centralized models registry for Alembic and metadata reflection."""

from packages.commerce.models import Category, Order, OrderItem, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.core.database import Base
from packages.fulfillment.models import FulfillmentAttempt, FulfillmentStatus
from packages.payments.models import (
    LedgerTransaction,
    PaymentIntent,
    PaymentProviderConfig,
    PaymentTransaction,
    PaymentTransactionType,
    PaymentWebhookEvent,
    TransactionType,
    Wallet,
)
from packages.payments.state_machine import PaymentIntentStatus
from packages.providers.models import (
    Provider,
    ProviderCredential,
    ProviderHealthStatus,
    ProviderModel,
    ProviderProductMapping,
)
from packages.telegram.models import Bot, TenantTelegramUser
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

__all__ = [
    "AuditLog",
    "Base",
    "Bot",
    "Category",
    "FulfillmentAttempt",
    "FulfillmentStatus",
    "LedgerTransaction",
    "Membership",
    "Order",
    "OrderItem",
    "OrderStatus",
    "PaymentIntent",
    "PaymentIntentStatus",
    "PaymentProviderConfig",
    "PaymentTransaction",
    "PaymentTransactionType",
    "PaymentWebhookEvent",
    "Product",
    "ProductVariant",
    "Provider",
    "ProviderCredential",
    "ProviderHealthStatus",
    "ProviderModel",
    "ProviderProductMapping",
    "Role",
    "Tenant",
    "TenantTelegramUser",
    "TransactionType",
    "User",
    "Wallet",
]
