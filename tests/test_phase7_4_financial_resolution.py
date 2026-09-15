import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_auth_token_service
from apps.api.main import app
from packages.core.auth import AuthSource, AuthTokenService
from packages.core.database import get_db_session
from packages.payments.models import (
    FinancialResolutionCase,
    FinancialResolutionCaseStatus,
    PaymentIntent,
    PaymentIntentPurpose,
    PaymentReconciliationEvent,
    PaymentReconciliationEventStatus,
    Wallet,
    WalletTopUpReversal,
    WalletTopUpReversalStatus,
)
from packages.payments.resolution import FinancialResolutionService
from packages.payments.state_machine import PaymentIntentStatus
from packages.tenants.models import AuditLog, Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
TEST_JWT_SECRET = "phase7-finance-test-jwt-secret-0123456789abcdef-0123456789abcdef"


async def identity(
    session: AsyncSession,
    tenant: Tenant,
    role: Role,
) -> tuple[User, str]:
    user = User(
        telegram_id=int(uuid.uuid4().int % 2_000_000_000),
        username=f"finance_{role.value.lower()}_{uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    session.add(
        Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            permissions=[],
            is_active=True,
        )
    )
    await session.flush()
    token = AuthTokenService(secret_key=TEST_JWT_SECRET).issue_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        roles=[role],
        source=AuthSource.TEST,
        token_version=user.token_version,
    )
    return user, token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def finance_env(db_session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    token_service = AuthTokenService(secret_key=TEST_JWT_SECRET)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_auth_token_service] = lambda: token_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "session": db_session}
    app.dependency_overrides.clear()


async def create_integrity_case(
    session: AsyncSession,
    tenant: Tenant,
    customer: User,
    *,
    amount: Decimal = Decimal("50.00"),
) -> tuple[Wallet, PaymentIntent, PaymentReconciliationEvent, FinancialResolutionCase]:
    wallet = Wallet(
        tenant_id=tenant.id,
        user_id=customer.id,
        currency="XTR",
        balance=amount,
        is_active=False,
    )
    intent = PaymentIntent(
        tenant_id=tenant.id,
        order_id=None,
        purpose=PaymentIntentPurpose.WALLET_TOPUP,
        user_id=customer.id,
        provider="telegram_stars",
        provider_payment_id=f"charge-{uuid.uuid4().hex[:8]}",
        currency="XTR",
        amount=amount,
        status=PaymentIntentStatus.SUCCEEDED,
        idempotency_key=f"intent-{uuid.uuid4().hex}",
        metadata_json={},
    )
    session.add_all([wallet, intent])
    await session.flush()
    event = PaymentReconciliationEvent(
        tenant_id=tenant.id,
        provider="telegram_stars",
        provider_event_id=intent.provider_payment_id,
        event_type="TELEGRAM_STARS_OUTBOUND_INVOICE",
        payment_intent_id=intent.id,
        status=PaymentReconciliationEventStatus.MANUAL_REVIEW,
        amount=amount,
        currency="XTR",
        classification="OUTBOUND_INTEGRITY_MISMATCH",
        requires_review=True,
        metadata_json={"detail": "Provider receiver identity mismatch."},
    )
    session.add(event)
    await session.flush()
    case = await FinancialResolutionService().ensure_case_for_reconciliation_event(session, event)
    assert case is not None
    await session.flush()
    return wallet, intent, event, case


