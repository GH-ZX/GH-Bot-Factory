from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import require_admin_or_owner, require_staff_or_above
from packages.core.auth import AuthenticatedPrincipal
from packages.core.database import get_db_session
from packages.payments.exceptions import PaymentError, PaymentIntegrityError
from packages.payments.models import (
    PaymentMethodConfig,
    PaymentMethodType,
    PaymentObservation,
    PaymentObservationStatus,
    PaymentVerificationMode,
)
from packages.payments.platform import PaymentPlatformService
from packages.tenants.models import AuditLog

router = APIRouter(prefix="/admin/payments", tags=["admin-payments"])


class PaymentMethodCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    method_type: PaymentMethodType
    verification_mode: PaymentVerificationMode
    provider_name: str | None = Field(default=None, max_length=50)
    asset: str | None = Field(default=None, max_length=24)
    network: str | None = Field(default=None, max_length=64)
    destination_address: str | None = Field(default=None, max_length=255)
    destination_memo: str | None = Field(default=None, max_length=255)
    instructions: str | None = Field(default=None, max_length=2000)
    is_enabled: bool = True
    requires_admin_approval: bool = False
    auto_credit_enabled: bool = False
    auto_credit_target: str = Field(default="ASSET_WALLET", pattern="^(ASSET_WALLET|SETTLEMENT_WALLET)$")
    settings: dict[str, Any] = Field(default_factory=dict)


class PaymentMethodUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    provider_name: str | None = Field(default=None, max_length=50)
    asset: str | None = Field(default=None, max_length=24)
    network: str | None = Field(default=None, max_length=64)
    destination_address: str | None = Field(default=None, max_length=255)
    destination_memo: str | None = Field(default=None, max_length=255)
    instructions: str | None = Field(default=None, max_length=2000)
    requires_admin_approval: bool | None = None
    auto_credit_enabled: bool | None = None
    auto_credit_target: str | None = Field(default=None, pattern="^(ASSET_WALLET|SETTLEMENT_WALLET)$")
    settings: dict[str, Any] | None = None


class PaymentMethodEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_enabled: bool


class PaymentMethodResponse(BaseModel):
    id: uuid.UUID
    code: str
    display_name: str
    method_type: str
    verification_mode: str
    provider_name: str | None
    asset: str | None
    network: str | None
    destination_address: str | None
    destination_memo: str | None
    instructions: str | None
    is_enabled: bool
    requires_admin_approval: bool
    auto_credit_enabled: bool
    auto_credit_target: str
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, method: PaymentMethodConfig) -> PaymentMethodResponse:
        return cls(
            id=method.id,
            code=method.code,
            display_name=method.display_name,
            method_type=method.method_type.value,
            verification_mode=method.verification_mode.value,
            provider_name=method.provider_name,
            asset=method.asset,
            network=method.network,
            destination_address=method.destination_address,
            destination_memo=method.destination_memo,
            instructions=method.instructions,
            is_enabled=method.is_enabled,
            requires_admin_approval=method.requires_admin_approval,
            auto_credit_enabled=method.auto_credit_enabled,
            auto_credit_target=method.auto_credit_target,
            settings=method.settings_json or {},
            created_at=method.created_at,
            updated_at=method.updated_at,
        )


class PaymentObservationResponse(BaseModel):
    id: uuid.UUID
    payment_intent_id: uuid.UUID
    payment_method_id: uuid.UUID
    source: str
    status: str
    assurance_level: str | None
    external_reference: str | None
    asset: str | None
    network: str | None
    destination_address: str | None
    asset_amount: Decimal | None
    settlement_amount: Decimal | None
    settlement_currency: str | None
    confirmations: int | None
    is_final: bool
    observed_at: datetime | None
    verified_at: datetime | None
    verified_by_user_id: uuid.UUID | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, observation: PaymentObservation) -> PaymentObservationResponse:
        return cls(
            id=observation.id,
            payment_intent_id=observation.payment_intent_id,
            payment_method_id=observation.payment_method_id,
            source=observation.source.value,
            status=observation.status.value,
            assurance_level=(
                observation.assurance_level.value if observation.assurance_level is not None else None
            ),
            external_reference=observation.external_reference,
            asset=observation.asset,
            network=observation.network,
            destination_address=observation.destination_address,
            asset_amount=observation.asset_amount,
            settlement_amount=observation.settlement_amount,
            settlement_currency=observation.settlement_currency,
            confirmations=observation.confirmations,
            is_final=observation.is_final,
            observed_at=observation.observed_at,
            verified_at=observation.verified_at,
            verified_by_user_id=observation.verified_by_user_id,
            rejection_reason=observation.rejection_reason,
            created_at=observation.created_at,
            updated_at=observation.updated_at,
        )


class ManualApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved_amount: Decimal | None = Field(default=None, gt=Decimal(0))
    approved_currency: str | None = Field(default=None, min_length=3, max_length=3)


class RejectObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


def get_payment_platform_service() -> PaymentPlatformService:
    return PaymentPlatformService()


async def _audit(
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
    )


@router.get("/methods", response_model=list[PaymentMethodResponse])
async def list_payment_methods(
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
    service: PaymentPlatformService = Depends(get_payment_platform_service),
) -> list[PaymentMethodResponse]:
    methods = await service.list_methods(session, tenant_id=principal.tenant_id)
    return [PaymentMethodResponse.from_model(method) for method in methods]


