from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.marketplace.models import (
    CommercialQuote,
    CustomerInquiry,
    DeploymentHandoff,
    HandoffStatus,
    InquiryStatus,
    QuoteStatus,
    TenantIntegrationEntitlement,
)


class CommercialGovernanceService:
    @classmethod
    async def collect_metrics(cls, session: AsyncSession) -> dict[str, Any]:
        # 1. Inquiries metrics
        total_inquiries = (
            await session.execute(select(func.count(CustomerInquiry.id)))
        ).scalar_one()

        inq_status_counts: dict[str, int] = {st.value: 0 for st in InquiryStatus}
        inq_rows = (
            await session.execute(
                select(CustomerInquiry.status, func.count(CustomerInquiry.id)).group_by(CustomerInquiry.status)
            )
        ).all()
        for st, count in inq_rows:
            inq_status_counts[st.value] = count

        converted_count = inq_status_counts.get("CONVERTED", 0)
        conversion_rate = (
            round((converted_count / total_inquiries) * 100, 1) if total_inquiries > 0 else 0.0
        )

        # 2. Quotes metrics
        total_quotes = (
            await session.execute(select(func.count(CommercialQuote.id)))
        ).scalar_one()

        quote_status_counts: dict[str, int] = {st.value: 0 for st in QuoteStatus}
        quote_rows = (
            await session.execute(
                select(CommercialQuote.status, func.count(CommercialQuote.id)).group_by(CommercialQuote.status)
            )
        ).all()
        for st, count in quote_rows:
            quote_status_counts[st.value] = count

        # Active pipeline value (DRAFT and SENT)
        pipeline_sum = (
            await session.execute(
                select(
                    func.coalesce(func.sum(CommercialQuote.total_one_time), Decimal("0.00")),
                    func.coalesce(func.sum(CommercialQuote.total_monthly), Decimal("0.00")),
                ).where(CommercialQuote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT]))
            )
        ).one()
        pipeline_one_time, pipeline_monthly = pipeline_sum

        # Accepted revenue
        accepted_sum = (
            await session.execute(
                select(
                    func.coalesce(func.sum(CommercialQuote.total_one_time), Decimal("0.00")),
                    func.coalesce(func.sum(CommercialQuote.total_monthly), Decimal("0.00")),
                ).where(CommercialQuote.status == QuoteStatus.ACCEPTED)
            )
        ).one()
        accepted_one_time, accepted_monthly = accepted_sum

        # 3. Deployment handoffs
        total_handoffs = (
            await session.execute(select(func.count(DeploymentHandoff.id)))
        ).scalar_one()

        active_handoffs = (
            await session.execute(
                select(func.count(DeploymentHandoff.id)).where(
                    DeploymentHandoff.status.in_([HandoffStatus.EXPORTED, HandoffStatus.HANDED_OFF])
                )
            )
        ).scalar_one()

        # 4. Top integrations by entitlement
        ent_rows = (
            await session.execute(
                select(
                    TenantIntegrationEntitlement.integration_key,
                    func.count(TenantIntegrationEntitlement.id),
                )
                .where(TenantIntegrationEntitlement.is_enabled.is_(True))
                .group_by(TenantIntegrationEntitlement.integration_key)
                .order_by(func.count(TenantIntegrationEntitlement.id).desc())
                .limit(5)
            )
        ).all()
        top_integrations = [
            {"integration_key": k, "active_tenants": cnt} for k, cnt in ent_rows
        ]

        return {
            "total_inquiries": total_inquiries,
            "inquiries_by_status": inq_status_counts,
            "inquiry_conversion_rate_percent": conversion_rate,
            "total_quotes": total_quotes,
            "quotes_by_status": quote_status_counts,
            "pipeline_one_time": str(pipeline_one_time),
            "pipeline_monthly": str(pipeline_monthly),
            "accepted_one_time": str(accepted_one_time),
            "accepted_monthly": str(accepted_monthly),
            "total_handoffs": total_handoffs,
            "active_handoffs": active_handoffs,
            "top_integrations": top_integrations,
        }
