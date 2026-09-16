from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import settings
from packages.payments.models import (
    FinancialResolutionCase,
    FinancialResolutionCaseStatus,
    PaymentIntent,
    PaymentMethodConfig,
    PaymentObservation,
    PaymentObservationStatus,
    PaymentProviderConfig,
    WalletTopUpReversal,
    WalletTopUpReversalStatus,
)
from packages.payments.state_machine import PaymentIntentStatus


@dataclass(frozen=True, slots=True)
class PaymentOperationsAlert:
    code: str
    severity: str
    count: int
    message: str


@dataclass(frozen=True, slots=True)
class PaymentOperationsSnapshot:
    tenant_id: uuid.UUID
    generated_at: datetime
    stale_after_seconds: int
    status: str
    open_intents: int
    pending_intents: int
    processing_intents: int
    unknown_intents: int
    stale_provider_intents: int
    creation_ambiguities: int
    manual_review_observations: int
    reversal_reconciliation_items: int
    open_financial_cases: int
    enabled_provider_configs: int
    enabled_payment_methods: int
    alerts: tuple[PaymentOperationsAlert, ...]


class PaymentOperationsService:
    """Read-only operational health for one tenant's payment system.

    This service deliberately performs no reconciliation, settlement, refund, or wallet mutation.
    It only surfaces durable financial states that already exist in the database so operators can
    distinguish normal in-flight payments from states that require investigation.
    """

    OPEN_STATUSES = (
        PaymentIntentStatus.PENDING,
        PaymentIntentStatus.PROCESSING,
        PaymentIntentStatus.UNKNOWN,
    )

    @staticmethod
    def _has_creation_ambiguity(intent: PaymentIntent) -> bool:
        marker = (intent.metadata_json or {}).get("_provider_creation_ambiguity")
        return isinstance(marker, dict) and marker.get("reason") == "transport_outcome_unknown"

    async def snapshot(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        stale_after_seconds: int | None = None,
        now: datetime | None = None,
    ) -> PaymentOperationsSnapshot:
        stale_seconds = int(
            stale_after_seconds
            if stale_after_seconds is not None
            else settings.payment_operations_stale_seconds
        )
        stale_seconds = max(60, min(stale_seconds, 86400))
        generated_at = now or datetime.now(UTC)
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        cutoff = generated_at - timedelta(seconds=stale_seconds)

        status_rows = (
            await session.execute(
                select(PaymentIntent.status, func.count())
                .where(
                    PaymentIntent.tenant_id == tenant_id,
                    PaymentIntent.status.in_(self.OPEN_STATUSES),
                )
                .group_by(PaymentIntent.status)
            )
        ).all()
        status_counts = {status: int(count) for status, count in status_rows}
        pending = status_counts.get(PaymentIntentStatus.PENDING, 0)
        processing = status_counts.get(PaymentIntentStatus.PROCESSING, 0)
        unknown = status_counts.get(PaymentIntentStatus.UNKNOWN, 0)

        stale_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PaymentIntent)
                    .where(
                        PaymentIntent.tenant_id == tenant_id,
                        PaymentIntent.status.in_(self.OPEN_STATUSES),
                        PaymentIntent.provider_payment_id.isnot(None),
                        PaymentIntent.updated_at <= cutoff,
                    )
                )
            ).scalar_one()
        )

        # Keep JSON inspection in Python for SQLite/PostgreSQL portability and to avoid
        # binding operational correctness to one backend's JSON operator semantics.
        ambiguity_candidates = list(
            (
                await session.execute(
                    select(PaymentIntent).where(
                        PaymentIntent.tenant_id == tenant_id,
                        PaymentIntent.status == PaymentIntentStatus.UNKNOWN,
                        PaymentIntent.provider_payment_id.is_(None),
                    )
                )
            ).scalars().all()
        )
        ambiguity_count = sum(
            1 for intent in ambiguity_candidates if self._has_creation_ambiguity(intent)
        )

        manual_review = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PaymentObservation)
                    .where(
                        PaymentObservation.tenant_id == tenant_id,
                        PaymentObservation.status == PaymentObservationStatus.MANUAL_REVIEW,
                    )
                )
            ).scalar_one()
        )
        reversal_review = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(WalletTopUpReversal)
                    .where(
                        WalletTopUpReversal.tenant_id == tenant_id,
                        WalletTopUpReversal.status.in_(
                            (
                                WalletTopUpReversalStatus.RECONCILIATION_REQUIRED,
                                WalletTopUpReversalStatus.MANUAL_REVIEW,
                            )
                        ),
                    )
                )
            ).scalar_one()
        )
        open_cases = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(FinancialResolutionCase)
                    .where(
                        FinancialResolutionCase.tenant_id == tenant_id,
                        FinancialResolutionCase.status != FinancialResolutionCaseStatus.RESOLVED,
                    )
                )
            ).scalar_one()
        )
        enabled_providers = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PaymentProviderConfig)
                    .where(
                        PaymentProviderConfig.tenant_id == tenant_id,
                        PaymentProviderConfig.is_enabled.is_(True),
                    )
                )
            ).scalar_one()
        )
        enabled_methods = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PaymentMethodConfig)
                    .where(
                        PaymentMethodConfig.tenant_id == tenant_id,
                        PaymentMethodConfig.is_enabled.is_(True),
                    )
                )
            ).scalar_one()
        )

        alerts: list[PaymentOperationsAlert] = []
        if ambiguity_count:
            alerts.append(
                PaymentOperationsAlert(
                    code="PAYMENT_CREATION_AMBIGUITY",
                    severity="HIGH",
                    count=ambiguity_count,
                    message="Provider create outcome is unknown and requires safe recovery/reconciliation.",
                )
            )
        if stale_count:
            alerts.append(
                PaymentOperationsAlert(
                    code="STALE_PROVIDER_PAYMENT",
                    severity="MEDIUM",
                    count=stale_count,
                    message="Provider-backed payments have not converged within the configured window.",
                )
            )
        if manual_review:
            alerts.append(
                PaymentOperationsAlert(
                    code="MANUAL_PAYMENT_REVIEW",
                    severity="MEDIUM",
                    count=manual_review,
                    message="Payment observations are waiting for explicit operator review.",
                )
            )
        if reversal_review:
            alerts.append(
                PaymentOperationsAlert(
                    code="REVERSAL_RECONCILIATION",
                    severity="HIGH",
                    count=reversal_review,
                    message="Top-up reversals require reconciliation or manual review.",
                )
            )
        if open_cases:
            alerts.append(
                PaymentOperationsAlert(
                    code="FINANCIAL_CASES_OPEN",
                    severity="HIGH",
                    count=open_cases,
                    message="Durable financial resolution cases remain open.",
                )
            )

        high = any(alert.severity == "HIGH" for alert in alerts)
        status = "ATTENTION" if high else ("DEGRADED" if alerts else "HEALTHY")
        return PaymentOperationsSnapshot(
            tenant_id=tenant_id,
            generated_at=generated_at,
            stale_after_seconds=stale_seconds,
            status=status,
            open_intents=pending + processing + unknown,
            pending_intents=pending,
            processing_intents=processing,
            unknown_intents=unknown,
            stale_provider_intents=stale_count,
            creation_ambiguities=ambiguity_count,
            manual_review_observations=manual_review,
            reversal_reconciliation_items=reversal_review,
            open_financial_cases=open_cases,
            enabled_provider_configs=enabled_providers,
            enabled_payment_methods=enabled_methods,
            alerts=tuple(alerts),
        )
