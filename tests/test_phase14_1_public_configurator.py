from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from packages.core.database import get_db_session
from packages.marketplace.models import CustomerInquiry, InquiryStatus

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def public_client(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    app.dependency_overrides[get_db_session] = lambda: db_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "session": db_session}

    app.dependency_overrides.clear()


async def test_public_templates_endpoint_returns_catalog(public_client: dict[str, Any]) -> None:
    client: httpx.AsyncClient = public_client["client"]
    res = await client.get("/api/v1/public/templates")
    assert res.status_code == 200, res.text
    data = res.json()
    assert "templates" in data
    templates = data["templates"]
    assert len(templates) == 11

    keys = {t["key"] for t in templates}
    assert "general-commerce" in keys
    assert "numbers-sms" in keys
    assert "reseller-hub" in keys

    for t in templates:
        assert "guidance" in t
        g = t["guidance"]
        assert g is not None
        assert g["product_source"] in {"stored", "provider_api", "hybrid"}
        assert g["operational_complexity"] in {"Low", "Medium", "High"}


async def test_public_integrations_endpoint_returns_offerings(public_client: dict[str, Any]) -> None:
    client: httpx.AsyncClient = public_client["client"]
    res = await client.get("/api/v1/public/integrations")
    assert res.status_code == 200, res.text
    data = res.json()
    assert "integrations" in data
    integrations = data["integrations"]
    assert len(integrations) >= 5

    keys = {i["key"] for i in integrations}
    assert "numbers-sms" in keys
    assert "crypto-payments" in keys
    assert "binance-pay" in keys

    for item in integrations:
        assert float(item["setup_fee"]) >= 0
        assert float(item["monthly_fee"]) >= 0
        assert item["currency"] == "USD"


async def test_public_estimate_calculates_server_authoritative_pricing(public_client: dict[str, Any]) -> None:
    client: httpx.AsyncClient = public_client["client"]

    # Managed Combo Bundle with Wholesale API
    payload = {
        "format": "combo",
        "template_key": "numbers-sms",
        "product_source": "provider_api",
        "delivery_model": "managed",
        "integration_keys": ["numbers-sms"],
    }
    res = await client.post("/api/v1/public/estimate", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["format"] == "combo"
    assert data["template_name"] == "Numbers & SMS"
    # Base combo: 89 setup, 49 monthly
    # Provider api: 15 setup, 10 monthly
    # Numbers-sms integration: 30 setup, 15 monthly
    # Total setup = 89 + 15 + 30 = 134.00
    # Total monthly = 49 + 10 + 15 = 74.00
    assert data["total_one_time"] == "134.00"
    assert data["total_monthly"] == "74.00"
    assert len(data["items"]) >= 4

    # Source code license buyout (no recurring monthly)
    buyout_payload = {
        "format": "combo",
        "template_key": "gaming-store",
        "product_source": "hybrid",
        "delivery_model": "source_license",
        "integration_keys": [],
    }
    res_buyout = await client.post("/api/v1/public/estimate", json=buyout_payload)
    assert res_buyout.status_code == 200, res_buyout.text
    data_buyout = res_buyout.json()
    assert data_buyout["total_monthly"] == "0.00"
    # Base 89 + hybrid 25 + source_license 2000 = 2114.00
    assert data_buyout["total_one_time"] == "2114.00"


async def test_public_inquiry_submission_persists_lead_and_returns_telegram_link(
    public_client: dict[str, Any], monkeypatch,
) -> None:
    client: httpx.AsyncClient = public_client["client"]
    from packages.core.config import settings

    monkeypatch.setattr(settings, "owner_telegram_handle", "FactorySales")
    session: AsyncSession = public_client["session"]

    payload = {
        "contact_method": "TELEGRAM",
        "contact_handle": "@prospect_merchant",
        "project_notes": "We need a bot selling SMS numbers with 5sim integration.",
        "format": "combo",
        "template_key": "numbers-sms",
        "product_source": "provider_api",
        "delivery_model": "managed",
        "integration_keys": ["numbers-sms"],
        "custom_api_request": "Custom SMS Gateway (api.example.com)",
    }
    res = await client.post("/api/v1/public/inquiries", json=payload)
    assert res.status_code == 201, res.text
    data = res.json()

    inquiry_id = uuid.UUID(data["inquiry_id"])
    assert data["status"] == "NEW"
    assert "telegram_link" in data
    assert "https://t.me/" in data["telegram_link"]
    assert "prospect_merchant" in data["telegram_link"]

    # Verify database persistence
    inquiry = await session.get(CustomerInquiry, inquiry_id)
    assert inquiry is not None
    assert inquiry.contact_handle == "@prospect_merchant"
    assert inquiry.status == InquiryStatus.NEW
    assert inquiry.configuration["format"] == "combo"
    assert inquiry.estimated_quote["total_one_time"] == "134.00"
    assert inquiry.estimated_quote["total_monthly"] == "74.00"
    assert inquiry.configuration["custom_api_request"] == "Custom SMS Gateway (api.example.com)"
    assert "Custom SMS Gateway" in inquiry.project_notes

async def test_build_static_configurator_page_loads(public_client: dict[str, Any]) -> None:
    client: httpx.AsyncClient = public_client["client"]
    res = await client.get("/build/")
    assert res.status_code == 200
    assert "Build Your Bot" in res.text
    assert "/build/app.js" in res.text


async def test_contact_destination_matches_before_and_after_submission(public_client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from packages.core.config import settings

    monkeypatch.setattr(settings, "owner_telegram_handle", "FactorySales")
    client = public_client["client"]
    estimate = await client.post("/api/v1/public/estimate", json={})
    assert estimate.json()["telegram_contact_url"] == "https://t.me/FactorySales"
    response = await client.post("/api/v1/public/inquiries", json={
        "contact_handle": "@buyer", "project_notes": "A&B #1 / café",
    })
    assert response.status_code == 201
    link = urlsplit(response.json()["telegram_link"])
    assert link.netloc == "t.me"
    assert link.path == "/FactorySales"
    assert "A&B #1 / café" in parse_qs(link.query)["text"][0]


@pytest.mark.parametrize("value", ["FactorySales", " @FactorySales ", "https://t.me/FactorySales", "t.me/FactorySales/"])
async def test_contact_setting_normalizes_profile_urls(value):
    from packages.core.config import Settings

    assert Settings.normalize_owner_telegram_handle(value) == "FactorySales"


@pytest.mark.parametrize("value", ["https://example.com/FactorySales", "https://t.me/FactorySales?start=x", "bad/name"])
async def test_contact_setting_rejects_invalid_destinations(value):
    from packages.core.config import Settings

    with pytest.raises(ValueError):
        Settings.normalize_owner_telegram_handle(value)


async def test_inquiry_works_without_configured_telegram_contact(public_client, monkeypatch):
    from packages.core.config import settings

    monkeypatch.setattr(settings, "owner_telegram_handle", "")
    client = public_client["client"]
    estimate = await client.post("/api/v1/public/estimate", json={})
    assert estimate.json()["telegram_contact_url"] is None
    inquiry = await client.post("/api/v1/public/inquiries", json={"contact_handle": "@buyer"})
    assert inquiry.status_code == 201
    assert inquiry.json()["telegram_link"] is None
