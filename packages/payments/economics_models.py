from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.database import Base, TimestampMixin, UUIDMixin
from packages.payments.models import PaymentMethodConfig, Wallet
from packages.tenants.models import JSON_TYPE


class AssetLedgerTransactionType(str, enum.Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    CONVERSION_IN = "CONVERSION_IN"
    CONVERSION_OUT = "CONVERSION_OUT"
    ADJUSTMENT = "ADJUSTMENT"


class WalletHoldStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class FxPolicyMode(str, enum.Enum):
    PARITY = "PARITY"
    FIXED_RATE = "FIXED_RATE"


class AutoCreditTarget(str, enum.Enum):
    ASSET_WALLET = "ASSET_WALLET"
    SETTLEMENT_WALLET = "SETTLEMENT_WALLET"


class FlexibleDepositStatus(str, enum.Enum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SETTLED_REVIEW = "SETTLED_REVIEW"
    CREDITED = "CREDITED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"
    REVERSED = "REVERSED"


class AssetWallet(Base, UUIDMixin, TimestampMixin):
    """High-precision tenant/user balance for an explicit crypto/stablecoin asset identity."""

    __tablename__ = "asset_wallets"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "asset", "network", name="uq_asset_wallet_identity"
        ),
        Index("ix_asset_wallet_tenant_user", "tenant_id", "user_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset: Mapped[str] = mapped_column(String(24), nullable=False)
    network: Mapped[str] = mapped_column(String(64), nullable=False, default="OFFCHAIN")
    balance: Mapped[Decimal] = mapped_column(
        Numeric(36, 18), nullable=False, default=Decimal(0)
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    transactions: Mapped[list[AssetLedgerTransaction]] = relationship(
        "AssetLedgerTransaction",
        back_populates="wallet",
        cascade="all, delete-orphan",
        order_by="AssetLedgerTransaction.created_at",
    )


class AssetLedgerTransaction(Base, UUIDMixin, TimestampMixin):
    """Immutable high-precision asset ledger event with database idempotency."""

    __tablename__ = "asset_ledger_transactions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_asset_ledger_idempotency"),
        Index("ix_asset_ledger_wallet_created", "wallet_id", "created_at"),
        Index("ix_asset_ledger_tenant_ref", "tenant_id", "reference_type", "reference_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("asset_wallets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transaction_type: Mapped[AssetLedgerTransactionType] = mapped_column(
        Enum(AssetLedgerTransactionType, name="asset_ledger_transaction_type_enum", native_enum=False),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(36, 18), nullable=False)
    balance_before: Mapped[Decimal] = mapped_column(Numeric(36, 18), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(36, 18), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reference_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    wallet: Mapped[AssetWallet] = relationship("AssetWallet", back_populates="transactions")


class WalletHold(Base, UUIDMixin, TimestampMixin):
    """Reservation against a fiat settlement wallet without mutating booked balance."""

    __tablename__ = "wallet_holds"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_wallet_hold_idempotency"),
        Index("ix_wallet_hold_wallet_status", "wallet_id", "status"),
        Index("ix_wallet_hold_expires", "status", "expires_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[WalletHoldStatus] = mapped_column(
        Enum(WalletHoldStatus, name="wallet_hold_status_enum", native_enum=False),
        nullable=False,
        default=WalletHoldStatus.ACTIVE,
    )
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(160), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    wallet: Mapped[Wallet] = relationship("Wallet")


class FxPolicy(Base, UUIDMixin, TimestampMixin):
    """Explicit tenant-owned asset -> fiat conversion policy.

    There is intentionally no implicit stablecoin parity. A PARITY policy is an explicit
    merchant decision and can be bounded by max_auto_credit_amount to limit exposure.
    """

    __tablename__ = "fx_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "from_asset", "from_network", "to_currency", name="uq_fx_policy_scope"
        ),
        Index("ix_fx_policy_tenant_enabled", "tenant_id", "is_enabled"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_asset: Mapped[str] = mapped_column(String(24), nullable=False)
    from_network: Mapped[str] = mapped_column(String(64), nullable=False, default="ANY")
    to_currency: Mapped[str] = mapped_column(String(12), nullable=False)
    mode: Mapped[FxPolicyMode] = mapped_column(
        Enum(FxPolicyMode, name="fx_policy_mode_enum", native_enum=False), nullable=False
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(36, 18), nullable=False)
    max_auto_credit_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)


class FlexibleDepositSession(Base, UUIDMixin, TimestampMixin):
    """Open-amount provider invoice whose received asset amount is unknown at creation time."""

    __tablename__ = "flexible_deposit_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_flexible_deposit_idempotency"),
        UniqueConstraint(
            "tenant_id", "provider", "provider_deposit_id", name="uq_flexible_deposit_provider_id"
        ),
        Index("ix_flexible_deposit_tenant_user", "tenant_id", "user_id", "created_at"),
        Index("ix_flexible_deposit_status", "status", "updated_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_method_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payment_method_configs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_deposit_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[FlexibleDepositStatus] = mapped_column(
        Enum(FlexibleDepositStatus, name="flexible_deposit_status_enum", native_enum=False),
        nullable=False,
        default=FlexibleDepositStatus.CREATED,
    )
    checkout_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    asset: Mapped[str | None] = mapped_column(String(24), nullable=True)
    network: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_received: Mapped[Decimal | None] = mapped_column(Numeric(36, 18), nullable=True)
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(36, 18), nullable=True)
    auto_credit_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_credit_target: Mapped[AutoCreditTarget] = mapped_column(
        Enum(AutoCreditTarget, name="auto_credit_target_enum", native_enum=False),
        nullable=False,
        default=AutoCreditTarget.ASSET_WALLET,
    )
    auto_credit_basis: Mapped[str] = mapped_column(String(32), nullable=False, default="NET_AFTER_FEE")
    settlement_currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    fx_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fx_policies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    credited_amount: Mapped[Decimal | None] = mapped_column(Numeric(36, 18), nullable=True)
    credited_asset: Mapped[str | None] = mapped_column(String(24), nullable=True)
    credited_currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    credited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_snapshot_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    policy_snapshot_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)

    payment_method: Mapped[PaymentMethodConfig] = relationship("PaymentMethodConfig")
    fx_policy: Mapped[FxPolicy | None] = relationship("FxPolicy")
