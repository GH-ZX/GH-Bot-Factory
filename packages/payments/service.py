import asyncio
import logging
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.exceptions import (
    InsufficientFundsError,
    LedgerIntegrityError,
    TenantAccessViolationError,
)
from packages.payments.models import LedgerTransaction, TransactionType, Wallet

logger = logging.getLogger("payments.ledger")

CANONICAL_REFUND_TYPE = "ORDER_FULFILLMENT_REFUND"
CANONICAL_SETTLEMENT_TYPE = "PAYMENT_SETTLEMENT"
CANONICAL_PAYMENT_REFUND_TYPE = "PAYMENT_REFUND"
CANONICAL_TOPUP_REVERSAL_TYPE = "WALLET_TOPUP_REVERSAL"
CANONICAL_FLEXIBLE_DEPOSIT_TYPE = "FLEXIBLE_DEPOSIT_SETTLEMENT"


class LedgerService:
    """Provides auditable double-entry balance modifications and reconciliation."""

    @staticmethod
    async def _lock_wallet(session: AsyncSession, wallet: Wallet) -> Wallet:
        """Serialize every balance mutation on the authoritative wallet row.

        SQLite largely ignores ``FOR UPDATE`` so the fast unit suite keeps its existing
        behavior. PostgreSQL, however, will block competing transactions until the
        current balance mutation commits or rolls back. ``populate_existing`` is
        required because callers commonly pass a Wallet that is already present in the
        session identity map; after waiting for a concurrent transaction we must reload
        the newly committed balance before calculating ``balance_before``.
        """
        stmt = (
            select(Wallet)
            .where(
                Wallet.id == wallet.id,
                Wallet.tenant_id == wallet.tenant_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        locked_wallet = (await session.execute(stmt)).scalar_one_or_none()
        if locked_wallet is None:
            raise ValueError(f"Wallet with id {wallet.id} does not exist for tenant {wallet.tenant_id}.")
        return locked_wallet

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
            try:
                async with session.begin_nested():
                    session.add(wallet)
                    await session.flush()
            except IntegrityError:
                result = await session.execute(stmt)
                wallet = result.scalar_one()

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

        wallet = await cls._lock_wallet(session, wallet)

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
    async def settle_payment(
        cls,
        session: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        payment_intent_id: uuid.UUID,
        description: str | None = None,
    ) -> LedgerTransaction:
        """Atomically and idempotently credits wallet for payment settlement.

        Database unique partial index (uq_settlement_idempotency) guarantees exactly
        one settlement transaction can ever exist for (wallet_id, PAYMENT_SETTLEMENT, payment_intent_id).
        """
        if amount <= Decimal("0.00"):
            raise ValueError("Settlement amount must be strictly positive.")

        wallet = await cls._lock_wallet(session, wallet)

        wallet_id = wallet.id
        ref_id = str(payment_intent_id)
        ref_type = CANONICAL_SETTLEMENT_TYPE

        # 1. Fast-path application-level optimization lookup
        stmt = select(LedgerTransaction).where(
            LedgerTransaction.wallet_id == wallet_id,
            LedgerTransaction.transaction_type == TransactionType.CREDIT,
            LedgerTransaction.reference_type == ref_type,
            LedgerTransaction.reference_id == ref_id,
        )
        existing_tx = (await session.execute(stmt)).scalars().first()
        if existing_tx is not None:
            if existing_tx.amount != amount:
                raise LedgerIntegrityError(
                    f"Payment settlement amount mismatch for intent {ref_id}: "
                    f"existing={existing_tx.amount}, requested={amount}."
                )
            logger.warning(
                "Idempotent settlement hit: settlement for intent %s already exists. Skipping duplicate credit.",
                ref_id,
            )
            return existing_tx

        # 2. Database-enforced atomic credit via savepoint
        try:
            async with session.begin_nested():
                balance_before = wallet.balance
                balance_after = balance_before + amount
                wallet.balance = balance_after

                tx = LedgerTransaction(
                    tenant_id=wallet.tenant_id,
                    wallet_id=wallet_id,
                    transaction_type=TransactionType.CREDIT,
                    amount=amount,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    reference_id=ref_id,
                    reference_type=ref_type,
                    description=description or f"Payment settlement for intent {ref_id}",
                )
                session.add(tx)
                await session.flush()
            return tx
        except IntegrityError as exc:
            # Cleanly rollback wallet balance
            try:
                await session.refresh(wallet)
            except Exception:  # noqa: BLE001
                session.expire(wallet)

            stmt = select(LedgerTransaction).where(
                LedgerTransaction.wallet_id == wallet_id,
                LedgerTransaction.transaction_type == TransactionType.CREDIT,
                LedgerTransaction.reference_type == ref_type,
                LedgerTransaction.reference_id == ref_id,
            )
            existing_tx = (await session.execute(stmt)).scalars().first()
            if existing_tx is None:
                for _ in range(20):
                    await asyncio.sleep(0.05)
                    existing_tx = (await session.execute(stmt)).scalars().first()
                    if existing_tx is not None:
                        break

            if existing_tx is not None:
                if existing_tx.amount != amount:
                    raise LedgerIntegrityError(
                        f"Payment settlement amount mismatch for intent {ref_id}: "
                        f"existing={existing_tx.amount}, requested={amount}."
                    ) from exc
                logger.warning(
                    "Concurrent race resolved: settlement for intent %s caught by DB unique constraint.",
                    ref_id,
                )
                return existing_tx

            raise

    @classmethod
    async def reserve_topup_reversal(
        cls,
        session: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        reversal_id: uuid.UUID,
        description: str | None = None,
    ) -> LedgerTransaction:
        """Idempotently reserve wallet funds for an external top-up refund saga.

        The debit happens before the external refund is attempted so the customer cannot
        spend funds that are being returned upstream. A partial unique index guarantees
        that retries and concurrent workers can never reserve the same reversal twice.
        """
        if amount <= Decimal("0.00"):
            raise ValueError("Top-up reversal amount must be strictly positive.")

        wallet = await cls._lock_wallet(session, wallet)

        wallet_id = wallet.id
        reference_id = str(reversal_id)
        stmt = select(LedgerTransaction).where(
            LedgerTransaction.wallet_id == wallet_id,
            LedgerTransaction.transaction_type == TransactionType.DEBIT,
            LedgerTransaction.reference_type == CANONICAL_TOPUP_REVERSAL_TYPE,
            LedgerTransaction.reference_id == reference_id,
        )
        existing_tx = (await session.execute(stmt)).scalars().first()
        if existing_tx is not None:
            if existing_tx.amount != amount:
                raise LedgerIntegrityError(
                    f"Top-up reversal amount mismatch for reversal {reference_id}: "
                    f"existing={existing_tx.amount}, requested={amount}."
                )
            return existing_tx

        if wallet.balance < amount:
            raise InsufficientFundsError(
                f"Insufficient reversible balance: available {wallet.balance} {wallet.currency}, "
                f"required {amount} {wallet.currency}."
            )

        try:
            async with session.begin_nested():
                balance_before = wallet.balance
                wallet.balance = balance_before - amount
                tx = LedgerTransaction(
                    tenant_id=wallet.tenant_id,
                    wallet_id=wallet_id,
                    transaction_type=TransactionType.DEBIT,
                    amount=amount,
                    balance_before=balance_before,
                    balance_after=wallet.balance,
                    reference_id=reference_id,
                    reference_type=CANONICAL_TOPUP_REVERSAL_TYPE,
                    description=description or f"Wallet top-up reversal reservation {reference_id}",
                )
                session.add(tx)
                await session.flush()
            return tx
        except IntegrityError as exc:
            try:
                await session.refresh(wallet)
            except Exception:  # noqa: BLE001
                session.expire(wallet)
            existing_tx = (await session.execute(stmt)).scalars().first()
            if existing_tx is not None:
                if existing_tx.amount != amount:
                    raise LedgerIntegrityError(
                        f"Top-up reversal amount mismatch for reversal {reference_id}: "
                        f"existing={existing_tx.amount}, requested={amount}."
                    ) from exc
                return existing_tx
            raise

    @classmethod
    async def settle_flexible_deposit(
        cls,
        session: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        flexible_deposit_id: uuid.UUID,
        description: str | None = None,
    ) -> LedgerTransaction:
        """Exactly-once fiat-wallet credit for an open-amount deposit session."""
        if amount <= Decimal("0.00"):
            raise ValueError("Flexible deposit settlement amount must be positive.")
        wallet = await cls._lock_wallet(session, wallet)
        ref_id = str(flexible_deposit_id)
        existing = await session.scalar(
            select(LedgerTransaction).where(
                LedgerTransaction.wallet_id == wallet.id,
                LedgerTransaction.transaction_type == TransactionType.CREDIT,
                LedgerTransaction.reference_type == CANONICAL_FLEXIBLE_DEPOSIT_TYPE,
                LedgerTransaction.reference_id == ref_id,
            )
        )
        if existing is not None:
            if existing.amount != amount:
                raise LedgerIntegrityError(
                    f"Flexible deposit settlement amount mismatch for {ref_id}: existing={existing.amount}, requested={amount}."
                )
            return existing
        try:
            async with session.begin_nested():
                before = wallet.balance
                after = before + amount
                wallet.balance = after
                tx = LedgerTransaction(
                    tenant_id=wallet.tenant_id,
                    wallet_id=wallet.id,
                    transaction_type=TransactionType.CREDIT,
                    amount=amount,
                    balance_before=before,
                    balance_after=after,
                    reference_id=ref_id,
                    reference_type=CANONICAL_FLEXIBLE_DEPOSIT_TYPE,
                    description=description or "Flexible deposit settlement",
                )
                session.add(tx)
                await session.flush()
                return tx
        except IntegrityError:
            existing = await session.scalar(
                select(LedgerTransaction).where(
                    LedgerTransaction.wallet_id == wallet.id,
                    LedgerTransaction.transaction_type == TransactionType.CREDIT,
                    LedgerTransaction.reference_type == CANONICAL_FLEXIBLE_DEPOSIT_TYPE,
                    LedgerTransaction.reference_id == ref_id,
                )
            )
            if existing is None or existing.amount != amount:
                raise LedgerIntegrityError("Concurrent flexible-deposit settlement conflict.")
            await session.refresh(wallet)
            return existing

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

        wallet = await cls._lock_wallet(session, wallet)

        if not wallet.is_active:
            raise InsufficientFundsError(
                "Wallet is unavailable pending financial reconciliation."
            )

        # Phase 12 reservations reduce spendable balance without changing booked balance.
        # A hold capture marks the hold CAPTURED before it reaches this method, so that
        # specific hold is no longer counted and can be debited exactly once.
        from sqlalchemy import func

        from packages.payments.economics_models import WalletHold, WalletHoldStatus

        held_total = await session.scalar(
            select(func.coalesce(func.sum(WalletHold.amount), 0)).where(
                WalletHold.wallet_id == wallet.id,
                WalletHold.status == WalletHoldStatus.ACTIVE,
            )
        )
        available_balance = wallet.balance - Decimal(held_total or 0)
        if available_balance < amount:
            raise InsufficientFundsError(
                f"Insufficient funds: available {available_balance} {wallet.currency}, required {amount} {wallet.currency}."
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

        wallet = await cls._lock_wallet(session, wallet)

        wallet_id = wallet.id

        # 1. Fast-path application-level optimization lookup
        if reference_id and reference_type:
            stmt = select(LedgerTransaction).where(
                LedgerTransaction.wallet_id == wallet_id,
                LedgerTransaction.transaction_type == TransactionType.REFUND,
                LedgerTransaction.reference_id == reference_id,
                LedgerTransaction.reference_type == reference_type,
            )
            existing_tx = (await session.execute(stmt)).scalars().first()
            if existing_tx:
                if existing_tx.amount != amount:
                    raise LedgerIntegrityError(
                        f"Refund amount mismatch for ref_id={reference_id} ref_type={reference_type}: "
                        f"existing={existing_tx.amount}, requested={amount}."
                    )
                logger.warning(
                    "Idempotent refund hit: refund for ref_id=%s ref_type=%s already exists. Skipping duplicate credit.",
                    reference_id,
                    reference_type,
                )
                return existing_tx

        # 2. Database-enforced atomic refund execution via savepoint
        # The database partial unique index (uq_refund_idempotency) guarantees that only
        # ONE refund transaction may ever be committed for (wallet_id, reference_type, reference_id).
        try:
            async with session.begin_nested():
                balance_before = wallet.balance
                balance_after = balance_before + amount
                wallet.balance = balance_after

                tx = LedgerTransaction(
                    tenant_id=wallet.tenant_id,
                    wallet_id=wallet_id,
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

        except IntegrityError as exc:
            # Savepoint rollback cleanly reverts the pending INSERT and wallet balance modification.
            try:
                await session.refresh(wallet)
            except Exception:  # noqa: BLE001
                session.expire(wallet)

            # Check if this IntegrityError was specifically the refund uniqueness conflict
            if reference_id and reference_type:
                stmt = select(LedgerTransaction).where(
                    LedgerTransaction.wallet_id == wallet_id,
                    LedgerTransaction.transaction_type == TransactionType.REFUND,
                    LedgerTransaction.reference_id == reference_id,
                    LedgerTransaction.reference_type == reference_type,
                )
                existing_tx = (await session.execute(stmt)).scalars().first()
                if existing_tx is None:
                    # In high-concurrency races, wait briefly if winning transaction is in-flight
                    for _ in range(20):
                        await asyncio.sleep(0.05)
                        existing_tx = (await session.execute(stmt)).scalars().first()
                        if existing_tx is not None:
                            break

                if existing_tx:
                    if existing_tx.amount != amount:
                        raise LedgerIntegrityError(
                            f"Refund amount mismatch for ref_id={reference_id} ref_type={reference_type}: "
                            f"existing={existing_tx.amount}, requested={amount}."
                        ) from exc
                    logger.warning(
                        "Concurrent race resolved: refund for ref_id=%s ref_type=%s caught by DB unique constraint.",
                        reference_id,
                        reference_type,
                    )
                    return existing_tx

            # Not an idempotency conflict: do NOT swallow unrelated IntegrityError!
            raise

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

        wallet = await cls._lock_wallet(session, wallet)

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
