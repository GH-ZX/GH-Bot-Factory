from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.exceptions import InsufficientFundsError, LedgerIntegrityError
from packages.payments.economics_models import (
    AssetLedgerTransaction,
    AssetLedgerTransactionType,
    AssetWallet,
    AutoCreditTarget,
    FlexibleDepositSession,
    FlexibleDepositStatus,
    FxPolicy,
    FxPolicyMode,
    WalletHold,
    WalletHoldStatus,
)
from packages.payments.models import LedgerTransaction, TransactionType, Wallet
from packages.payments.service import LedgerService

FLEXIBLE_DEPOSIT_SETTLEMENT_REF = "FLEXIBLE_DEPOSIT_SETTLEMENT"
WALLET_HOLD_CAPTURE_REF = "WALLET_HOLD_CAPTURE"


def normalize_asset(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized or len(normalized) > 24:
        raise ValueError("Asset identifier must be 1-24 characters.")
    return normalized


def normalize_network(value: str | None) -> str:
    normalized = (value or "OFFCHAIN").strip().upper()
    if not normalized or len(normalized) > 64:
        raise ValueError("Network identifier must be 1-64 characters.")
    return normalized


def normalize_currency(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) not in {3, 4, 5, 6, 7, 8, 9, 10, 11, 12} or not normalized.replace("_", "").isalnum():
        raise ValueError("Settlement currency identifier is invalid.")
    return normalized


class AssetLedgerService:
    """High-precision asset ledger separate from the fiat settlement wallet ledger."""

    @staticmethod
    async def get_or_create_wallet(
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        asset: str,
        network: str | None,
    ) -> AssetWallet:
        asset_code = normalize_asset(asset)
        network_code = normalize_network(network)
        stmt = select(AssetWallet).where(
            AssetWallet.tenant_id == tenant_id,
            AssetWallet.user_id == user_id,
            AssetWallet.asset == asset_code,
            AssetWallet.network == network_code,
        )
        wallet = (await session.execute(stmt)).scalar_one_or_none()
        if wallet is not None:
            return wallet
        wallet = AssetWallet(
            tenant_id=tenant_id,
            user_id=user_id,
            asset=asset_code,
            network=network_code,
            balance=Decimal(0),
            is_active=True,
        )
        try:
            async with session.begin_nested():
                session.add(wallet)
                await session.flush()
        except IntegrityError:
            wallet = (await session.execute(stmt)).scalar_one()
        return wallet

    @staticmethod
    async def _lock_wallet(session: AsyncSession, wallet: AssetWallet) -> AssetWallet:
        locked = (
            await session.execute(
                select(AssetWallet)
                .where(
                    AssetWallet.id == wallet.id,
                    AssetWallet.tenant_id == wallet.tenant_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if locked is None:
            raise ValueError("Asset wallet not found in tenant scope.")
        return locked

    @classmethod
    async def credit(
        cls,
        session: AsyncSession,
        *,
        wallet: AssetWallet,
        amount: Decimal,
        idempotency_key: str,
        reference_type: str | None = None,
        reference_id: str | None = None,
        description: str | None = None,
    ) -> AssetLedgerTransaction:
        if amount <= 0:
            raise ValueError("Asset credit amount must be positive.")
        key = idempotency_key.strip()
        if not key or len(key) > 180:
            raise ValueError("Asset ledger idempotency key is invalid.")
        wallet = await cls._lock_wallet(session, wallet)
        existing = await session.scalar(
            select(AssetLedgerTransaction).where(
                AssetLedgerTransaction.tenant_id == wallet.tenant_id,
                AssetLedgerTransaction.idempotency_key == key,
            )
        )
        if existing is not None:
            if existing.wallet_id != wallet.id or existing.amount != amount or existing.transaction_type != AssetLedgerTransactionType.CREDIT:
                raise LedgerIntegrityError("Asset ledger idempotency key was reused with different financial semantics.")
            return existing
        try:
            async with session.begin_nested():
                before = wallet.balance
                after = before + amount
                wallet.balance = after
                tx = AssetLedgerTransaction(
                    tenant_id=wallet.tenant_id,
                    wallet_id=wallet.id,
                    transaction_type=AssetLedgerTransactionType.CREDIT,
                    amount=amount,
                    balance_before=before,
                    balance_after=after,
                    idempotency_key=key,
                    reference_type=reference_type,
                    reference_id=reference_id,
                    description=description,
                )
                session.add(tx)
                await session.flush()
                return tx
        except IntegrityError:
            existing = await session.scalar(
                select(AssetLedgerTransaction).where(
                    AssetLedgerTransaction.tenant_id == wallet.tenant_id,
                    AssetLedgerTransaction.idempotency_key == key,
                )
            )
            if existing is None or existing.wallet_id != wallet.id or existing.amount != amount:
                raise LedgerIntegrityError("Concurrent asset credit conflicted with different financial semantics.")
            await session.refresh(wallet)
            return existing


class WalletHoldService:
    """Reservation layer over booked wallet balances.

    Holds reduce available balance but do not mutate the booked balance until capture.
    """

    @staticmethod
    async def active_total(session: AsyncSession, *, wallet_id: uuid.UUID) -> Decimal:
        total = await session.scalar(
            select(func.coalesce(func.sum(WalletHold.amount), 0)).where(
                WalletHold.wallet_id == wallet_id,
                WalletHold.status == WalletHoldStatus.ACTIVE,
            )
        )
        return Decimal(total or 0)

    @classmethod
    async def available_balance(cls, session: AsyncSession, *, wallet: Wallet) -> Decimal:
        return wallet.balance - await cls.active_total(session, wallet_id=wallet.id)

    @classmethod
    async def reserve(
        cls,
        session: AsyncSession,
        *,
        wallet: Wallet,
        amount: Decimal,
        idempotency_key: str,
        reference_type: str,
        reference_id: str,
        expires_at: datetime | None = None,
    ) -> WalletHold:
        if amount <= Decimal(0):
            raise ValueError("Hold amount must be positive.")
        locked_wallet = await LedgerService._lock_wallet(session, wallet)
        existing = await session.scalar(
            select(WalletHold).where(
                WalletHold.tenant_id == locked_wallet.tenant_id,
                WalletHold.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.wallet_id != locked_wallet.id or existing.amount != amount:
                raise LedgerIntegrityError("Wallet hold idempotency key was reused with different semantics.")
            return existing
        available = locked_wallet.balance - await cls.active_total(session, wallet_id=locked_wallet.id)
        if available < amount:
            raise InsufficientFundsError(
                f"Insufficient available funds after holds: available {available} {locked_wallet.currency}, required {amount} {locked_wallet.currency}."
            )
        hold = WalletHold(
            tenant_id=locked_wallet.tenant_id,
            wallet_id=locked_wallet.id,
            amount=amount,
            status=WalletHoldStatus.ACTIVE,
            idempotency_key=idempotency_key,
            reference_type=reference_type,
            reference_id=reference_id,
            expires_at=expires_at,
        )
        session.add(hold)
        await session.flush()
        return hold

    @classmethod
    async def capture(cls, session: AsyncSession, *, hold: WalletHold, description: str | None = None) -> LedgerTransaction:
        locked = (
            await session.execute(
                select(WalletHold)
                .where(WalletHold.id == hold.id, WalletHold.tenant_id == hold.tenant_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if locked.status == WalletHoldStatus.CAPTURED:
            existing = await session.scalar(
                select(LedgerTransaction).where(
                    LedgerTransaction.wallet_id == locked.wallet_id,
                    LedgerTransaction.transaction_type == TransactionType.DEBIT,
                    LedgerTransaction.reference_type == WALLET_HOLD_CAPTURE_REF,
                    LedgerTransaction.reference_id == str(locked.id),
                )
            )
            if existing is None:
                raise LedgerIntegrityError("Captured hold is missing its ledger debit.")
            return existing
        if locked.status != WalletHoldStatus.ACTIVE:
            raise LedgerIntegrityError(f"Wallet hold cannot be captured from {locked.status.value}.")
        wallet = await session.get(Wallet, locked.wallet_id)
        if wallet is None:
            raise LedgerIntegrityError("Wallet hold references a missing wallet.")
        locked.status = WalletHoldStatus.CAPTURED
        locked.captured_at = datetime.now(UTC)
        await session.flush()
        return await LedgerService.debit(
            session,
            wallet,
            locked.amount,
            reference_id=str(locked.id),
            reference_type=WALLET_HOLD_CAPTURE_REF,
            description=description or f"Captured hold {locked.reference_type}:{locked.reference_id}",
        )

    @staticmethod
    async def release(session: AsyncSession, *, hold: WalletHold) -> WalletHold:
        locked = (
            await session.execute(
                select(WalletHold)
                .where(WalletHold.id == hold.id, WalletHold.tenant_id == hold.tenant_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if locked.status == WalletHoldStatus.RELEASED:
            return locked
        if locked.status != WalletHoldStatus.ACTIVE:
            raise LedgerIntegrityError(f"Wallet hold cannot be released from {locked.status.value}.")
        locked.status = WalletHoldStatus.RELEASED
        locked.released_at = datetime.now(UTC)
        await session.flush()
        return locked


class FxPolicyService:
    @staticmethod
    def normalize_rate(value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("FX policy rate must be positive and finite.")
        return value.quantize(Decimal("0.000000000000000001"))

    @classmethod
    async def create_policy(
        cls,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        from_asset: str,
        from_network: str | None,
        to_currency: str,
        mode: FxPolicyMode,
        rate: Decimal,
        max_auto_credit_amount: Decimal | None,
        metadata: dict | None = None,
    ) -> FxPolicy:
        rate = cls.normalize_rate(rate)
        if mode == FxPolicyMode.PARITY and rate != Decimal("1.000000000000000000"):
            raise ValueError("PARITY policy rate must be exactly 1.")
        if max_auto_credit_amount is not None and max_auto_credit_amount <= 0:
            raise ValueError("FX max_auto_credit_amount must be positive when configured.")
        policy = FxPolicy(
            tenant_id=tenant_id,
            from_asset=normalize_asset(from_asset),
            from_network=normalize_network(from_network) if from_network else "ANY",
            to_currency=normalize_currency(to_currency),
            mode=mode,
            rate=rate,
            max_auto_credit_amount=max_auto_credit_amount,
            is_enabled=True,
            acknowledged_at=datetime.now(UTC),
            metadata_json=dict(metadata or {}),
        )
        try:
            async with session.begin_nested():
                session.add(policy)
                await session.flush()
        except IntegrityError as exc:
            raise LedgerIntegrityError("An FX policy already exists for this asset/network/currency scope.") from exc
        return policy

    @staticmethod
    async def resolve(
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        asset: str,
        network: str,
        currency: str,
        policy_id: uuid.UUID | None = None,
    ) -> FxPolicy:
        stmt = select(FxPolicy).where(FxPolicy.tenant_id == tenant_id, FxPolicy.is_enabled.is_(True))
        if policy_id is not None:
            stmt = stmt.where(FxPolicy.id == policy_id)
        else:
            stmt = stmt.where(
                FxPolicy.from_asset == normalize_asset(asset),
                FxPolicy.from_network.in_([normalize_network(network), "ANY"]),
                FxPolicy.to_currency == normalize_currency(currency),
            ).order_by((FxPolicy.from_network == normalize_network(network)).desc())
        policy = (await session.execute(stmt)).scalars().first()
        if policy is None:
            raise LedgerIntegrityError("No enabled FX/parity policy matches the deposit asset and settlement currency.")
        if policy.from_asset != normalize_asset(asset):
            raise LedgerIntegrityError("FX policy asset identity mismatch.")
        if policy.from_network not in {"ANY", normalize_network(network)}:
            raise LedgerIntegrityError("FX policy network identity mismatch.")
        if policy.to_currency != normalize_currency(currency):
            raise LedgerIntegrityError("FX policy settlement currency mismatch.")
        return policy

    @staticmethod
    def convert(policy: FxPolicy, amount: Decimal) -> Decimal:
        if amount <= 0:
            raise ValueError("Conversion amount must be positive.")
        raw = amount * policy.rate
        return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class FlexibleDepositSettlementService:
    """Applies settled flexible deposits according to explicit tenant auto-credit policy."""

    @staticmethod
    def net_asset_amount(*, amount_received: Decimal, fee_amount: Decimal | None, basis: str) -> Decimal:
        normalized_basis = basis.strip().upper()
        if normalized_basis == "GROSS_RECEIVED":
            result = amount_received
        elif normalized_basis == "NET_AFTER_FEE":
            result = amount_received - (fee_amount or Decimal(0))
        else:
            raise LedgerIntegrityError("Flexible deposit auto_credit_basis must be GROSS_RECEIVED or NET_AFTER_FEE.")
        if result <= 0:
            raise LedgerIntegrityError("Flexible deposit net credit amount is not positive.")
        return result

    @classmethod
    async def settle(
        cls,
        session: AsyncSession,
        *,
        deposit: FlexibleDepositSession,
    ) -> FlexibleDepositSession:
        if deposit.status == FlexibleDepositStatus.CREDITED:
            return deposit
        if deposit.amount_received is None or deposit.amount_received <= 0 or not deposit.asset or not deposit.network:
            raise LedgerIntegrityError("Settled flexible deposit is missing authoritative asset evidence.")
        if not deposit.auto_credit_enabled:
            deposit.status = FlexibleDepositStatus.SETTLED_REVIEW
            await session.flush()
            return deposit

        basis = deposit.auto_credit_basis or "NET_AFTER_FEE"
        asset_amount = cls.net_asset_amount(
            amount_received=deposit.amount_received,
            fee_amount=deposit.fee_amount,
            basis=basis,
        )
        target = deposit.auto_credit_target
        if target == AutoCreditTarget.ASSET_WALLET:
            wallet = await AssetLedgerService.get_or_create_wallet(
                session,
                tenant_id=deposit.tenant_id,
                user_id=deposit.user_id,
                asset=deposit.asset,
                network=deposit.network,
            )
            await AssetLedgerService.credit(
                session,
                wallet=wallet,
                amount=asset_amount,
                idempotency_key=f"flexible-deposit:{deposit.id}",
                reference_type=FLEXIBLE_DEPOSIT_SETTLEMENT_REF,
                reference_id=str(deposit.id),
                description=f"Flexible deposit via {deposit.provider}",
            )
            deposit.credited_amount = asset_amount
            deposit.credited_asset = deposit.asset
        elif target == AutoCreditTarget.SETTLEMENT_WALLET:
            if not deposit.settlement_currency:
                raise LedgerIntegrityError("Settlement-wallet auto-credit requires an explicit settlement currency snapshot.")
            currency = normalize_currency(deposit.settlement_currency)
            policy = await FxPolicyService.resolve(
                session,
                tenant_id=deposit.tenant_id,
                asset=deposit.asset,
                network=deposit.network,
                currency=currency,
                policy_id=deposit.fx_policy_id,
            )
            settlement_amount = FxPolicyService.convert(policy, asset_amount)
            if policy.max_auto_credit_amount is not None and settlement_amount > policy.max_auto_credit_amount:
                deposit.status = FlexibleDepositStatus.SETTLED_REVIEW
                deposit.last_error = "AUTO_CREDIT_LIMIT_EXCEEDED"
                await session.flush()
                return deposit
            wallet = await LedgerService.get_or_create_wallet(
                session,
                tenant_id=deposit.tenant_id,
                user_id=deposit.user_id,
                currency=currency,
            )
            await LedgerService.settle_flexible_deposit(
                session,
                wallet=wallet,
                amount=settlement_amount,
                flexible_deposit_id=deposit.id,
                description=f"Flexible {deposit.asset} deposit via {deposit.provider}",
            )
            deposit.credited_amount = settlement_amount
            deposit.credited_currency = currency
            deposit.fx_policy_id = policy.id
        else:
            raise LedgerIntegrityError("Unsupported flexible deposit auto-credit target.")

        deposit.status = FlexibleDepositStatus.CREDITED
        deposit.credited_at = datetime.now(UTC)
        deposit.last_error = None
        await session.flush()
        return deposit
