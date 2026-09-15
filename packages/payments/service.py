import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.exceptions import (
    InsufficientFundsError,
    LedgerIntegrityError,
    TenantAccessViolationError,
)
from packages.payments.models import LedgerTransaction, TransactionType, Wallet


class LedgerService:
    """Provides auditable double-entry balance modifications and reconciliation."""

    @staticmethod
    async def get_or_create_wallet(
        session: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        currency: str = "USD",
    ) -> Wallet:
        stmt = select(Wallet).where(
            Wallet.tenant_id == tenant_id,
            Wallet.user_id == user_id,
            Wallet.currency == currency,
        )
        result = await session.execute(stmt)
        wallet = result.scalar_one_or_none()

        if wallet is None:
            wallet = Wallet(
                tenant_id=tenant_id,
                user_id=user_id,
                currency=currency,
                balance=Decimal("0.00"),
                is_active=True,
            )
            session.add(wallet)
            await session.flush()

        return wallet

    @classmethod
    async def credit(
        cls,
        session: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        reference_id: str | None = None,
        reference_type: str | None = None,
        description: str | None = None,
    ) -> LedgerTransaction:
        if amount <= Decimal("0.00"):
            raise ValueError("Credit amount must be strictly positive.")

        balance_before = wallet.balance
        balance_after = balance_before + amount
        wallet.balance = balance_after

        tx = LedgerTransaction(
            tenant_id=wallet.tenant_id,
            wallet_id=wallet.id,
            transaction_type=TransactionType.CREDIT,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_id=reference_id,
            reference_type=reference_type,
            description=description,
        )
        session.add(tx)
        await session.flush()
        return tx

    @classmethod
    async def debit(
        cls,
        session: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        reference_id: str | None = None,
        reference_type: str | None = None,
        description: str | None = None,
    ) -> LedgerTransaction:
        if amount <= Decimal("0.00"):
            raise ValueError("Debit amount must be strictly positive.")

        if wallet.balance < amount:
            raise InsufficientFundsError(
                f"Insufficient funds: available {wallet.balance} {wallet.currency}, required {amount} {wallet.currency}."
            )

        balance_before = wallet.balance
        balance_after = balance_before - amount
        wallet.balance = balance_after

        tx = LedgerTransaction(
            tenant_id=wallet.tenant_id,
            wallet_id=wallet.id,
            transaction_type=TransactionType.DEBIT,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_id=reference_id,
            reference_type=reference_type,
            description=description,
        )
        session.add(tx)
        await session.flush()
        return tx

    @classmethod
    async def refund(
        cls,
        session: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        reference_id: str | None = None,
        reference_type: str | None = None,
        description: str | None = None,
    ) -> LedgerTransaction:
        if amount <= Decimal("0.00"):
            raise ValueError("Refund amount must be strictly positive.")

        balance_before = wallet.balance
        balance_after = balance_before + amount
        wallet.balance = balance_after

        tx = LedgerTransaction(
            tenant_id=wallet.tenant_id,
            wallet_id=wallet.id,
            transaction_type=TransactionType.REFUND,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_id=reference_id,
            reference_type=reference_type,
            description=description or "Refund",
        )
        session.add(tx)
        await session.flush()
        return tx

    @classmethod
    async def adjust(
        cls,
        session: AsyncSession,
        wallet: Wallet,
        target_balance: Decimal,
        reason: str,
    ) -> LedgerTransaction:
        if target_balance < Decimal("0.00"):
            raise ValueError("Target balance cannot be negative.")

        balance_before = wallet.balance
        balance_after = target_balance
        diff = abs(balance_after - balance_before)
        wallet.balance = balance_after

        tx = LedgerTransaction(
            tenant_id=wallet.tenant_id,
            wallet_id=wallet.id,
            transaction_type=TransactionType.ADJUSTMENT,
            amount=diff,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_id=None,
            reference_type="MANUAL_ADJUSTMENT",
            description=reason,
        )
        session.add(tx)
        await session.flush()
        return tx

    @classmethod
    async def reconstruct_and_verify_balance(
        cls,
        session: AsyncSession,
        wallet_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> tuple[Decimal, bool]:
        """Audits all ledger entries from inception and checks match against current wallet balance."""
        wallet = await session.get(Wallet, wallet_id)
        if wallet is None:
            raise ValueError(f"Wallet with id {wallet_id} does not exist.")

        if wallet.tenant_id != tenant_id:
            raise TenantAccessViolationError(
                f"Tenant {tenant_id} cannot verify wallet {wallet_id} belonging to {wallet.tenant_id}."
            )

        stmt = (
            select(LedgerTransaction)
            .where(LedgerTransaction.wallet_id == wallet_id)
            .order_by(LedgerTransaction.created_at.asc(), LedgerTransaction.id.asc())
        )
        result = await session.execute(stmt)
        transactions = result.scalars().all()

        reconstructed = Decimal("0.00")
        for tx in transactions:
            if tx.transaction_type in (TransactionType.CREDIT, TransactionType.REFUND):
                reconstructed += tx.amount
            elif tx.transaction_type == TransactionType.DEBIT:
                reconstructed -= tx.amount
            elif tx.transaction_type == TransactionType.ADJUSTMENT:
                reconstructed = tx.balance_after

        is_valid = reconstructed == wallet.balance
        if not is_valid:
            raise LedgerIntegrityError(
                f"Ledger discrepancy detected for wallet {wallet_id}: "
                f"stored balance={wallet.balance}, reconstructed={reconstructed}."
            )

        return reconstructed, True
