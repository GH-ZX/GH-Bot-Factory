import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.database import Base, TimestampMixin, UUIDMixin
from packages.payments.state_machine import PaymentIntentStatus, PaymentStateMachine
from packages.tenants.models import JSON_TYPE, Tenant, User


class TransactionType(str, enum.Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"


class PaymentTransactionType(str, enum.Enum):
    AUTHORIZATION = "AUTHORIZATION"
    CAPTURE = "CAPTURE"
    SETTLEMENT = "SETTLEMENT"
    REFUND = "REFUND"
    VOID = "VOID"


class Wallet(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "currency", name="uq_wallet_tenant_user_currency"),
        Index("ix_wallet_tenant_user", "tenant_id", "user_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    transactions: Mapped[list["LedgerTransaction"]] = relationship(
        "LedgerTransaction",
        back_populates="wallet",
        cascade="all, delete-orphan",
        order_by="LedgerTransaction.created_at",
    )


class LedgerTransaction(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "ledger_transactions"
    __table_args__ = (
        Index("ix_ledger_wallet_type", "wallet_id", "transaction_type"),
        Index("ix_ledger_tenant_ref", "tenant_id", "reference_type", "reference_id"),
        Index(
            "uq_refund_idempotency",
            "wallet_id",
            "reference_type",
            "reference_id",
            unique=True,
            postgresql_where=text(
                "transaction_type = 'REFUND' AND reference_type IS NOT NULL AND reference_id IS NOT NULL"
            ),
            sqlite_where=text(
                "transaction_type = 'REFUND' AND reference_type IS NOT NULL AND reference_id IS NOT NULL"
            ),
        ),
        Index(
            "uq_settlement_idempotency",
            "wallet_id",
            "reference_type",
            "reference_id",
            unique=True,
            postgresql_where=text(
                "transaction_type = 'CREDIT' AND reference_type = 'PAYMENT_SETTLEMENT' AND reference_id IS NOT NULL"
            ),
            sqlite_where=text(
                "transaction_type = 'CREDIT' AND reference_type = 'PAYMENT_SETTLEMENT' AND reference_id IS NOT NULL"
            ),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transaction_type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, name="transaction_type_enum", native_enum=False),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    balance_before: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reference_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    reference_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    wallet: Mapped[Wallet] = relationship("Wallet", back_populates="transactions")


class PaymentIntent(Base, UUIDMixin, TimestampMixin):
    """Durable payment intent representing an authoritative payment attempt for an Order."""

    __tablename__ = "payment_intents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_payment_intent_tenant_idempotency"),
        Index("ix_payment_intent_tenant_status", "tenant_id", "status"),
        Index("ix_payment_intent_tenant_order", "tenant_id", "order_id"),
        Index("ix_payment_intent_provider_id", "provider", "provider_payment_id"),
        Index(
            "uq_active_order_payment_intent",
            "tenant_id",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('CREATED', 'PENDING', 'PROCESSING')"),
            sqlite_where=text("status IN ('CREATED', 'PENDING', 'PROCESSING')"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[PaymentIntentStatus] = mapped_column(
        Enum(PaymentIntentStatus, name="payment_intent_status_enum", native_enum=False),
        default=PaymentIntentStatus.CREATED,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )

    tenant: Mapped["Tenant"] = relationship("Tenant")
    user: Mapped["User"] = relationship("User")
    transactions: Mapped[list["PaymentTransaction"]] = relationship(
        "PaymentTransaction",
        back_populates="payment_intent",
        cascade="all, delete-orphan",
        order_by="PaymentTransaction.created_at",
    )

    def transition_to(self, target: PaymentIntentStatus) -> PaymentIntentStatus:
        return PaymentStateMachine.transition(self, target)


class PaymentTransaction(Base, UUIDMixin, TimestampMixin):
    """Specific payment event/transaction (authorization, capture, settlement, refund, void)."""

    __tablename__ = "payment_transactions"
    __table_args__ = (
        Index("ix_pay_tx_tenant_intent", "tenant_id", "payment_intent_id"),
        Index("ix_pay_tx_provider_tx", "provider_tx_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payment_intents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transaction_type: Mapped[PaymentTransactionType] = mapped_column(
        Enum(PaymentTransactionType, name="payment_tx_type_enum", native_enum=False),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_tx_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    reference_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )

    payment_intent: Mapped[PaymentIntent] = relationship("PaymentIntent", back_populates="transactions")


class PaymentProviderConfig(Base, UUIDMixin, TimestampMixin):
    """Tenant-specific payment provider configuration and credentials pointer."""

    __tablename__ = "payment_provider_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_name", name="uq_tenant_payment_provider"),
        Index("ix_pay_prov_cfg_tenant_enabled", "tenant_id", "is_enabled"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    credentials_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    webhook_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )

    tenant: Mapped["Tenant"] = relationship("Tenant")


class PaymentWebhookEvent(Base, UUIDMixin, TimestampMixin):
    """Audit log and deduplication record for incoming provider webhook events."""

    __tablename__ = "payment_webhook_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "provider_event_id", name="uq_webhook_tenant_provider_event"),
        Index("ix_webhook_tenant_processed", "tenant_id", "processed"),
        Index("ix_webhook_intent", "payment_intent_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(100), nullable=False)
    payment_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_intents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )
