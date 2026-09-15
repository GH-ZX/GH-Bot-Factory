from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Order
from packages.commerce.state_machine import OrderStatus
from packages.fulfillment.models import (
    FulfillmentAttempt,
    FulfillmentJobRecord,
    FulfillmentJobStatus,
    FulfillmentStatus,
)
from packages.payments.models import (
    FinancialResolutionCase,
    FinancialResolutionCaseStatus,
    PaymentIntent,
    PaymentIntentPurpose,
    PaymentReconciliationEvent,
    Wallet,
    WalletTopUpReversal,
    WalletTopUpReversalStatus,
)
from packages.payments.state_machine import PaymentIntentStatus
from packages.providers.models import Provider
from packages.tenants.models import AuditLog


_CAPTURED_ORDER_STATUSES = (
    OrderStatus.PAID,
    OrderStatus.PROCESSING,
    OrderStatus.PARTIALLY_FULFILLED,
    OrderStatus.FULFILLED,
    OrderStatus.REFUNDED,
)


class AdminAnalyticsService:
    """Read-only tenant analytics built from authoritative operational tables.

    Monetary values are never combined across currencies. All period metrics use UTC
    timestamps and all queries are explicitly tenant scoped.
    """

    @staticmethod
    def period_bounds(days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
        if days not in {7, 30, 90}:
            raise ValueError("Analytics period must be one of 7, 30, or 90 days.")
        end = now or datetime.now(UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        start_day = (end - timedelta(days=days - 1)).date()
        start = datetime.combine(start_day, datetime.min.time(), tzinfo=UTC)
        return start, end

    async def overview(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        *,
        days: int = 30,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        start, end = self.period_bounds(days, now)

        order_rows = (
            await session.execute(
                select(
                    Order.currency,
                    func.count(Order.id),
                    func.coalesce(func.sum(Order.total_amount), 0),
                    func.coalesce(
                        func.sum(
                            case(
                                (Order.status == OrderStatus.REFUNDED, Order.total_amount),
                                else_=Decimal("0.00"),
                            )
                        ),
                        0,
                    ),
                )
                .where(
                    Order.tenant_id == tenant_id,
                    Order.created_at >= start,
                    Order.created_at <= end,
                    Order.status.in_(_CAPTURED_ORDER_STATUSES),
                )
                .group_by(Order.currency)
            )
        ).all()

        status_rows = (
            await session.execute(
                select(Order.status, func.count(Order.id))
                .where(
                    Order.tenant_id == tenant_id,
                    Order.created_at >= start,
                    Order.created_at <= end,
                )
                .group_by(Order.status)
            )
        ).all()
        order_status_counts = {status.value: int(count) for status, count in status_rows}
        order_count = sum(order_status_counts.values())

        topup_rows = (
            await session.execute(
                select(
                    PaymentIntent.currency,
                    func.count(PaymentIntent.id),
                    func.coalesce(func.sum(PaymentIntent.amount), 0),
                )
                .where(
                    PaymentIntent.tenant_id == tenant_id,
                    PaymentIntent.purpose == PaymentIntentPurpose.WALLET_TOPUP,
                    PaymentIntent.status == PaymentIntentStatus.SUCCEEDED,
                    PaymentIntent.created_at >= start,
                    PaymentIntent.created_at <= end,
                )
                .group_by(PaymentIntent.currency)
            )
        ).all()

        reversal_rows = (
            await session.execute(
                select(
                    WalletTopUpReversal.currency,
                    func.count(WalletTopUpReversal.id),
                    func.coalesce(func.sum(WalletTopUpReversal.amount), 0),
                )
                .where(
                    WalletTopUpReversal.tenant_id == tenant_id,
                    WalletTopUpReversal.status == WalletTopUpReversalStatus.COMPLETED,
                    WalletTopUpReversal.completed_at.is_not(None),
                    WalletTopUpReversal.completed_at >= start,
                    WalletTopUpReversal.completed_at <= end,
                )
                .group_by(WalletTopUpReversal.currency)
            )
        ).all()

        liability_rows = (
            await session.execute(
                select(
                    Wallet.currency,
                    func.count(Wallet.id),
                    func.coalesce(func.sum(Wallet.balance), 0),
                )
                .where(Wallet.tenant_id == tenant_id)
                .group_by(Wallet.currency)
            )
        ).all()

        financials: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "currency": "",
                "order_count": 0,
                "gross_order_value": Decimal("0.00"),
                "refunded_order_value": Decimal("0.00"),
                "net_order_value": Decimal("0.00"),
                "topup_count": 0,
                "topup_value": Decimal("0.00"),
                "reversal_count": 0,
                "reversal_value": Decimal("0.00"),
                "wallet_count": 0,
                "wallet_liability": Decimal("0.00"),
            }
        )
        for currency, count, gross, refunded in order_rows:
            row = financials[currency]
            row.update(
                currency=currency,
                order_count=int(count),
                gross_order_value=Decimal(gross),
                refunded_order_value=Decimal(refunded),
                net_order_value=Decimal(gross) - Decimal(refunded),
            )
        for currency, count, amount in topup_rows:
            row = financials[currency]
            row.update(currency=currency, topup_count=int(count), topup_value=Decimal(amount))
        for currency, count, amount in reversal_rows:
            row = financials[currency]
            row.update(
                currency=currency,
                reversal_count=int(count),
                reversal_value=Decimal(amount),
            )
        for currency, count, amount in liability_rows:
            row = financials[currency]
            row.update(
                currency=currency,
                wallet_count=int(count),
                wallet_liability=Decimal(amount),
            )

        attempt_rows = (
            await session.execute(
                select(
                    FulfillmentAttempt.status,
                    func.count(FulfillmentAttempt.id),
                )
                .where(
                    FulfillmentAttempt.tenant_id == tenant_id,
                    FulfillmentAttempt.started_at >= start,
                    FulfillmentAttempt.started_at <= end,
                )
                .group_by(FulfillmentAttempt.status)
            )
        ).all()
        attempt_counts = {status.value: int(count) for status, count in attempt_rows}
        attempt_total = sum(attempt_counts.values())
        attempt_succeeded = attempt_counts.get(FulfillmentStatus.SUCCEEDED.value, 0)
        success_rate = (
            round((attempt_succeeded / attempt_total) * 100, 2) if attempt_total else None
        )

        provider_rows = (
            await session.execute(
                select(
                    FulfillmentAttempt.provider_id,
                    Provider.name,
                    FulfillmentAttempt.cost_currency,
                    func.count(FulfillmentAttempt.id),
                    func.coalesce(
                        func.sum(
                            case(
                                (FulfillmentAttempt.status == FulfillmentStatus.SUCCEEDED, 1),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(func.sum(FulfillmentAttempt.cost_amount), 0),
                )
                .outerjoin(Provider, Provider.id == FulfillmentAttempt.provider_id)
                .where(
                    FulfillmentAttempt.tenant_id == tenant_id,
                    FulfillmentAttempt.started_at >= start,
                    FulfillmentAttempt.started_at <= end,
                )
                .group_by(
                    FulfillmentAttempt.provider_id,
                    Provider.name,
                    FulfillmentAttempt.cost_currency,
                )
                .order_by(func.count(FulfillmentAttempt.id).desc())
            )
        ).all()
        providers = []
        for provider_id, provider_name, currency, attempts, succeeded, cost in provider_rows:
            attempts_int = int(attempts)
            succeeded_int = int(succeeded)
            providers.append(
                {
                    "provider_id": provider_id,
                    "provider_name": provider_name or "Unassigned",
                    "currency": currency,
                    "attempts": attempts_int,
                    "succeeded": succeeded_int,
                    "non_succeeded": attempts_int - succeeded_int,
                    "success_rate": round((succeeded_int / attempts_int) * 100, 2)
                    if attempts_int
                    else None,
                    "cost_amount": Decimal(cost),
                }
            )

        dead_letters = await session.scalar(
            select(func.count())
            .select_from(FulfillmentJobRecord)
            .where(
                FulfillmentJobRecord.tenant_id == tenant_id,
                FulfillmentJobRecord.status == FulfillmentJobStatus.DEAD_LETTER,
            )
        )
        open_cases = await session.scalar(
            select(func.count())
            .select_from(FinancialResolutionCase)
            .where(
                FinancialResolutionCase.tenant_id == tenant_id,
                FinancialResolutionCase.status != FinancialResolutionCaseStatus.RESOLVED,
            )
        )
        frozen_wallets = await session.scalar(
            select(func.count())
            .select_from(Wallet)
            .where(Wallet.tenant_id == tenant_id, Wallet.is_active.is_(False))
        )
        reconciliation_reviews = await session.scalar(
            select(func.count())
            .select_from(PaymentReconciliationEvent)
            .where(
                PaymentReconciliationEvent.tenant_id == tenant_id,
                PaymentReconciliationEvent.requires_review.is_(True),
            )
        )
        audit_events = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.created_at >= start,
                AuditLog.created_at <= end,
            )
        )

        order_daily_rows = (
            await session.execute(
                select(
                    func.date(Order.created_at),
                    Order.currency,
                    func.count(Order.id),
                    func.coalesce(func.sum(Order.total_amount), 0),
                )
                .where(
                    Order.tenant_id == tenant_id,
                    Order.created_at >= start,
                    Order.created_at <= end,
                    Order.status.in_(_CAPTURED_ORDER_STATUSES),
                )
                .group_by(func.date(Order.created_at), Order.currency)
            )
        ).all()
        topup_daily_rows = (
            await session.execute(
                select(
                    func.date(PaymentIntent.created_at),
                    PaymentIntent.currency,
                    func.count(PaymentIntent.id),
                    func.coalesce(func.sum(PaymentIntent.amount), 0),
                )
                .where(
                    PaymentIntent.tenant_id == tenant_id,
                    PaymentIntent.purpose == PaymentIntentPurpose.WALLET_TOPUP,
                    PaymentIntent.status == PaymentIntentStatus.SUCCEEDED,
                    PaymentIntent.created_at >= start,
                    PaymentIntent.created_at <= end,
                )
                .group_by(func.date(PaymentIntent.created_at), PaymentIntent.currency)
            )
        ).all()
        daily: dict[tuple[str, str], dict[str, Any]] = {}
        for raw_day, currency, count, amount in order_daily_rows:
            day = self._normalize_day(raw_day)
            daily[(day, currency)] = {
                "date": day,
                "currency": currency,
                "order_count": int(count),
                "order_value": Decimal(amount),
                "topup_count": 0,
                "topup_value": Decimal("0.00"),
            }
        for raw_day, currency, count, amount in topup_daily_rows:
            day = self._normalize_day(raw_day)
            row = daily.setdefault(
                (day, currency),
                {
                    "date": day,
                    "currency": currency,
                    "order_count": 0,
                    "order_value": Decimal("0.00"),
                    "topup_count": 0,
                    "topup_value": Decimal("0.00"),
                },
            )
            row["topup_count"] = int(count)
            row["topup_value"] = Decimal(amount)

        return {
            "period": {"days": days, "start": start, "end": end},
            "orders": {
                "total": order_count,
                "fulfilled": order_status_counts.get(OrderStatus.FULFILLED.value, 0),
                "failed": order_status_counts.get(OrderStatus.FAILED.value, 0),
                "refunded": order_status_counts.get(OrderStatus.REFUNDED.value, 0),
                "by_status": order_status_counts,
            },
            "fulfillment": {
                "attempts": attempt_total,
                "succeeded": attempt_succeeded,
                "success_rate": success_rate,
                "by_status": attempt_counts,
            },
            "operations": {
                "dead_letter_jobs": int(dead_letters or 0),
                "open_financial_cases": int(open_cases or 0),
                "frozen_wallets": int(frozen_wallets or 0),
                "reconciliation_reviews": int(reconciliation_reviews or 0),
                "audit_events": int(audit_events or 0),
            },
            "financials": sorted(financials.values(), key=lambda row: row["currency"]),
            "providers": providers,
            "daily": sorted(daily.values(), key=lambda row: (row["date"], row["currency"])),
        }

    @staticmethod
    def _normalize_day(raw_day: Any) -> str:
        if isinstance(raw_day, datetime):
            return raw_day.date().isoformat()
        if isinstance(raw_day, date):
            return raw_day.isoformat()
        return str(raw_day)
