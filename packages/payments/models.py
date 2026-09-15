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
    Integer,
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


class PaymentIntentPurpose(str, enum.Enum):
    ORDER_PAYMENT = "ORDER_PAYMENT"
    WALLET_TOPUP = "WALLET_TOPUP"


class PaymentTransactionType(str, enum.Enum):
    AUTHORIZATION = "AUTHORIZATION"
    CAPTURE = "CAPTURE"
    SETTLEMENT = "SETTLEMENT"
    REFUND = "REFUND"
    VOID = "VOID"


class WalletTopUpReversalStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    FUNDS_RESERVED = "FUNDS_RESERVED"
    PROCESSING = "PROCESSING"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    COMPLETED = "COMPLETED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class PaymentReconciliationEventStatus(str, enum.Enum):
    PROCESSED = "PROCESSED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    IGNORED = "IGNORED"


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
        Index(
            "uq_topup_reversal_debit",
            "wallet_id",
            "reference_type",
            "reference_id",
            unique=True,
            postgresql_where=text(
                "transaction_type = 'DEBIT' AND reference_type = 'WALLET_TOPUP_REVERSAL' AND reference_id IS NOT NULL"
            ),
            sqlite_where=text(
                "transaction_type = 'DEBIT' AND reference_type = 'WALLET_TOPUP_REVERSAL' AND reference_id IS NOT NULL"
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
    """Durable provider payment attempt for an order payment or wallet top-up."""

    __tablename__ = "payment_intents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_payment_intent_tenant_idempotency"),
        Index("ix_payment_intent_tenant_status", "tenant_id", "status"),
        Index("ix_payment_intent_tenant_order", "tenant_id", "order_id"),
        Index("ix_payment_intent_provider_id", "provider", "provider_payment_id"),
        Index(
            "ix_payment_intent_tenant_created_purpose_status",
            "tenant_id",
            "created_at",
            "purpose",
            "status",
        ),
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
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    purpose: Mapped[PaymentIntentPurpose] = mapped_column(
        Enum(PaymentIntentPurpose, name="payment_intent_purpose_enum", native_enum=False),
        default=PaymentIntentPurpose.ORDER_PAYMENT,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    checkout_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
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
        Index(
            "uq_topup_reversal_payment_tx",
            "tenant_id",
            "payment_intent_id",
            "reference_id",
            unique=True,
            postgresql_where=text(
                "transaction_type = 'REFUND' AND reference_id LIKE 'topup_reversal:%'"
            ),
            sqlite_where=text(
                "transaction_type = 'REFUND' AND reference_id LIKE 'topup_reversal:%'"
            ),
        ),
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
    provider_tx_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )

    payment_intent: Mapped[PaymentIntent] = relationship("PaymentIntent", back_populates="transactions")


class WalletTopUpReversal(Base, UUIDMixin, TimestampMixin):
    """Durable saga record for reversing a settled wallet top-up exactly once."""

    __tablename__ = "wallet_topup_reversals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "payment_intent_id",
            name="uq_topup_reversal_tenant_intent",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_topup_reversal_tenant_idempotency",
        ),
        Index("ix_topup_reversal_status_next", "status", "next_attempt_at"),
        Index("ix_topup_reversal_tenant_status", "tenant_id", "status"),
        Index(
            "ix_topup_reversal_tenant_completed_status",
            "tenant_id",
            "completed_at",
            "status",
        ),
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
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    original_provider_payment_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_refund_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[WalletTopUpReversalStatus] = mapped_column(
        Enum(
            WalletTopUpReversalStatus,
            name="wallet_topup_reversal_status_enum",
            native_enum=False,
        ),
        default=WalletTopUpReversalStatus.REQUESTED,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    payment_intent: Mapped["PaymentIntent"] = relationship("PaymentIntent")
    wallet: Mapped["Wallet"] = relationship("Wallet")
    user: Mapped["User"] = relationship("User")


class PaymentReconciliationEvent(Base, UUIDMixin, TimestampMixin):
    """Provider transaction observation used for durable reconciliation and audit."""

    __tablename__ = "payment_reconciliation_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_event_id",
            "event_type",
            name="uq_payment_reconciliation_provider_event",
        ),
        Index("ix_payment_reconciliation_tenant_status", "tenant_id", "status"),
        Index("ix_payment_reconciliation_intent", "payment_intent_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payment_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_intents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[PaymentReconciliationEventStatus] = mapped_column(
        Enum(
            PaymentReconciliationEventStatus,
            name="payment_reconciliation_event_status_enum",
            native_enum=False,
        ),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    classification: Mapped[str] = mapped_column(String(100), nullable=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    payment_intent: Mapped["PaymentIntent | None"] = relationship("PaymentIntent")


class FinancialResolutionCaseStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"


class FinancialResolutionCase(Base, UUIDMixin, TimestampMixin):
    """Durable operator case for financial anomalies that require explicit review.

    A case is intentionally separate from the provider observation/reversal rows. Those
    records remain immutable financial evidence; this row models the human workflow
    around that evidence, including assignment, optimistic versioning, and resolution.
    """

    __tablename__ = "financial_resolution_cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_key", name="uq_financial_case_tenant_source"),
        Index("ix_financial_case_tenant_status", "tenant_id", "status"),
        Index("ix_financial_case_tenant_type", "tenant_id", "case_type"),
        Index("ix_financial_case_wallet", "wallet_id"),
        Index("ix_financial_case_assignee", "assigned_to_user_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_key: Mapped[str] = mapped_column(String(160), nullable=False)
    case_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="HIGH")
    status: Mapped[FinancialResolutionCaseStatus] = mapped_column(
        Enum(
            FinancialResolutionCaseStatus,
            name="financial_resolution_case_status_enum",
            native_enum=False,
        ),
        default=FinancialResolutionCaseStatus.OPEN,
        nullable=False,
    )
    payment_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_intents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    wallet_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wallets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reversal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wallet_topup_reversals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reconciliation_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_reconciliation_events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    resolution_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    payment_intent: Mapped["PaymentIntent | None"] = relationship("PaymentIntent")
    wallet: Mapped["Wallet | None"] = relationship("Wallet")
    reversal: Mapped["WalletTopUpReversal | None"] = relationship("WalletTopUpReversal")
    reconciliation_event: Mapped["PaymentReconciliationEvent | None"] = relationship(
        "PaymentReconciliationEvent"
    )


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