@router.post("/methods", response_model=PaymentMethodResponse, status_code=status.HTTP_201_CREATED)
async def create_payment_method(
    req: PaymentMethodCreateRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
    service: PaymentPlatformService = Depends(get_payment_platform_service),
) -> PaymentMethodResponse:
    try:
        method = await service.create_method(
            session,
            tenant_id=principal.tenant_id,
            code=req.code,
            display_name=req.display_name,
            method_type=req.method_type,
            verification_mode=req.verification_mode,
            provider_name=req.provider_name,
            asset=req.asset,
            network=req.network,
            destination_address=req.destination_address,
            destination_memo=req.destination_memo,
            instructions=req.instructions,
            is_enabled=req.is_enabled,
            requires_admin_approval=req.requires_admin_approval,
            auto_credit_enabled=req.auto_credit_enabled,
            auto_credit_target=req.auto_credit_target,
            settings=req.settings,
        )
        await _audit(
            session,
            principal,
            action="PAYMENT_METHOD_CREATED",
            resource_type="payment_method",
            resource_id=str(method.id),
            details={"code": method.code, "method_type": method.method_type.value},
        )
        await session.flush()
        return PaymentMethodResponse.from_model(method)
    except (PaymentError, PaymentIntegrityError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.patch("/methods/{method_id}", response_model=PaymentMethodResponse)
async def update_payment_method(
    method_id: uuid.UUID,
    req: PaymentMethodUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
    service: PaymentPlatformService = Depends(get_payment_platform_service),
) -> PaymentMethodResponse:
    try:
        method = await service.update_method(
            session,
            tenant_id=principal.tenant_id,
            method_id=method_id,
            display_name=req.display_name,
            provider_name=req.provider_name,
            asset=req.asset,
            network=req.network,
            destination_address=req.destination_address,
            destination_memo=req.destination_memo,
            instructions=req.instructions,
            requires_admin_approval=req.requires_admin_approval,
            auto_credit_enabled=req.auto_credit_enabled,
            auto_credit_target=req.auto_credit_target,
            settings=req.settings,
        )
        await _audit(
            session,
            principal,
            action="PAYMENT_METHOD_UPDATED",
            resource_type="payment_method",
            resource_id=str(method.id),
        )
        await session.flush()
        return PaymentMethodResponse.from_model(method)
    except (PaymentError, PaymentIntegrityError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/methods/{method_id}/enabled", response_model=PaymentMethodResponse)
async def set_payment_method_enabled(
    method_id: uuid.UUID,
    req: PaymentMethodEnabledRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
    service: PaymentPlatformService = Depends(get_payment_platform_service),
) -> PaymentMethodResponse:
    try:
        method = await service.set_method_enabled(
            session,
            tenant_id=principal.tenant_id,
            method_id=method_id,
            is_enabled=req.is_enabled,
        )
        await _audit(
            session,
            principal,
            action="PAYMENT_METHOD_ENABLED_CHANGED",
            resource_type="payment_method",
            resource_id=str(method.id),
            details={"is_enabled": method.is_enabled},
        )
        await session.flush()
        return PaymentMethodResponse.from_model(method)
    except PaymentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/observations", response_model=list[PaymentObservationResponse])
async def list_payment_observations(
    observation_status: PaymentObservationStatus | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=200),
    principal: AuthenticatedPrincipal = Depends(require_staff_or_above),
    session: AsyncSession = Depends(get_db_session),
) -> list[PaymentObservationResponse]:
    stmt = select(PaymentObservation).where(PaymentObservation.tenant_id == principal.tenant_id)
    if observation_status is not None:
        stmt = stmt.where(PaymentObservation.status == observation_status)
    stmt = stmt.order_by(PaymentObservation.created_at.desc()).limit(limit)
    observations = list((await session.execute(stmt)).scalars().all())
    return [PaymentObservationResponse.from_model(item) for item in observations]


@router.post("/observations/{observation_id}/approve", response_model=PaymentObservationResponse)
async def approve_payment_observation(
    observation_id: uuid.UUID,
    req: ManualApprovalRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
    service: PaymentPlatformService = Depends(get_payment_platform_service),
) -> PaymentObservationResponse:
    try:
        observation = await service.approve_manual_observation(
            session,
            tenant_id=principal.tenant_id,
            observation_id=observation_id,
            actor_user_id=principal.user_id,
            approved_amount=req.approved_amount,
            approved_currency=req.approved_currency,
        )
        await _audit(
            session,
            principal,
            action="PAYMENT_OBSERVATION_APPROVED",
            resource_type="payment_observation",
            resource_id=str(observation.id),
            details={"payment_intent_id": str(observation.payment_intent_id)},
        )
        await session.flush()
        return PaymentObservationResponse.from_model(observation)
    except (PaymentError, PaymentIntegrityError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/observations/{observation_id}/reject", response_model=PaymentObservationResponse)
async def reject_payment_observation(
    observation_id: uuid.UUID,
    req: RejectObservationRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
    service: PaymentPlatformService = Depends(get_payment_platform_service),
) -> PaymentObservationResponse:
    try:
        observation = await service.reject_observation(
            session,
            tenant_id=principal.tenant_id,
            observation_id=observation_id,
            actor_user_id=principal.user_id,
            reason=req.reason,
        )
        await _audit(
            session,
            principal,
            action="PAYMENT_OBSERVATION_REJECTED",
            resource_type="payment_observation",
            resource_id=str(observation.id),
            details={"reason": req.reason},
        )
        await session.flush()
        return PaymentObservationResponse.from_model(observation)
    except (PaymentError, PaymentIntegrityError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
