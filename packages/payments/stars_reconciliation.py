import asyncio
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.database import async_session_factory
from packages.payments.exceptions import PaymentError, PaymentIntegrityError, PaymentProviderError
from packages.payments.models import (
    PaymentIntent,
    PaymentIntentPurpose,
    PaymentProviderConfig,
    PaymentReconciliationEvent,
    PaymentReconciliationEventStatus,
    Wallet,
    WalletTopUpReversal,
    WalletTopUpReversalStatus,
)
from packages.payments.payment_service import PaymentService
from packages.payments.providers.telegram_stars import TelegramStarsProvider
from packages.payments.resolution import FinancialResolutionService
from packages.payments.state_machine import PaymentIntentStatus
from packages.tenants.models import User

logger = logging.getLogger("payments.stars_reconciliation")


class TelegramStarsReconciliationService:
    """Reconcile outbound Telegram Stars invoice transactions with local wallet state.

    Intentional merchant refunds and external chargebacks/refunds have the same transaction
    identifier as the original incoming payment. The local reversal saga is therefore the
    discriminator: an outbound invoice transaction with no local reversal is treated as an
    external reversal and mirrored into the wallet ledger.
    """

    EVENT_TYPE = "TELEGRAM_STARS_OUTBOUND_INVOICE"

    def __init__(
        self,
        payment_service: PaymentService | None = None,
        resolution_service: FinancialResolutionService | None = None,
    ) -> None:
        self.payment_service = payment_service or PaymentService()
        self.resolution_service = resolution_service or FinancialResolutionService()

    @staticmethod
    def _is_relevant_outbound(transaction: dict[str, Any]) -> bool:
        receiver = transaction.get("receiver")
        if not isinstance(receiver, dict):
            return False
        return (
            receiver.get("type") == "user"
            and receiver.get("transaction_type") == "invoice_payment"
        )

    @staticmethod
    def _transaction_amount(transaction: dict[str, Any]) -> Decimal:
        try:
            return Decimal(str(abs(int(transaction.get("amount", 0)))))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise PaymentIntegrityError("Telegram Stars transaction contains an invalid amount.") from exc

    @staticmethod
    def _occurred_at(transaction: dict[str, Any]) -> datetime | None:
        try:
            raw = int(transaction.get("date"))
        except (TypeError, ValueError):
            return None
        try:
            return datetime.fromtimestamp(raw, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None

    async def _existing_event(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        transaction_id: str,
    ) -> PaymentReconciliationEvent | None:
        stmt = select(PaymentReconciliationEvent).where(
            PaymentReconciliationEvent.tenant_id == tenant_id,
            PaymentReconciliationEvent.provider == "telegram_stars",
            PaymentReconciliationEvent.provider_event_id == transaction_id,
            PaymentReconciliationEvent.event_type == self.EVENT_TYPE,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _find_intent(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        transaction_id: str,
        invoice_payload: str | None,
    ) -> PaymentIntent | None:
        stmt = select(PaymentIntent).where(
            PaymentIntent.tenant_id == tenant_id,
            PaymentIntent.provider == "telegram_stars",
            PaymentIntent.provider_payment_id == transaction_id,
            PaymentIntent.purpose == PaymentIntentPurpose.WALLET_TOPUP,
        )
        intent = (await session.execute(stmt)).scalar_one_or_none()
        if intent is not None:
            return intent

        prefix = "ghbf:wallet-topup:"
        if not invoice_payload or not invoice_payload.startswith(prefix):
            return None
        try:
            intent_id = uuid.UUID(invoice_payload.removeprefix(prefix))
        except ValueError:
            return None
        candidate = await session.get(PaymentIntent, intent_id)
        if candidate is None or candidate.tenant_id != tenant_id:
            return None
        if candidate.provider != "telegram_stars" or candidate.purpose != PaymentIntentPurpose.WALLET_TOPUP:
            return None
        return candidate

    @staticmethod
    async def _freeze_intent_wallet(session: AsyncSession, intent: PaymentIntent) -> None:
        stmt = select(Wallet).where(
            Wallet.tenant_id == intent.tenant_id,
            Wallet.user_id == intent.user_id,
            Wallet.currency == intent.currency,
        )
        wallet = (await session.execute(stmt)).scalar_one_or_none()
        if wallet is not None:
            wallet.is_active = False

    async def process_outbound_transaction(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        transaction: dict[str, Any],
    ) -> PaymentReconciliationEvent | None:
        if not self._is_relevant_outbound(transaction):
            return None
        transaction_id = str(transaction.get("id") or "").strip()
        if not transaction_id:
            return None

        existing_event = await self._existing_event(session, tenant_id, transaction_id)
        if existing_event is not None:
            if existing_event.requires_review:
                await self.resolution_service.ensure_case_for_reconciliation_event(
                    session, existing_event
                )
            return existing_event

        receiver = transaction.get("receiver") or {}
        invoice_payload = str(receiver.get("invoice_payload") or "").strip() or None
        # Reconcile only invoice transactions that can plausibly belong to this application.
        if invoice_payload is not None and not invoice_payload.startswith("ghbf:wallet-topup:"):
            return None

        amount = self._transaction_amount(transaction)
        intent = await self._find_intent(
            session,
            tenant_id,
            transaction_id,
            invoice_payload,
        )

        status = PaymentReconciliationEventStatus.PROCESSED
        classification = "EXPECTED_MERCHANT_REFUND"
        requires_review = False
        detail: str | None = None
        reversal_for_case: WalletTopUpReversal | None = None

        if intent is None:
            # If it has our payload namespace but no matching local intent, preserve the
            # provider observation for operator review instead of silently dropping it.
            if invoice_payload is None:
                return None
            status = PaymentReconciliationEventStatus.MANUAL_REVIEW
            classification = "UNMATCHED_GHBF_OUTBOUND"
            requires_review = True
            detail = "Telegram reported an outbound GH-Bot-Factory invoice transaction with no local intent."
        else:
            user = await session.get(User, intent.user_id)
            receiver_user = receiver.get("user") if isinstance(receiver, dict) else None
            receiver_user_id = receiver_user.get("id") if isinstance(receiver_user, dict) else None
            integrity_error: str | None = None
            if intent.status != PaymentIntentStatus.SUCCEEDED:
                integrity_error = f"Intent is not settled: {intent.status.value}."
            elif intent.currency != "XTR" or intent.amount != amount:
                integrity_error = (
                    f"Provider reversal amount/currency mismatch: intent={intent.amount} {intent.currency}, "
                    f"provider={amount} XTR."
                )
            elif intent.provider_payment_id != transaction_id:
                integrity_error = "Provider transaction identifier does not match the settled intent."
            elif user is None or user.telegram_id is None:
                integrity_error = "Settled intent user has no Telegram identity."
            elif receiver_user_id is not None:
                try:
                    receiver_user_id_int = int(receiver_user_id)
                except (TypeError, ValueError):
                    integrity_error = "Provider reversal receiver contains an invalid Telegram user identifier."
                else:
                    if receiver_user_id_int != int(user.telegram_id):
                        integrity_error = "Provider reversal receiver does not match the settled intent user."

            if integrity_error is not None:
                await self._freeze_intent_wallet(session, intent)
                status = PaymentReconciliationEventStatus.MANUAL_REVIEW
                classification = "OUTBOUND_INTEGRITY_MISMATCH"
                requires_review = True
                detail = integrity_error
            else:
                reversal_stmt = select(WalletTopUpReversal).where(
                    WalletTopUpReversal.tenant_id == tenant_id,
                    WalletTopUpReversal.payment_intent_id == intent.id,
                )
                reversal = (await session.execute(reversal_stmt)).scalar_one_or_none()
                if reversal is not None:
                    reversal_for_case = reversal
                    reversal = await self.payment_service.complete_wallet_topup_reversal_from_provider_event(
                        session=session,
                        tenant_id=tenant_id,
                        reversal=reversal,
                        provider_tx_id=transaction_id,
                        provider_metadata=transaction,
                    )
                    if reversal.status == WalletTopUpReversalStatus.MANUAL_REVIEW:
                        status = PaymentReconciliationEventStatus.MANUAL_REVIEW
                        classification = "LOCAL_REVERSAL_REQUIRES_REVIEW"
                        requires_review = True
                        detail = reversal.last_error_detail
                    else:
                        classification = "EXPECTED_MERCHANT_REFUND"
                else:
                    reversal = await self.payment_service.record_external_wallet_topup_reversal(
                        session=session,
                        tenant_id=tenant_id,
                        intent=intent,
                        provider_metadata=transaction,
                    )
                    reversal_for_case = reversal
                    if reversal.status == WalletTopUpReversalStatus.COMPLETED:
                        classification = "EXTERNAL_REVERSAL_APPLIED"
                    else:
                        status = PaymentReconciliationEventStatus.MANUAL_REVIEW
                        classification = "EXTERNAL_REVERSAL_INSUFFICIENT_FUNDS"
                        requires_review = True
                        detail = reversal.last_error_detail

        event = PaymentReconciliationEvent(
            tenant_id=tenant_id,
            provider="telegram_stars",
            provider_event_id=transaction_id,
            event_type=self.EVENT_TYPE,
            payment_intent_id=intent.id if intent is not None else None,
            status=status,
            amount=amount,
            currency="XTR",
            occurred_at=self._occurred_at(transaction),
            classification=classification,
            requires_review=requires_review,
            metadata_json={
                "detail": detail,
                "transaction": transaction,
            },
        )
        try:
            async with session.begin_nested():
                session.add(event)
                await session.flush()
        except IntegrityError:
            concurrent = await self._existing_event(session, tenant_id, transaction_id)
            if concurrent is not None:
                if concurrent.requires_review:
                    await self.resolution_service.ensure_case_for_reconciliation_event(
                        session, concurrent, reversal_for_case
                    )
                return concurrent
            raise
        if event.requires_review:
            await self.resolution_service.ensure_case_for_reconciliation_event(
                session, event, reversal_for_case
            )
        return event

    async def scan_tenant(self, session: AsyncSession, tenant_id: uuid.UUID) -> int:
        provider = await self.payment_service.registry.get_provider(
            session=session,
            tenant_id=tenant_id,
            provider_name="telegram_stars",
            secret_storage=self.payment_service.secret_storage,
        )
        if not isinstance(provider, TelegramStarsProvider) and not hasattr(provider, "list_recent_star_transactions"):
            raise PaymentProviderError("Configured telegram_stars provider cannot list transactions.")
        transactions = await provider.list_recent_star_transactions()
        processed = 0
        for transaction in transactions:
            event = await self.process_outbound_transaction(session, tenant_id, transaction)
            if event is not None:
                processed += 1
        return processed


class TelegramStarsReconciliationWorker:
    """Periodic durable scanner for external Telegram Stars invoice reversals."""

    def __init__(
        self,
        reconciliation_service: TelegramStarsReconciliationService | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        poll_interval_seconds: float = 60.0,
    ) -> None:
        self.reconciliation_service = reconciliation_service or TelegramStarsReconciliationService()
        self.session_factory = session_factory or async_session_factory
        self.poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task | None = None
        self._is_running = False

    async def poll_once(self) -> int:
        async with self.session_factory() as session:
            stmt = select(PaymentProviderConfig.tenant_id).where(
                PaymentProviderConfig.provider_name == "telegram_stars",
                PaymentProviderConfig.is_enabled.is_(True),
            )
            tenant_ids = list((await session.execute(stmt)).scalars().all())

        total = 0
        for tenant_id in tenant_ids:
            async with self.session_factory() as session:
                config_stmt = select(PaymentProviderConfig).where(
                    PaymentProviderConfig.tenant_id == tenant_id,
                    PaymentProviderConfig.provider_name == "telegram_stars",
                    PaymentProviderConfig.is_enabled.is_(True),
                )
                config = (await session.execute(config_stmt)).scalar_one_or_none()
                if config is None:
                    continue
                if not bool((config.settings_json or {}).get("chargeback_reconciliation_enabled", True)):
                    continue
                try:
                    total += await self.reconciliation_service.scan_tenant(session, tenant_id)
                    await session.commit()
                except PaymentError:
                    await session.rollback()
                    logger.exception("Telegram Stars reconciliation failed for tenant=%s", tenant_id)
                except Exception:
                    await session.rollback()
                    logger.exception("Unexpected Stars reconciliation failure for tenant=%s", tenant_id)
        return total

    async def start(self) -> None:
        self._is_running = True
        self._task = asyncio.create_task(self._run_loop(), name="telegram-stars-reconciliation-worker")

    async def stop(self) -> None:
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        while self._is_running:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Telegram Stars reconciliation worker poll failed")
            await asyncio.sleep(self.poll_interval_seconds)
