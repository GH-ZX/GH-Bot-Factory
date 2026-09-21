import asyncio

import pytest
from sqlalchemy import func, select

from packages.core.system_models import SystemInstallState
from packages.setup.service import SetupError, install_web_factory
from packages.tenants.models import Membership, Tenant

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_concurrent_web_setup_has_exactly_one_operator(postgres_session_factory):
    async with postgres_session_factory() as session:
        session.add(SystemInstallState(id=1, is_initialized=False))
        await session.commit()

    async def initialize(suffix):
        async with postgres_session_factory() as session:
            try:
                return await install_web_factory(session=session, tenant_slug=f"factory-{suffix}",
                    tenant_name="Factory", username=f"owner_{suffix}", password="test-only-setup-password")
            except SetupError as exc:
                return exc.code

    results = await asyncio.gather(initialize("first"), initialize("second"))
    assert sum(r == "ALREADY_INITIALIZED" for r in results) == 1
    async with postgres_session_factory() as session:
        assert await session.scalar(select(func.count(Tenant.id))) == 1
        assert await session.scalar(select(func.count(Membership.id))) == 1
        state = await session.get(SystemInstallState, 1)
        assert state.is_initialized and state.operator_user_id is not None
