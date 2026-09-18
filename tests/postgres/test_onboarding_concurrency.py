import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from packages.marketplace.models import CommercialQuote, QuoteStatus
from packages.marketplace.onboarding import CustomerOnboardingService
from packages.telegram.admin_login import AdminLoginService
from packages.telegram.models import Bot
from packages.tenants.models import Membership, Tenant

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_concurrent_onboarding_creates_one_tenant_and_owner(postgres_session_factory, monkeypatch):
    monkeypatch.setattr(AdminLoginService, "_redis_call", AsyncMock(return_value=True))
    async with postgres_session_factory() as session:
        quote = CommercialQuote(
            quote_number=f"Q-{uuid.uuid4().hex[:12]}", version=1,
            customer_name="Concurrent", customer_contact="@display_only",
            status=QuoteStatus.ACCEPTED, currency="USD", total_one_time=89, total_monthly=49,
        )
        session.add(quote)
        await session.commit()
        quote_id = quote.id

    async def onboard():
        async with postgres_session_factory() as session:
            return await CustomerOnboardingService.onboard_from_quote(
                session, quote_id=quote_id, owner_telegram_id=123456789,
            )

    first, second = await asyncio.gather(onboard(), onboard())
    assert first.tenant_id == second.tenant_id
    assert first.owner_id == second.owner_id
    assert sorted([first.already_existed, second.already_existed]) == [False, True]
    async with postgres_session_factory() as session:
        assert await session.scalar(select(func.count(Tenant.id))) == 1
        assert await session.scalar(select(func.count(Membership.id)).where(Membership.tenant_id == first.tenant_id)) == 1
        assert await session.scalar(select(func.count(Bot.id)).where(Bot.tenant_id == first.tenant_id)) == 0
