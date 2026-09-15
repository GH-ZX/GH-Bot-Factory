import enum
import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.database import Base, TimestampMixin, UUIDMixin


class TransactionType(str, enum.Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"


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
