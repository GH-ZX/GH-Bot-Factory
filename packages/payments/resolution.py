import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.exceptions import TenantAccessViolationError
from packages.payments.economics_models import AssetWallet
from packages.payments.exceptions import PaymentError, PaymentIntegrityError
from packages.payments.models import (
    FinancialResolutionCase,
    FinancialResolutionCaseStatus,
    PaymentIntent,
    PaymentReconciliationEvent,
    PaymentReconciliationEventStatus,
    Wallet,
    WalletTopUpReversal,
    WalletTopUpReversalStatus,
)
from packages.payments.payment_service import PaymentService


class FinancialResolutionAction(str, enum.Enum):
    RETRY_LOCAL_REVERSAL = "RETRY_LOCAL_REVERSAL"
    ACKNOWLEDGE_NO_WALLET_IMPACT = "ACKNOWLEDGE_NO_WALLET_IMPACT"
    CLOSE_KEEP_WALLET_FROZEN = "CLOSE_KEEP_WALLET_FROZEN"
    MARK_FALSE_POSITIVE_AND_UNFREEZE = "MARK_FALSE_POSITIVE_AND_UNFREEZE"


class FinancialResolutionService:
    """Human-resolution workflow layered on immutable payment evidence.

    Provider events, ledger entries, and reversal records remain the financial source of
    truth. Resolution cases coordinate operator assignment and audited decisions without
    mutating or deleting that evidence.
    """

    @staticmethod
    def _source_key_for_reversal(reversal_id: uuid.UUID) -> str:
        return f"reversal:{reversal_id}"

    @staticmethod
    def _source_key_for_event(event_id: uuid.UUID) -> str:
        return f"event:{event_id}"

    @staticmethod
    async def _wallet_for_intent(
        session: AsyncSession,
        intent: PaymentIntent | None,
    ) -> Wallet | None:
        if intent is None:
            return None
        stmt = select(Wallet).where(
            Wallet.tenant_id == intent.tenant_id,
            Wallet.user_id == intent.user_id,
            Wallet.currency == intent.currency,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def ensure_case_for_reversal(
        self,
        session: AsyncSession,
        reversal: WalletTopUpReversal,
    ) -> FinancialResolutionCase | None:
        if reversal.status != WalletTopUpReversalStatus.MANUAL_REVIEW:
            return None
        source_key = self._source_key_for_reversal(reversal.id)
        existing_stmt = select(FinancialResolutionCase).where(
            FinancialResolutionCase.tenant_id == reversal.tenant_id,
            FinancialResolutionCase.source_key == source_key,
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        wallet = await session.get(Wallet, reversal.wallet_id)
        severity = "CRITICAL" if wallet is not None and not wallet.is_active else "HIGH"
        metadata = {
            "origin": (reversal.metadata_json or {}).get("origin", "MERCHANT_REQUEST"),
            "last_error_code": reversal.last_error_code,
            "last_error_detail": reversal.last_error_detail,
        }
        if existing is not None:
            changed = False
            if existing.case_type != (reversal.last_error_code or "TOPUP_REVERSAL_MANUAL_REVIEW"):
                existing.case_type = reversal.last_error_code or "TOPUP_REVERSAL_MANUAL_REVIEW"
                changed = True
            if existing.severity != severity:
                existing.severity = severity
                changed = True
            if existing.wallet_id != reversal.wallet_id:
                existing.wallet_id = reversal.wallet_id
                changed = True
            if existing.metadata_json != metadata:
                existing.metadata_json = metadata
                changed = True
            if changed and existing.status != FinancialResolutionCaseStatus.RESOLVED:
                existing.version += 1
            return existing

        case = FinancialResolutionCase(
            tenant_id=reversal.tenant_id,
            source_key=source_key,
            case_type=reversal.last_error_code or "TOPUP_REVERSAL_MANUAL_REVIEW",
            severity=severity,
            status=FinancialResolutionCaseStatus.OPEN,
            payment_intent_id=reversal.payment_intent_id,
            wallet_id=reversal.wallet_id,
            reversal_id=reversal.id,
            version=1,
            metadata_json=metadata,
        )
        try:
            async with session.begin_nested():
                session.add(case)
                await session.flush()
        except IntegrityError:
            concurrent = (await session.execute(existing_stmt)).scalar_one_or_none()
            if concurrent is not None:
                return concurrent
            raise
        return case

    async def ensure_case_for_reconciliation_event(
        self,
        session: AsyncSession,
        event: PaymentReconciliationEvent,
        reversal: WalletTopUpReversal | None = None,
    ) -> FinancialResolutionCase | None:
        if not event.requires_review:
            return None

        if reversal is None and event.payment_intent_id is not None:
            reversal_stmt = select(WalletTopUpReversal).where(
                WalletTopUpReversal.tenant_id == event.tenant_id,
                WalletTopUpReversal.payment_intent_id == event.payment_intent_id,
                WalletTopUpReversal.status == WalletTopUpReversalStatus.MANUAL_REVIEW,
            )
            reversal = (await session.execute(reversal_stmt)).scalars().first()

        source_key = (
            self._source_key_for_reversal(reversal.id)
            if reversal is not None
            else self._source_key_for_event(event.id)
        )
        existing_stmt = select(FinancialResolutionCase).where(
            FinancialResolutionCase.tenant_id == event.tenant_id,
            FinancialResolutionCase.source_key == source_key,
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()

        intent = await session.get(PaymentIntent, event.payment_intent_id) if event.payment_intent_id else None
        wallet = await session.get(Wallet, reversal.wallet_id) if reversal is not None else await self._wallet_for_intent(session, intent)
        severity = "CRITICAL" if wallet is not None and not wallet.is_active else "HIGH"
        metadata = {
            "provider": event.provider,
            "provider_event_id": event.provider_event_id,
            "event_type": event.event_type,
            "detail": (event.metadata_json or {}).get("detail"),
        }
        if existing is not None:
            changed = False
            for attr, value in (
                ("case_type", event.classification),
                ("severity", severity),
                ("reconciliation_event_id", event.id),
                ("payment_intent_id", event.payment_intent_id),
                ("wallet_id", wallet.id if wallet is not None else None),
            ):
                if getattr(existing, attr) != value and value is not None:
                    setattr(existing, attr, value)
                    changed = True
            merged = {**(existing.metadata_json or {}), **metadata}
            if merged != (existing.metadata_json or {}):
                existing.metadata_json = merged
                changed = True
            if changed and existing.status != FinancialResolutionCaseStatus.RESOLVED:
                existing.version += 1
            return existing

        case = FinancialResolutionCase(
            tenant_id=event.tenant_id,
            source_key=source_key,
            case_type=event.classification,
            severity=severity,
            status=FinancialResolutionCaseStatus.OPEN,
            payment_intent_id=event.payment_intent_id,
            wallet_id=wallet.id if wallet is not None else None,
            reversal_id=reversal.id if reversal is not None else None,
            reconciliation_event_id=event.id,
            version=1,
            metadata_json=metadata,
        )
        try:
            async with session.begin_nested():
                session.add(case)
                await session.flush()
        except IntegrityError:
            concurrent = (await session.execute(existing_stmt)).scalar_one_or_none()
            if concurrent is not None:
                return concurrent
            raise
        return case

    @staticmethod
    async def _locked_case(
        session: AsyncSession,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
    ) -> FinancialResolutionCase:
        stmt = (
            select(FinancialResolutionCase)
            .where(
                FinancialResolutionCase.id == case_id,
                FinancialResolutionCase.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        case = (await session.execute(stmt)).scalar_one_or_none()
        if case is None:
            raise PaymentError("Financial resolution case not found.")
        return case

    @staticmethod
    def _verify_version(case: FinancialResolutionCase, expected_version: int) -> None:
        if case.version != expected_version:
            raise PaymentIntegrityError(
                f"Financial resolution case changed concurrently; expected version {expected_version}, current version {case.version}."
            )

    async def claim_case(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        expected_version: int,
    ) -> FinancialResolutionCase:
        case = await self._locked_case(session, tenant_id, case_id)
        self._verify_version(case, expected_version)
        if case.status == FinancialResolutionCaseStatus.RESOLVED:
            raise PaymentError("Resolved financial cases cannot be claimed.")
        if case.assigned_to_user_id not in (None, actor_user_id):
            raise PaymentIntegrityError("Financial case is already assigned to another operator.")
        case.assigned_to_user_id = actor_user_id
        case.status = FinancialResolutionCaseStatus.IN_PROGRESS
        case.version += 1
        await session.flush()
        return case

    async def release_case(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        expected_version: int,
        allow_override: bool = False,
    ) -> FinancialResolutionCase:
        case = await self._locked_case(session, tenant_id, case_id)
        self._verify_version(case, expected_version)
        if case.status == FinancialResolutionCaseStatus.RESOLVED:
            raise PaymentError("Resolved financial cases cannot be released.")
        if case.assigned_to_user_id not in (None, actor_user_id) and not allow_override:
            raise PaymentIntegrityError("Only the assigned operator can release this financial case.")
        case.assigned_to_user_id = None
        case.status = FinancialResolutionCaseStatus.OPEN
        case.version += 1
        await session.flush()
        return case

    async def _mark_event_resolved(
        self,
        session: AsyncSession,
        case: FinancialResolutionCase,
        *,
        ignored: bool,
        action: FinancialResolutionAction,
        actor_user_id: uuid.UUID,
        note: str,
    ) -> None:
        if case.reconciliation_event_id is None:
            return
        event = await session.get(PaymentReconciliationEvent, case.reconciliation_event_id)
        if event is None or event.tenant_id != case.tenant_id:
            raise PaymentIntegrityError("Financial case reconciliation event is missing or cross-tenant.")
        event.requires_review = False
        event.status = (
            PaymentReconciliationEventStatus.IGNORED
            if ignored
            else PaymentReconciliationEventStatus.PROCESSED
        )
        metadata = dict(event.metadata_json or {})
        metadata["operator_resolution"] = {
            "action": action.value,
            "actor_user_id": str(actor_user_id),
            "resolved_at": datetime.now(UTC).isoformat(),
            "note": note,
        }
        event.metadata_json = metadata

    async def _other_open_case_count(
        self,
        session: AsyncSession,
        case: FinancialResolutionCase,
    ) -> int:
        asset_wallet_id = (case.metadata_json or {}).get("asset_wallet_id")
        if case.wallet_id is None and not asset_wallet_id:
            return 0
        wallet_filter = (
            FinancialResolutionCase.wallet_id == case.wallet_id if case.wallet_id is not None
            else FinancialResolutionCase.metadata_json["asset_wallet_id"].as_string() == asset_wallet_id
        )
        return int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(FinancialResolutionCase)
                    .where(
                        FinancialResolutionCase.tenant_id == case.tenant_id,
                        wallet_filter,
                        FinancialResolutionCase.status != FinancialResolutionCaseStatus.RESOLVED,
                        FinancialResolutionCase.id != case.id,
                    )
                )
            )
            or 0
        )

    @staticmethod
    async def _resolution_wallet(session: AsyncSession, case: FinancialResolutionCase):
        asset_id = (case.metadata_json or {}).get("asset_wallet_id")
        if case.wallet_id is not None:
            model, wallet_id = Wallet, case.wallet_id
        elif asset_id:
            model, wallet_id = AssetWallet, uuid.UUID(asset_id)
        else:
            raise PaymentError("This case has no wallet to resolve.")
        wallet = await session.scalar(select(model).where(
            model.id == wallet_id, model.tenant_id == case.tenant_id,
        ).with_for_update().execution_options(populate_existing=True))
        if wallet is None:
            raise PaymentIntegrityError("Financial case wallet is missing or cross-tenant.")
        return wallet

    async def resolve_case(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        expected_version: int,
        action: FinancialResolutionAction,
        note: str,
        *,
        actor_is_owner: bool,
        payment_service: PaymentService,
    ) -> FinancialResolutionCase:
        case = await self._locked_case(session, tenant_id, case_id)
        self._verify_version(case, expected_version)
        if case.status == FinancialResolutionCaseStatus.RESOLVED:
            return case
        if case.assigned_to_user_id not in (None, actor_user_id):
            raise PaymentIntegrityError("Financial case is assigned to another operator.")
        case.assigned_to_user_id = actor_user_id

        clean_note = note.strip()
        if len(clean_note) < 12:
            raise PaymentError("A resolution note of at least 12 characters is required.")

        if action == FinancialResolutionAction.RETRY_LOCAL_REVERSAL:
            if case.reversal_id is None:
                raise PaymentError("This financial case has no wallet reversal to retry.")
            reversal = await session.get(WalletTopUpReversal, case.reversal_id)
            if reversal is None or reversal.tenant_id != tenant_id:
                raise PaymentIntegrityError("Financial case reversal is missing or cross-tenant.")
            if (reversal.metadata_json or {}).get("origin") != "EXTERNAL_PROVIDER":
                raise PaymentError("Only externally-originated reversals can use local retry resolution.")
            resolved_reversal = await payment_service.resolve_external_wallet_topup_reversal(
                session=session,
                tenant_id=tenant_id,
                reversal_id=reversal.id,
            )
            if resolved_reversal.status != WalletTopUpReversalStatus.COMPLETED:
                raise PaymentIntegrityError("Wallet reversal did not reach COMPLETED state.")
            await self._mark_event_resolved(
                session,
                case,
                ignored=False,
                action=action,
                actor_user_id=actor_user_id,
                note=clean_note,
            )
            wallet = await session.get(Wallet, case.wallet_id) if case.wallet_id else None
            if wallet is not None and await self._other_open_case_count(session, case):
                wallet.is_active = False
            resolution_code = "EXTERNAL_REVERSAL_DEBIT_APPLIED"

        elif action == FinancialResolutionAction.ACKNOWLEDGE_NO_WALLET_IMPACT:
            if case.wallet_id is not None or case.reversal_id is not None or (case.metadata_json or {}).get("asset_wallet_id"):
                raise PaymentError("This action is allowed only for cases with no wallet impact.")
            await self._mark_event_resolved(
                session,
                case,
                ignored=True,
                action=action,
                actor_user_id=actor_user_id,
                note=clean_note,
            )
            resolution_code = "NO_WALLET_IMPACT_ACKNOWLEDGED"

        elif action == FinancialResolutionAction.CLOSE_KEEP_WALLET_FROZEN:
            wallet = await self._resolution_wallet(session, case)
            if wallet.is_active:
                raise PaymentError("Wallet is active; this action is only valid for a frozen wallet.")
            await self._mark_event_resolved(
                session,
                case,
                ignored=False,
                action=action,
                actor_user_id=actor_user_id,
                note=clean_note,
            )
            resolution_code = "WALLET_REMAINS_FROZEN"

        elif action == FinancialResolutionAction.MARK_FALSE_POSITIVE_AND_UNFREEZE:
            if not actor_is_owner:
                raise TenantAccessViolationError("Only an owner can unfreeze a wallet after a false-positive review.")
            if len(clean_note) < 20:
                raise PaymentError("Owner unfreeze requires a resolution note of at least 20 characters.")
            if case.reversal_id is not None:
                raise PaymentError("A case linked to a provider reversal cannot be waived as a false positive.")
            wallet = await self._resolution_wallet(session, case)
            if wallet.is_active:
                raise PaymentError("Wallet is already active.")
            if await self._other_open_case_count(session, case):
                raise PaymentIntegrityError("Wallet has other unresolved financial cases and cannot be reactivated.")
            pending_reversal_count = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(WalletTopUpReversal)
                        .where(
                            WalletTopUpReversal.tenant_id == tenant_id,
                            WalletTopUpReversal.wallet_id == wallet.id,
                            WalletTopUpReversal.status == WalletTopUpReversalStatus.MANUAL_REVIEW,
                        )
                    )
                )
                or 0
            )
            if pending_reversal_count:
                raise PaymentIntegrityError("Wallet has an unresolved provider reversal and cannot be reactivated.")
            wallet.is_active = True
            await self._mark_event_resolved(
                session,
                case,
                ignored=True,
                action=action,
                actor_user_id=actor_user_id,
                note=clean_note,
            )
            resolution_code = "FALSE_POSITIVE_WALLET_REACTIVATED"

        else:  # pragma: no cover - enum guarantees this boundary
            raise PaymentError(f"Unsupported financial resolution action: {action}.")

        case.status = FinancialResolutionCaseStatus.RESOLVED
        case.resolution_code = resolution_code
        case.resolution_note = clean_note
        case.resolved_by_user_id = actor_user_id
        case.resolved_at = datetime.now(UTC)
        case.version += 1
        case_metadata = dict(case.metadata_json or {})
        case_metadata["resolution_action"] = action.value
        case.metadata_json = case_metadata
        await session.flush()
        return case