async def test_financial_cases_are_tenant_scoped_and_customer_forbidden(
    finance_env: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = finance_env["client"]
    session: AsyncSession = finance_env["session"]
    tenant = Tenant(name="Finance", slug=f"finance-{uuid.uuid4().hex[:6]}", is_active=True)
    other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add_all([tenant, other])
    await session.flush()
    _, staff_token = await identity(session, tenant, Role.STAFF)
    customer, customer_token = await identity(session, tenant, Role.CUSTOMER)
    other_customer, _ = await identity(session, other, Role.CUSTOMER)
    await create_integrity_case(session, tenant, customer)
    await create_integrity_case(session, other, other_customer)

    forbidden = await client.get("/api/v1/admin/financial-resolution/cases", headers=auth(customer_token))
    assert forbidden.status_code == 403

    response = await client.get("/api/v1/admin/financial-resolution/cases", headers=auth(staff_token))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["cases"][0]["case_type"] == "OUTBOUND_INTEGRITY_MISMATCH"
    assert body["cases"][0]["wallet_active"] is False
    assert body["cases"][0]["severity"] == "CRITICAL"


async def test_claim_uses_optimistic_version_and_audits(finance_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = finance_env["client"]
    session: AsyncSession = finance_env["session"]
    tenant = Tenant(name="Claim", slug=f"claim-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    staff, staff_token = await identity(session, tenant, Role.STAFF)
    customer, _ = await identity(session, tenant, Role.CUSTOMER)
    _, _, _, case = await create_integrity_case(session, tenant, customer)

    claimed = await client.post(
        f"/api/v1/admin/financial-resolution/cases/{case.id}/claim",
        headers=auth(staff_token),
        json={"expected_version": 1},
    )
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "IN_PROGRESS"
    assert claimed.json()["assigned_to_user_id"] == str(staff.id)
    assert claimed.json()["version"] == 2

    stale = await client.post(
        f"/api/v1/admin/financial-resolution/cases/{case.id}/claim",
        headers=auth(staff_token),
        json={"expected_version": 1},
    )
    assert stale.status_code == 409

    logs = list(
        (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant.id,
                    AuditLog.action == "FINANCIAL_CASE_CLAIMED",
                )
            )
        ).scalars().all()
    )
    assert len(logs) == 1


async def test_owner_can_mark_event_false_positive_and_unfreeze(finance_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = finance_env["client"]
    session: AsyncSession = finance_env["session"]
    tenant = Tenant(name="Owner Resolve", slug=f"owner-resolve-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    _, owner_token = await identity(session, tenant, Role.OWNER)
    customer, _ = await identity(session, tenant, Role.CUSTOMER)
    wallet, _, event, case = await create_integrity_case(session, tenant, customer)

    response = await client.post(
        f"/api/v1/admin/financial-resolution/cases/{case.id}/resolve",
        headers=auth(owner_token),
        json={
            "expected_version": case.version,
            "action": "MARK_FALSE_POSITIVE_AND_UNFREEZE",
            "note": "Verified against Telegram records: this event was a false positive.",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "RESOLVED"
    assert response.json()["resolution_code"] == "FALSE_POSITIVE_WALLET_REACTIVATED"
    await session.refresh(wallet)
    await session.refresh(event)
    assert wallet.is_active is True
    assert event.requires_review is False
    assert event.status == PaymentReconciliationEventStatus.IGNORED


async def test_admin_cannot_use_owner_only_unfreeze(finance_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = finance_env["client"]
    session: AsyncSession = finance_env["session"]
    tenant = Tenant(name="Admin Boundary", slug=f"admin-boundary-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    _, admin_token = await identity(session, tenant, Role.ADMIN)
    customer, _ = await identity(session, tenant, Role.CUSTOMER)
    wallet, _, event, case = await create_integrity_case(session, tenant, customer)

    response = await client.post(
        f"/api/v1/admin/financial-resolution/cases/{case.id}/resolve",
        headers=auth(admin_token),
        json={
            "expected_version": case.version,
            "action": "MARK_FALSE_POSITIVE_AND_UNFREEZE",
            "note": "This note is long enough but the actor is not the tenant owner.",
        },
    )
    assert response.status_code == 403
    await session.refresh(wallet)
    await session.refresh(event)
    assert wallet.is_active is False
    assert event.requires_review is True


async def test_external_reversal_resolution_debits_exactly_once(finance_env: dict[str, Any]) -> None:
    client: httpx.AsyncClient = finance_env["client"]
    session: AsyncSession = finance_env["session"]
    tenant = Tenant(name="External", slug=f"external-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add(tenant)
    await session.flush()
    admin, admin_token = await identity(session, tenant, Role.ADMIN)
    customer, _ = await identity(session, tenant, Role.CUSTOMER)
    wallet = Wallet(
        tenant_id=tenant.id,
        user_id=customer.id,
        currency="XTR",
        balance=Decimal("40.00"),
        is_active=False,
    )
    intent = PaymentIntent(
        tenant_id=tenant.id,
        order_id=None,
        purpose=PaymentIntentPurpose.WALLET_TOPUP,
        user_id=customer.id,
        provider="telegram_stars",
        provider_payment_id="stars-charge-resolution",
        currency="XTR",
        amount=Decimal("40.00"),
        status=PaymentIntentStatus.SUCCEEDED,
        idempotency_key=f"topup-{uuid.uuid4().hex}",
        metadata_json={},
    )
    session.add_all([wallet, intent])
    await session.flush()
    reversal = WalletTopUpReversal(
        tenant_id=tenant.id,
        payment_intent_id=intent.id,
        user_id=customer.id,
        wallet_id=wallet.id,
        provider="telegram_stars",
        original_provider_payment_id=intent.provider_payment_id,
        provider_refund_id=intent.provider_payment_id,
        amount=Decimal("40.00"),
        currency="XTR",
        idempotency_key=f"external-{uuid.uuid4().hex}",
        status=WalletTopUpReversalStatus.MANUAL_REVIEW,
        last_error_code="EXTERNAL_REVERSAL_INSUFFICIENT_FUNDS",
        last_error_detail="Value was previously spent.",
        metadata_json={"origin": "EXTERNAL_PROVIDER"},
    )
    session.add(reversal)
    await session.flush()
    case = await FinancialResolutionService().ensure_case_for_reversal(session, reversal)
    assert case is not None

    response = await client.post(
        f"/api/v1/admin/financial-resolution/cases/{case.id}/resolve",
        headers=auth(admin_token),
        json={
            "expected_version": case.version,
            "action": "RETRY_LOCAL_REVERSAL",
            "note": "Customer restored enough XTR value; apply the external reversal debit.",
        },
    )
    assert response.status_code == 200
    assert response.json()["resolution_code"] == "EXTERNAL_REVERSAL_DEBIT_APPLIED"
    await session.refresh(wallet)
    await session.refresh(reversal)
    assert wallet.balance == Decimal("0.00")
    assert wallet.is_active is True
    assert reversal.status == WalletTopUpReversalStatus.COMPLETED

    repeated = await client.post(
        f"/api/v1/admin/financial-resolution/cases/{case.id}/resolve",
        headers=auth(admin_token),
        json={
            "expected_version": response.json()["version"],
            "action": "RETRY_LOCAL_REVERSAL",
            "note": "Repeated operator request must remain idempotent and not debit twice.",
        },
    )
    assert repeated.status_code == 200
    await session.refresh(wallet)
    assert wallet.balance == Decimal("0.00")

    logs = list(
        (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant.id,
                    AuditLog.user_id == admin.id,
                    AuditLog.action == "FINANCIAL_CASE_RESOLVED",
                )
            )
        ).scalars().all()
    )
    assert len(logs) == 2


async def test_stars_reconciliation_auto_creates_operator_case(db_session: AsyncSession) -> None:
    from packages.payments.stars_reconciliation import TelegramStarsReconciliationService

    tenant = Tenant(name="Auto Case", slug=f"auto-case-{uuid.uuid4().hex[:6]}", is_active=True)
    db_session.add(tenant)
    await db_session.flush()
    missing_intent_id = uuid.uuid4()
    event = await TelegramStarsReconciliationService().process_outbound_transaction(
        db_session,
        tenant.id,
        {
            "id": "unmatched-ghbf-outbound",
            "amount": -15,
            "date": 1_700_000_000,
            "receiver": {
                "type": "user",
                "transaction_type": "invoice_payment",
                "invoice_payload": f"ghbf:wallet-topup:{missing_intent_id}",
            },
        },
    )
    assert event is not None and event.requires_review is True
    case = (
        await db_session.execute(
            select(FinancialResolutionCase).where(
                FinancialResolutionCase.tenant_id == tenant.id,
                FinancialResolutionCase.reconciliation_event_id == event.id,
            )
        )
    ).scalar_one()
    assert case.case_type == "UNMATCHED_GHBF_OUTBOUND"
    assert case.status == FinancialResolutionCaseStatus.OPEN
    assert case.wallet_id is None


async def test_reversal_worker_manual_review_auto_creates_case(db_session_factory) -> None:
    from packages.payments.reversal_worker import WalletTopUpReversalWorker

    async with db_session_factory() as session:
        tenant = Tenant(name="Worker Case", slug=f"worker-case-{uuid.uuid4().hex[:6]}", is_active=True)
        user = User(telegram_id=int(uuid.uuid4().int % 2_000_000_000), is_active=True)
        session.add_all([tenant, user])
        await session.flush()
        wallet = Wallet(
            tenant_id=tenant.id,
            user_id=user.id,
            currency="XTR",
            balance=Decimal("10.00"),
            is_active=True,
        )
        intent = PaymentIntent(
            tenant_id=tenant.id,
            order_id=None,
            purpose=PaymentIntentPurpose.WALLET_TOPUP,
            user_id=user.id,
            provider="telegram_stars",
            provider_payment_id="worker-charge",
            currency="XTR",
            amount=Decimal("10.00"),
            status=PaymentIntentStatus.SUCCEEDED,
            idempotency_key=f"worker-intent-{uuid.uuid4().hex}",
            metadata_json={},
        )
        session.add_all([wallet, intent])
        await session.flush()
        reversal = WalletTopUpReversal(
            tenant_id=tenant.id,
            payment_intent_id=intent.id,
            user_id=user.id,
            wallet_id=wallet.id,
            provider="telegram_stars",
            original_provider_payment_id="worker-charge",
            amount=Decimal("10.00"),
            currency="XTR",
            idempotency_key=f"worker-reversal-{uuid.uuid4().hex}",
            status=WalletTopUpReversalStatus.PROCESSING,
            metadata_json={},
        )
        session.add(reversal)
        await session.commit()
        reversal_id = reversal.id
        tenant_id = tenant.id

    worker = WalletTopUpReversalWorker(session_factory=db_session_factory)
    await worker._mark_manual_review(reversal_id, "INTEGRITY_FAILURE", "Provider evidence is inconsistent.")

    async with db_session_factory() as session:
        case = (
            await session.execute(
                select(FinancialResolutionCase).where(
                    FinancialResolutionCase.tenant_id == tenant_id,
                    FinancialResolutionCase.reversal_id == reversal_id,
                )
            )
        ).scalar_one()
        assert case.case_type == "INTEGRITY_FAILURE"
        assert case.status == FinancialResolutionCaseStatus.OPEN
