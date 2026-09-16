from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.exceptions import LedgerIntegrityError
from packages.payments.economics import (
    FlexibleDepositSettlementService,
    normalize_asset,
    normalize_currency,
    normalize_network,
)
from packages.payments.economics_models import (
    AssetWallet,
    AutoCreditTarget,
    FlexibleDepositSession,
    FlexibleDepositStatus,
)
from packages.payments.exceptions import PaymentError, PaymentIntegrityError, PaymentProviderError
from packages.payments.models import (
    FinancialResolutionCase,
    FinancialResolutionCaseStatus,
    PaymentMethodConfig,
    Wallet,
)
from packages.payments.payment_service import PaymentService
from packages.payments.providers.interface import (
    FlexibleDepositCreateRequest,
    FlexibleDepositProvider,
    FlexibleDepositResult,
)

logger = logging.getLogger("payments.flexible-deposits")


class FlexibleDepositService:
    """Open-amount deposit orchestration with provider evidence separated from ledger policy."""

    def __init__(self, payment_service: PaymentService | None = None) -> None:
        self.payment_service = payment_service or PaymentService()

    @staticmethod
    def _merchant_reference(tenant_id: uuid.UUID, user_id: uuid.UUID, idempotency_key: str) -> str:
        digest = hashlib.sha256(f"{tenant_id}:{user_id}:{idempotency_key}".encode()).hexdigest()[:28]
        return f"DEP-{digest.upper()}"

    @staticmethod
    def _allowed_values(settings: dict[str, Any], key: str, normalizer) -> set[str]:
        raw = settings.get(key)
        if raw is None:
            return set()
        if not isinstance(raw, list):
            raise PaymentIntegrityError(f"Flexible deposit setting {key} must be a list.")
        result: set[str] = set()
        for value in raw:
            if not isinstance(value, str) or not value.strip():
                raise PaymentIntegrityError(f"Flexible deposit setting {key} contains an invalid value.")
            result.add(normalizer(value))
        return result

    @staticmethod
    def _policy_snapshot(
        method: PaymentMethodConfig, *, allow_auto_credit: bool = True
    ) -> tuple[AutoCreditTarget, str, str | None, uuid.UUID | None, dict[str, Any]]:
        settings = dict(method.settings_json or {})
        if not bool(settings.get("flexible_deposits_enabled", False)):
            raise PaymentError("Flexible/open-amount deposits are not enabled for this payment method.")
        target_raw = str(method.auto_credit_target or "ASSET_WALLET").strip().upper()
        try:
            target = AutoCreditTarget(target_raw)
        except ValueError as exc:
            raise PaymentIntegrityError("Payment method has an invalid auto-credit target.") from exc
        basis = str(settings.get("auto_credit_basis") or "NET_AFTER_FEE").strip().upper()
        if basis not in {"NET_AFTER_FEE", "GROSS_RECEIVED"}:
            raise PaymentIntegrityError("auto_credit_basis must be NET_AFTER_FEE or GROSS_RECEIVED.")

        settlement_currency: str | None = None
        fx_policy_id: uuid.UUID | None = None
        effective_auto_credit = bool(method.auto_credit_enabled and allow_auto_credit)
        if effective_auto_credit and target == AutoCreditTarget.SETTLEMENT_WALLET:
            raw_currency = str(settings.get("auto_credit_currency") or "").strip()
            if not raw_currency:
                raise PaymentIntegrityError(
                    "Settlement-wallet auto-credit requires auto_credit_currency in payment method settings."
                )
            settlement_currency = normalize_currency(raw_currency)
            raw_policy = str(settings.get("fx_policy_id") or "").strip()
            if not raw_policy:
                raise PaymentIntegrityError(
                    "Settlement-wallet auto-credit requires an explicit fx_policy_id."
                )
            try:
                fx_policy_id = uuid.UUID(raw_policy)
            except ValueError as exc:
                raise PaymentIntegrityError("Payment method fx_policy_id is invalid.") from exc

        snapshot = {
            "auto_credit_enabled": effective_auto_credit,
            "auto_credit_target": target.value,
            "auto_credit_basis": basis,
            "settlement_currency": settlement_currency,
            "fx_policy_id": str(fx_policy_id) if fx_policy_id else None,
            "allowed_assets": sorted(
                FlexibleDepositService._allowed_values(settings, "allowed_assets", normalize_asset)
            ),
            "allowed_networks": sorted(
                FlexibleDepositService._allowed_values(settings, "allowed_networks", normalize_network)
            ),
        }
        return target, basis, settlement_currency, fx_policy_id, snapshot

    @staticmethod
    def _assert_provider_result_allowed(deposit: FlexibleDepositSession, result: FlexibleDepositResult) -> None:
        snapshot = deposit.policy_snapshot_json or {}
        allowed_assets = set(snapshot.get("allowed_assets") or [])
        allowed_networks = set(snapshot.get("allowed_networks") or [])
        asset = normalize_asset(result.asset) if result.asset else None
        network = normalize_network(result.network) if result.network else None
        if asset is not None and allowed_assets and asset not in allowed_assets:
            raise PaymentIntegrityError(f"Provider selected asset {asset}, which is outside this deposit policy.")
        if network is not None and allowed_networks and network not in allowed_networks:
            raise PaymentIntegrityError(f"Provider selected network {network}, which is outside this deposit policy.")
        if deposit.asset and asset and deposit.asset != asset:
            raise PaymentIntegrityError("Flexible deposit asset identity changed after it was first observed.")
        if deposit.network and network and deposit.network != network:
            raise PaymentIntegrityError("Flexible deposit network identity changed after it was first observed.")

    @staticmethod
    def _apply_provider_result(deposit: FlexibleDepositSession, result: FlexibleDepositResult) -> None:
        if result.provider_deposit_id != deposit.provider_deposit_id:
            raise PaymentIntegrityError("Flexible deposit provider identity changed during reconciliation.")
        FlexibleDepositService._assert_provider_result_allowed(deposit, result)
        if result.asset:
            deposit.asset = normalize_asset(result.asset)
        if result.network:
            deposit.network = normalize_network(result.network)
        if result.amount_received is not None:
            if result.amount_received < 0:
                raise PaymentIntegrityError("Flexible deposit provider returned a negative received amount.")
            # Amount may rise while a flexible invoice is receiving multiple transfers, but must never shrink.
            if deposit.amount_received is not None and result.amount_received < deposit.amount_received:
                raise PaymentIntegrityError("Flexible deposit provider amount_received decreased unexpectedly.")
            deposit.amount_received = result.amount_received
        if result.fee_amount is not None:
            if result.fee_amount < 0:
                raise PaymentIntegrityError("Flexible deposit provider returned a negative fee.")
            deposit.fee_amount = result.fee_amount
        if result.checkout_url:
            deposit.checkout_url = result.checkout_url
        deposit.provider_snapshot_json = dict(result.raw_data or {})
        deposit.last_reconciled_at = datetime.now(UTC)
        deposit.status = result.status
        deposit.last_error = None

    async def _provider(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, provider_name: str
    ) -> FlexibleDepositProvider:
        provider = await self.payment_service.registry.get_provider(
            session=session,
            tenant_id=tenant_id,
            provider_name=provider_name,
            secret_storage=self.payment_service.secret_storage,
        )
        if not isinstance(provider, FlexibleDepositProvider) or not getattr(
            provider, "supports_flexible_deposits", False
        ):
            raise PaymentError(f"Payment provider {provider_name} does not support flexible deposits.")
        return provider

    async def create(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        payment_method_id: uuid.UUID,
        idempotency_key: str,
        return_url: str | None = None,
        allow_auto_credit: bool = True,
    ) -> FlexibleDepositSession:
        key = idempotency_key.strip()
        if not 8 <= len(key) <= 100:
            raise PaymentError("Flexible deposit idempotency key must be 8-100 characters.")
        existing = await session.scalar(
            select(FlexibleDepositSession).where(
                FlexibleDepositSession.tenant_id == tenant_id,
                FlexibleDepositSession.idempotency_key == key,
            )
        )
        if existing is not None:
            if existing.user_id != user_id or existing.payment_method_id != payment_method_id:
                raise PaymentIntegrityError("Flexible deposit idempotency key was reused with different semantics.")
            return existing

        method = await session.scalar(
            select(PaymentMethodConfig).where(
                PaymentMethodConfig.id == payment_method_id,
                PaymentMethodConfig.tenant_id == tenant_id,
                PaymentMethodConfig.is_enabled.is_(True),
            )
        )
        if method is None:
            raise PaymentError("Payment method not found or unavailable.")
        provider_name = (method.provider_name or "").strip().lower()
        if not provider_name:
            raise PaymentError("Flexible deposits require a provider-backed payment method.")
        target, basis, currency, fx_policy_id, snapshot = self._policy_snapshot(
            method, allow_auto_credit=allow_auto_credit
        )
        provider = await self._provider(session, tenant_id=tenant_id, provider_name=provider_name)
        result = await provider.create_flexible_deposit(
            FlexibleDepositCreateRequest(
                idempotency_key=key,
                order_reference=self._merchant_reference(tenant_id, user_id, key),
                metadata={"product_name": method.display_name},
                return_url=return_url,
            )
        )
        if not result.provider_deposit_id:
            raise PaymentIntegrityError("Provider returned an empty flexible deposit identity.")
        deposit = FlexibleDepositSession(
            tenant_id=tenant_id,
            user_id=user_id,
            payment_method_id=method.id,
            provider=provider_name,
            provider_deposit_id=result.provider_deposit_id,
            idempotency_key=key,
            status=result.status,
            checkout_url=result.checkout_url,
            asset=normalize_asset(result.asset) if result.asset else None,
            network=normalize_network(result.network) if result.network else None,
            amount_received=result.amount_received,
            fee_amount=result.fee_amount,
            auto_credit_enabled=bool(snapshot["auto_credit_enabled"]),
            auto_credit_target=target,
            auto_credit_basis=basis,
            settlement_currency=currency,
            fx_policy_id=fx_policy_id,
            provider_snapshot_json=dict(result.raw_data or {}),
            policy_snapshot_json=snapshot,
            last_reconciled_at=datetime.now(UTC),
        )
        self._assert_provider_result_allowed(deposit, result)
        try:
            async with session.begin_nested():
                session.add(deposit)
                await session.flush()
        except IntegrityError:
            winner = await session.scalar(
                select(FlexibleDepositSession).where(
                    FlexibleDepositSession.tenant_id == tenant_id,
                    FlexibleDepositSession.idempotency_key == key,
                )
            )
            if winner is None:
                raise
            if winner.provider_deposit_id != result.provider_deposit_id:
                raise PaymentIntegrityError(
                    "Concurrent flexible deposit creation returned conflicting provider identities."
                )
            deposit = winner
        return deposit

    async def get(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, deposit_id: uuid.UUID
    ) -> FlexibleDepositSession:
        deposit = await session.scalar(
            select(FlexibleDepositSession).where(
                FlexibleDepositSession.id == deposit_id,
                FlexibleDepositSession.tenant_id == tenant_id,
            )
        )
        if deposit is None:
            raise PaymentError("Flexible deposit not found.")
        return deposit

    async def reconcile(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, deposit_id: uuid.UUID
    ) -> FlexibleDepositSession:
        deposit = await session.scalar(
            select(FlexibleDepositSession).where(
                FlexibleDepositSession.id == deposit_id,
                FlexibleDepositSession.tenant_id == tenant_id,
            ).with_for_update().execution_options(populate_existing=True)
        )
        if deposit is None:
            raise PaymentError("Flexible deposit not found.")
        if deposit.status in {
            FlexibleDepositStatus.EXPIRED, FlexibleDepositStatus.CANCELLED,
            FlexibleDepositStatus.REVERSED,
        }:
            return deposit
        provider = await self._provider(session, tenant_id=tenant_id, provider_name=deposit.provider)
        try:
            result = await provider.get_flexible_deposit(deposit.provider_deposit_id)
            if result.provider_deposit_id != deposit.provider_deposit_id:
                raise PaymentIntegrityError("Flexible deposit provider identity changed during reconciliation.")
            if deposit.credited_at is not None:
                # A post-credit observation must never rewrite credited financial evidence
                # or cause a second credit. Reversals freeze value for explicit resolution.
                deposit.last_reconciled_at = datetime.now(UTC)
                if result.status == FlexibleDepositStatus.REVERSED:
                    await self._record_credited_reversal(session, deposit)
                else:
                    self._assert_provider_result_allowed(deposit, result)
                    deposit.status = FlexibleDepositStatus.CREDITED
                    deposit.last_error = None
            else:
                self._apply_provider_result(deposit, result)
                if result.status == FlexibleDepositStatus.SETTLED_REVIEW:
                    await FlexibleDepositSettlementService.settle(session, deposit=deposit)
            await session.flush()
            return deposit
        except (PaymentProviderError, PaymentIntegrityError, LedgerIntegrityError) as exc:
            deposit.status = FlexibleDepositStatus.UNKNOWN
            deposit.last_error = str(exc)[:500]
            deposit.last_reconciled_at = datetime.now(UTC)
            await session.flush()
            logger.warning("Flexible deposit %s reconciliation requires review: %s", deposit.id, exc)
            return deposit

    @staticmethod
    async def _record_credited_reversal(session: AsyncSession, deposit: FlexibleDepositSession) -> None:
        wallet = None
        asset_wallet = None
        if deposit.credited_currency:
            wallet = await session.scalar(select(Wallet).where(
                Wallet.tenant_id == deposit.tenant_id, Wallet.user_id == deposit.user_id,
                Wallet.currency == deposit.credited_currency,
            ).with_for_update().execution_options(populate_existing=True))
        else:
            asset_wallet = await session.scalar(select(AssetWallet).where(
                AssetWallet.tenant_id == deposit.tenant_id, AssetWallet.user_id == deposit.user_id,
                AssetWallet.asset == deposit.credited_asset, AssetWallet.network == deposit.network,
            ).with_for_update().execution_options(populate_existing=True))
        target_wallet = wallet if wallet is not None else asset_wallet
        if target_wallet is None:
            raise LedgerIntegrityError("Credited deposit references a missing wallet.")
        target_wallet.is_active = False
        source_key = f"flexible-reversal:{deposit.id}"
        existing = await session.scalar(select(FinancialResolutionCase).where(
            FinancialResolutionCase.tenant_id == deposit.tenant_id,
            FinancialResolutionCase.source_key == source_key,
        ))
        if existing is None:
            session.add(FinancialResolutionCase(
                tenant_id=deposit.tenant_id, source_key=source_key,
                case_type="FLEXIBLE_DEPOSIT_REVERSED_AFTER_CREDIT", severity="CRITICAL",
                status=FinancialResolutionCaseStatus.OPEN,
                wallet_id=wallet.id if wallet is not None else None,
                metadata_json={
                    "deposit_id": str(deposit.id), "provider": deposit.provider,
                    "provider_deposit_id": deposit.provider_deposit_id,
                    "credited_amount": str(deposit.credited_amount),
                    "asset": deposit.credited_asset, "network": deposit.network,
                    "currency": deposit.credited_currency,
                    "asset_wallet_id": str(asset_wallet.id) if asset_wallet is not None else None,
                },
            ))
        deposit.status = FlexibleDepositStatus.REVERSED
        deposit.last_error = "PROVIDER_REVERSED_AFTER_CREDIT_REQUIRES_FINANCIAL_RESOLUTION"

    async def reconcile_open(
        self, session: AsyncSession, *, limit: int = 50
    ) -> list[FlexibleDepositSession]:
        open_statuses = [
            FlexibleDepositStatus.CREDITED,
            FlexibleDepositStatus.CREATED,
            FlexibleDepositStatus.PENDING,
            FlexibleDepositStatus.PROCESSING,
            FlexibleDepositStatus.SETTLED_REVIEW,
            FlexibleDepositStatus.UNKNOWN,
        ]
        stmt = (
            select(FlexibleDepositSession)
            .where(FlexibleDepositSession.status.in_(open_statuses))
            .order_by(FlexibleDepositSession.last_reconciled_at.asc().nullsfirst(), FlexibleDepositSession.id.asc())
            .limit(max(1, min(int(limit), 200)))
        )
        deposits = list((await session.execute(stmt)).scalars().all())
        result: list[FlexibleDepositSession] = []
        for deposit in deposits:
            result.append(await self.reconcile(session, tenant_id=deposit.tenant_id, deposit_id=deposit.id))
        return result
