"""A bounded public store profile; credentials and financial policies have separate APIs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_admin_or_owner, require_staff_or_above
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.operations.service import audit
from packages.tenants.models import Tenant

router = APIRouter(prefix="/admin/store-settings", tags=["store-settings"])
PUBLIC_KEYS = {"store_tagline", "store_description", "store_notice", "support_url", "terms_url",
               "privacy_url", "recipient_label", "recipient_help", "faq", "sales_paused"}


class FAQ(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=3, max_length=160)
    answer: str = Field(min_length=1, max_length=1500)


class StoreSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    sales_paused: bool = False
    expected_version: int = Field(ge=0)
    name: str = Field(min_length=2, max_length=100)
    store_tagline: str = Field(default="", max_length=240)
    store_description: str = Field(default="", max_length=1000)
    store_notice: str = Field(default="", max_length=500)
    support_url: str = Field(default="", max_length=500)
    terms_url: str = Field(default="", max_length=500)
    privacy_url: str = Field(default="", max_length=500)
    recipient_label: str = Field(default="", max_length=80)
    recipient_help: str = Field(default="", max_length=300)
    faq: list[FAQ] = Field(default_factory=list, max_length=12)

    @field_validator("support_url", "terms_url", "privacy_url")
    @classmethod
    def safe_url(cls, value):
        from urllib.parse import urlsplit
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or any(ord(c) < 32 for c in value):
            raise ValueError("Use a public HTTPS URL without credentials.")
        return value


def profile(tenant):
    settings = tenant.settings or {}
    return {"name": tenant.name, "version": settings.get("_store_settings_version", 0),
            **{key: settings.get(key, [] if key == "faq" else False if key == "sales_paused" else "") for key in PUBLIC_KEYS}}


@router.get("")
async def read_settings(principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
                        session: AsyncSession = Depends(get_db_session)):
    tenant = await session.scalar(select(Tenant).where(Tenant.id == principal.tenant_id))
    if tenant is None:
        raise HTTPException(404, "Store not found.")
    return profile(tenant)


@router.put("")
async def save_settings(req: StoreSettingsRequest,
                        principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
                        session: AsyncSession = Depends(get_db_session)):
    tenant = await session.scalar(select(Tenant).where(Tenant.id == principal.tenant_id).with_for_update())
    if tenant is None:
        raise HTTPException(404, "Store not found.")
    settings = dict(tenant.settings or {})
    if settings.get("_store_settings_version", 0) != req.expected_version:
        raise HTTPException(409, "Settings changed in another session. Reload before saving.")
    values = req.model_dump(exclude={"expected_version", "name"})
    tenant.name = req.name
    settings.update(values)
    settings["_store_settings_version"] = req.expected_version + 1
    tenant.settings = settings
    audit(session, principal.tenant_id, principal.user_id, "store.profile_updated", tenant,
          {"fields": sorted(values), "version": settings["_store_settings_version"]})
    await session.commit()
    return profile(tenant)
