from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from packages.core.config import settings
from packages.core.database import get_db_session
from packages.marketplace.models import (
    ContactMethod,
    CustomerInquiry,
    InquiryStatus,
)
from packages.saas.models import PlatformAuditLog

pytestmark = pytest.mark.asyncio
TEST_PLATFORM_TOKEN = "test-platform-token-0123456789abcdef0123456789abcdef"


@pytest_asyncio.fixture
async def platform_client(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[dict[str, Any], None]:
    monkeypatch.setattr(settings, "platform_admin_token", TEST_PLATFORM_TOKEN)
    app.dependency_overrides[get_db_session] = lambda: db_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "session": db_session}

    app.dependency_overrides.clear()


def platform_auth(token: str = TEST_PLATFORM_TOKEN) -> dict[str, str]:
    return {"X-GHBF-Platform-Token": token}


async def test_platform_sales_requires_valid_platform_token(
    platform_client: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = platform_client["client"]

    unauthed = await client.get("/api/v1/platform/sales/inquiries")
    assert unauthed.status_code == 401

    wrong = await client.get("/api/v1/platform/sales/inquiries", headers=platform_auth("invalid-token"))
    assert wrong.status_code == 401

    authed = await client.get("/api/v1/platform/sales/inquiries", headers=platform_auth())
    assert authed.status_code == 200
    assert "items" in authed.json()


async def test_sales_inquiry_lifecycle_and_audit_logging(
    platform_client: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = platform_client["client"]
    session: AsyncSession = platform_client["session"]

    inquiry = CustomerInquiry(
        contact_method=ContactMethod.TELEGRAM,
        contact_handle="@lead_merchant",
        project_notes="Looking for digital keys and gift store.",
        configuration={"format": "combo", "template_key": "digital-goods"},
        estimated_quote={"total_one_time": "89.00", "total_monthly": "49.00"},
        status=InquiryStatus.NEW,
    )
    session.add(inquiry)
    await session.commit()
    await session.refresh(inquiry)

    # 1. List inquiries
    res_list = await client.get("/api/v1/platform/sales/inquiries?search=lead_merchant", headers=platform_auth())
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    assert any(i["id"] == str(inquiry.id) for i in items)

    # 2. Get inquiry detail
    res_get = await client.get(f"/api/v1/platform/sales/inquiries/{inquiry.id}", headers=platform_auth())
    assert res_get.status_code == 200
    detail = res_get.json()
    assert detail["contact_handle"] == "@lead_merchant"
    assert detail["status"] == "NEW"

    # 3. Update status to CONTACTED
    res_patch = await client.patch(
        f"/api/v1/platform/sales/inquiries/{inquiry.id}/status",
        headers=platform_auth(),
        json={"status": "CONTACTED"},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["status"] == "CONTACTED"

    # Verify audit log
    audit = (
        (
            await session.execute(
                select(PlatformAuditLog)
                .where(
                    PlatformAuditLog.resource_id == str(inquiry.id),
                    PlatformAuditLog.action == "inquiry.status_updated",
                )
            )
        )
        .scalars()
        .first()
    )
    assert audit is not None
    assert audit.details["new_status"] == "CONTACTED"


async def test_quote_generation_acceptance_and_immutability(
    platform_client: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = platform_client["client"]
    session: AsyncSession = platform_client["session"]

    inquiry = CustomerInquiry(
        contact_method=ContactMethod.TELEGRAM,
        contact_handle="@vip_seller",
        configuration={"format": "combo", "template_key": "numbers-sms"},
        estimated_quote={"total_one_time": "134.00", "total_monthly": "74.00"},
        status=InquiryStatus.CONTACTED,
    )
    session.add(inquiry)
    await session.commit()
    await session.refresh(inquiry)

    # 1. Generate Quote
    quote_payload = {
        "customer_name": "VIP Enterprise",
        "customer_contact": "@vip_seller",
        "currency": "USD",
        "valid_days": 14,
        "terms": "Custom enterprise SLA with priority support.",
        "notes": "Includes free migration assistance.",
        "lines": [
            {
                "name": "Custom Telegram Bot + Mini App Setup",
                "category": "product_format",
                "item_type": "one_time",
                "amount": "120.00",
                "description": "Custom branded deployment",
            },
            {
                "name": "Enterprise Cloud Hosting",
                "category": "hosting",
                "item_type": "recurring",
                "amount": "60.00",
                "description": "Dedicated webhook routing",
            },
        ],
    }

    res_quote = await client.post(
        f"/api/v1/platform/sales/inquiries/{inquiry.id}/quotes",
        headers=platform_auth(),
        json=quote_payload,
    )
    assert res_quote.status_code == 201, res_quote.text
    q_data = res_quote.json()

    quote_id = uuid.UUID(q_data["id"])
    assert q_data["quote_number"].startswith("Q-")
    assert q_data["version"] == 1
    assert q_data["status"] == "DRAFT"
    assert q_data["total_one_time"] == "120.00"
    assert q_data["total_monthly"] == "60.00"
    assert len(q_data["lines"]) == 2

    # Verify inquiry status advanced to QUOTED
    await session.refresh(inquiry)
    assert inquiry.status == InquiryStatus.QUOTED

    # 2. List Quotes
    res_list = await client.get("/api/v1/platform/sales/quotes?search=VIP", headers=platform_auth())
    assert res_list.status_code == 200
    assert any(q["id"] == str(quote_id) for q in res_list.json()["items"])

    # 3. Accept Quote
    res_accept = await client.post(f"/api/v1/platform/sales/quotes/{quote_id}/accept", headers=platform_auth())
    assert res_accept.status_code == 200, res_accept.text
    accepted_data = res_accept.json()
    assert accepted_data["status"] == "ACCEPTED"
    assert accepted_data["accepted_at"] is not None

    # Verify inquiry status advanced to CONVERTED
    await session.refresh(inquiry)
    assert inquiry.status == InquiryStatus.CONVERTED

    # Verify audit log
    audit = (
        (
            await session.execute(
                select(PlatformAuditLog)
                .where(
                    PlatformAuditLog.action == "quote.accepted",
                    PlatformAuditLog.resource_id == accepted_data["quote_number"],
                )
            )
        )
        .scalars()
        .first()
    )
    assert audit is not None
    assert audit.details["customer_name"] == "VIP Enterprise"
